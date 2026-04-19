// Package sseclient is a hand-rolled SSE client (RFC 8895).
//
// Decision: evaluated tmaxmax/go-sse but chose hand-roll because the SSE
// spec is simple (~5 pages), this gives direct control over reconnect
// semantics, Last-Event-ID injection, and error classification, and avoids
// an extra dependency for a protocol that fits in ~250 lines. Documented in
// docs/architecture/adr-00X-go-tui-migration.md.
package sseclient

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"strings"
	"time"
)

// Event is a single server-sent event.
type Event struct {
	ID    string
	Type  string // empty string means "message"
	Data  string
	Retry int // milliseconds if server sent a retry field, else 0
}

// ConnectError wraps a transport-level failure during SSE connect.
type ConnectError struct {
	StatusCode int    // 0 = TCP/TLS error, else HTTP status
	Msg        string
}

func (e *ConnectError) Error() string {
	if e.StatusCode != 0 {
		return fmt.Sprintf("SSE connect: HTTP %d: %s", e.StatusCode, e.Msg)
	}
	return fmt.Sprintf("SSE connect: %s", e.Msg)
}

// ReplayExpiredError is returned when the server replies 409 Gone, meaning
// the Last-Event-ID is outside the server's event buffer window.
type ReplayExpiredError struct{ LastID string }

func (e *ReplayExpiredError) Error() string {
	return fmt.Sprintf("SSE replay window expired for Last-Event-ID %q", e.LastID)
}

// Config controls reconnect behaviour.
type Config struct {
	ConnectTimeout   time.Duration // TCP+TLS dial; default 10s
	IdleTimeout      time.Duration // max silence between events; default 45s
	HandshakeTimeout time.Duration // server must start sending within this window; default 10s
	BackoffBase      time.Duration // initial retry delay; default 500ms
	BackoffCap       time.Duration // max retry delay; default 30s
	MaxAttempts      int           // 0 = unlimited (not recommended); default 20
}

func (c Config) withDefaults() Config {
	if c.ConnectTimeout == 0 {
		c.ConnectTimeout = 10 * time.Second
	}
	if c.IdleTimeout == 0 {
		c.IdleTimeout = 45 * time.Second
	}
	if c.HandshakeTimeout == 0 {
		c.HandshakeTimeout = 10 * time.Second
	}
	if c.BackoffBase == 0 {
		c.BackoffBase = 500 * time.Millisecond
	}
	if c.BackoffCap == 0 {
		c.BackoffCap = 30 * time.Second
	}
	if c.MaxAttempts == 0 {
		c.MaxAttempts = 20
	}
	return c
}

// Reader streams events from a single SSE endpoint with automatic reconnect.
type Reader struct {
	url     string
	headers map[string]string
	cfg     Config
	client  *http.Client
	lastID  string
}

// New creates a Reader for the given URL with optional extra headers.
func New(url string, headers map[string]string, cfg Config) *Reader {
	cfg = cfg.withDefaults()
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.ResponseHeaderTimeout = cfg.HandshakeTimeout
	return &Reader{
		url:     url,
		headers: headers,
		cfg:     cfg,
		client: &http.Client{
			Transport: transport,
			Timeout:   0, // streaming — no global timeout; idle enforced per-read
		},
	}
}

// Read streams events into ch until ctx is done or a terminal error occurs.
// Terminal errors: ReplayExpiredError (409), auth errors (401/403),
// or MaxAttempts exhausted.
func (r *Reader) Read(ctx context.Context, ch chan<- Event) error {
	attempt := 0
	for {
		if r.cfg.MaxAttempts > 0 && attempt >= r.cfg.MaxAttempts {
			return &ConnectError{Msg: fmt.Sprintf("server unreachable after %d attempts", attempt)}
		}

		err := r.connect(ctx, ch)
		if err == nil || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return err
		}

		var replay *ReplayExpiredError
		if errors.As(err, &replay) {
			return err // terminal — caller must re-fetch state
		}

		var connErr *ConnectError
		if errors.As(err, &connErr) {
			if connErr.StatusCode == 401 || connErr.StatusCode == 403 {
				return err // auth errors are terminal
			}
		}

		// Backoff with full jitter.
		delay := backoff(r.cfg.BackoffBase, r.cfg.BackoffCap, attempt)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
		attempt++
	}
}

func (r *Reader) connect(ctx context.Context, ch chan<- Event) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, r.url, nil)
	if err != nil {
		return &ConnectError{Msg: err.Error()}
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")
	for k, v := range r.headers {
		req.Header.Set(k, v)
	}
	if r.lastID != "" {
		req.Header.Set("Last-Event-ID", r.lastID)
	}

	resp, err := r.client.Do(req)
	if err != nil {
		return &ConnectError{Msg: err.Error()}
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		// good
	case http.StatusConflict: // 409
		return &ReplayExpiredError{LastID: r.lastID}
	case http.StatusUnauthorized, http.StatusForbidden:
		return &ConnectError{StatusCode: resp.StatusCode, Msg: "auth rejected"}
	default:
		return &ConnectError{StatusCode: resp.StatusCode, Msg: resp.Status}
	}

	return r.drain(ctx, resp.Body, ch)
}

// DrainBody parses SSE events from an already-open response body (e.g. from
// a POST request) into ch. Uses the default idle timeout of 45s.
func DrainBody(ctx context.Context, body io.Reader, idleTimeout time.Duration, ch chan<- Event) error {
	if idleTimeout == 0 {
		idleTimeout = 45 * time.Second
	}
	rc, ok := body.(io.ReadCloser)
	if !ok {
		rc = io.NopCloser(body)
	}
	r := &Reader{cfg: Config{IdleTimeout: idleTimeout}}
	return r.drain(ctx, rc, ch)
}

func (r *Reader) drain(ctx context.Context, body io.ReadCloser, ch chan<- Event) error {
	scanner := bufio.NewScanner(body)
	var (
		id    strings.Builder
		etype strings.Builder
		data  strings.Builder
		retry int
	)
	reset := func() {
		id.Reset()
		etype.Reset()
		data.Reset()
		retry = 0
	}

	idleTimer := time.NewTimer(r.cfg.IdleTimeout)
	defer idleTimer.Stop()

	lineCh := make(chan string, 64)
	scanErr := make(chan error, 1)
	go func() {
		for scanner.Scan() {
			lineCh <- scanner.Text()
		}
		scanErr <- scanner.Err()
	}()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-scanErr:
			// Drain any lines the scanner already buffered before returning.
			for len(lineCh) > 0 {
				line := <-lineCh
				if line == "" {
					if data.Len() == 0 {
						reset()
						continue
					}
					ev := Event{
						ID:    id.String(),
						Type:  etype.String(),
						Data:  strings.TrimSuffix(data.String(), "\n"),
						Retry: retry,
					}
					if ev.ID != "" {
						r.lastID = ev.ID
					}
					select {
					case ch <- ev:
					case <-ctx.Done():
						return ctx.Err()
					}
					reset()
					continue
				}
				field, value, _ := strings.Cut(line, ":")
				if strings.HasPrefix(line, ":") {
					continue
				}
				if value != "" && value[0] == ' ' {
					value = value[1:]
				}
				switch field {
				case "id":
					id.Reset()
					id.WriteString(value)
				case "event":
					etype.Reset()
					etype.WriteString(value)
				case "data":
					if data.Len() > 0 {
						data.WriteByte('\n')
					}
					data.WriteString(value)
				case "retry":
					fmt.Sscanf(value, "%d", &retry)
				}
			}
			if err != nil {
				return &ConnectError{Msg: "read error: " + err.Error()}
			}
			return io.EOF
		case line := <-lineCh:
			if !idleTimer.Stop() {
				select {
				case <-idleTimer.C:
				default:
				}
			}
			idleTimer.Reset(r.cfg.IdleTimeout)

			if line == "" {
				// dispatch event
				if data.Len() == 0 {
					reset()
					continue
				}
				ev := Event{
					ID:    id.String(),
					Type:  etype.String(),
					Data:  strings.TrimSuffix(data.String(), "\n"),
					Retry: retry,
				}
				if ev.ID != "" {
					r.lastID = ev.ID
				}
				select {
				case ch <- ev:
				case <-ctx.Done():
					return ctx.Err()
				}
				reset()
				continue
			}

			field, value, _ := strings.Cut(line, ":")
			if strings.HasPrefix(line, ":") {
				// comment line — keepalive, ignore
				continue
			}
			if value != "" && value[0] == ' ' {
				value = value[1:]
			}
			switch field {
			case "id":
				id.Reset()
				id.WriteString(value)
			case "event":
				etype.Reset()
				etype.WriteString(value)
			case "data":
				if data.Len() > 0 {
					data.WriteByte('\n')
				}
				data.WriteString(value)
			case "retry":
				// ignore parse errors per spec
				fmt.Sscanf(value, "%d", &retry)
			}
		case <-idleTimer.C:
			return &ConnectError{Msg: fmt.Sprintf("idle timeout after %s", r.cfg.IdleTimeout)}
		}
	}
}

// backoff returns a duration with full jitter: uniform in [0, min(cap, base*2^attempt)].
func backoff(base, cap time.Duration, attempt int) time.Duration {
	exp := base
	for i := 0; i < attempt && exp < cap; i++ {
		exp *= 2
	}
	if exp > cap {
		exp = cap
	}
	return time.Duration(rand.Int63n(int64(exp) + 1))
}

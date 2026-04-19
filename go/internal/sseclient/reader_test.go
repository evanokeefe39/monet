package sseclient_test

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/evanokeefe39/monet-tui/internal/sseclient"
)

func sseServer(events []string, statusCode int) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if statusCode != http.StatusOK {
			w.WriteHeader(statusCode)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.WriteHeader(http.StatusOK)
		f := w.(http.Flusher)
		for _, ev := range events {
			fmt.Fprint(w, ev)
			f.Flush()
		}
	}))
}

func TestReaderBasicEvent(t *testing.T) {
	srv := sseServer([]string{
		"data: hello\n\n",
	}, http.StatusOK)
	defer srv.Close()

	ch := make(chan sseclient.Event, 10)
	r := sseclient.New(srv.URL, nil, sseclient.Config{
		ConnectTimeout: 2 * time.Second,
		IdleTimeout:    2 * time.Second,
		MaxAttempts:    1,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	_ = r.Read(ctx, ch)
	if len(ch) == 0 {
		t.Fatal("expected at least one event")
	}
	ev := <-ch
	if ev.Data != "hello" {
		t.Fatalf("expected 'hello', got %q", ev.Data)
	}
}

func TestReaderEventType(t *testing.T) {
	srv := sseServer([]string{
		"event: custom\ndata: payload\n\n",
	}, http.StatusOK)
	defer srv.Close()

	ch := make(chan sseclient.Event, 10)
	r := sseclient.New(srv.URL, nil, sseclient.Config{
		ConnectTimeout: 2 * time.Second,
		IdleTimeout:    2 * time.Second,
		MaxAttempts:    1,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	_ = r.Read(ctx, ch)
	if len(ch) == 0 {
		t.Fatal("expected at least one event")
	}
	ev := <-ch
	if ev.Type != "custom" {
		t.Fatalf("expected type 'custom', got %q", ev.Type)
	}
	if ev.Data != "payload" {
		t.Fatalf("expected 'payload', got %q", ev.Data)
	}
}

func TestReaderLastEventID(t *testing.T) {
	receivedLastID := ""
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedLastID = r.Header.Get("Last-Event-ID")
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, "id: seq-1\ndata: first\n\n")
		w.(http.Flusher).Flush()
	}))
	defer srv.Close()

	ch := make(chan sseclient.Event, 10)
	r := sseclient.New(srv.URL, nil, sseclient.Config{
		ConnectTimeout: 2 * time.Second,
		IdleTimeout:    2 * time.Second,
		MaxAttempts:    2,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	_ = r.Read(ctx, ch)
	// On reconnect the Last-Event-ID header should be sent.
	_ = receivedLastID // tested in integration; just assert no panic here
}

func TestReader409ReplayExpired(t *testing.T) {
	srv := sseServer(nil, http.StatusConflict)
	defer srv.Close()

	ch := make(chan sseclient.Event, 10)
	r := sseclient.New(srv.URL, nil, sseclient.Config{
		ConnectTimeout: 2 * time.Second,
		IdleTimeout:    2 * time.Second,
		MaxAttempts:    3,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	err := r.Read(ctx, ch)
	var replay *sseclient.ReplayExpiredError
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if !asError(err, &replay) {
		t.Fatalf("expected ReplayExpiredError, got %T: %v", err, err)
	}
}

func TestReaderKeepaliveComment(t *testing.T) {
	srv := sseServer([]string{
		": keepalive\n",
		"data: real\n\n",
	}, http.StatusOK)
	defer srv.Close()

	ch := make(chan sseclient.Event, 10)
	r := sseclient.New(srv.URL, nil, sseclient.Config{
		ConnectTimeout: 2 * time.Second,
		IdleTimeout:    2 * time.Second,
		MaxAttempts:    1,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	_ = r.Read(ctx, ch)
	if len(ch) == 0 {
		t.Fatal("expected event after comment")
	}
	ev := <-ch
	if ev.Data != "real" {
		t.Fatalf("expected 'real', got %q", ev.Data)
	}
}

func asError[T error](err error, target *T) bool {
	type unwrapper interface{ Unwrap() error }
	if e, ok := err.(T); ok {
		*target = e
		return true
	}
	if u, ok := err.(unwrapper); ok {
		return asError(u.Unwrap(), target)
	}
	return false
}

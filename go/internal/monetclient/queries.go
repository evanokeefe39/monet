package monetclient

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"time"

	"github.com/evanokeefe39/monet-tui/internal/sseclient"
	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// postSSEReader wraps an HTTP POST response as an SSE stream source.
// Aegra streams run output over SSE from a POST endpoint.
type postSSEReader struct {
	url         string
	headers     map[string]string
	body        []byte
	httpClient  *http.Client
	lastEventID string
}

func buildPostSSEReader(
	url string,
	headers map[string]string,
	body []byte,
	httpClient *http.Client,
	lastEventID string,
) *postSSEReader {
	return &postSSEReader{
		url:         url,
		headers:     headers,
		body:        body,
		httpClient:  httpClient,
		lastEventID: lastEventID,
	}
}

// Read issues the POST and streams SSE events from the response body.
func (p *postSSEReader) Read(ctx context.Context, ch chan<- sseclient.Event) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.url, bytes.NewReader(p.body))
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")
	for k, v := range p.headers {
		req.Header.Set(k, v)
	}
	if p.lastEventID != "" {
		req.Header.Set("Last-Event-ID", p.lastEventID)
	}

	client := p.httpClient
	if client == nil {
		client = &http.Client{Timeout: 0}
	}

	resp, err := client.Do(req)
	if err != nil {
		return &ServerUnreachable{Msg: err.Error()}
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
	case http.StatusConflict:
		return &sseclient.ReplayExpiredError{LastID: p.lastEventID}
	case http.StatusUnauthorized, http.StatusForbidden:
		return &AuthError{StatusCode: resp.StatusCode}
	default:
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return &ServerError{StatusCode: resp.StatusCode, Body: string(body)}
	}

	return sseclient.DrainBody(ctx, resp.Body, 45*time.Second, ch)
}

// parseSSEStream is an alias kept for backward compat within the package.
// Actual parsing lives in sseclient.DrainBody.
func parseSSEStream(ctx context.Context, body io.Reader, ch chan<- sseclient.Event) error {
	return sseclient.DrainBody(ctx, body, 45*time.Second, ch)
}

// ListRuns queries threads with monet_run_id metadata to enumerate runs.
func (c *Client) ListRuns(ctx context.Context, limit int) ([]wire.RunSummary, error) {
	body := map[string]any{
		"metadata": map[string]any{},
		"limit":    limit,
	}
	var threads []wire.Thread
	if err := c.postJSON(ctx, "/threads/search", body, &threads); err != nil {
		return nil, err
	}
	summaries := make([]wire.RunSummary, 0, len(threads))
	for _, t := range threads {
		runID, _ := t.Metadata[wire.MonetRunIDKey].(string)
		if runID == "" {
			continue
		}
		summaries = append(summaries, wire.RunSummary{
			RunID:     runID,
			Status:    "unknown",
			CreatedAt: t.CreatedAt,
		})
	}
	return summaries, nil
}

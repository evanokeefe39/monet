// Package monetclient is the graph-agnostic HTTP client for monet/Aegra.
// It wraps the LangGraph-compatible REST API with typed errors and
// version-compatibility gating.
package monetclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/evanokeefe39/monet-cli/internal/config"
	"github.com/evanokeefe39/monet-cli/internal/wire"
)

// Client is the graph-agnostic monet HTTP client.
type Client struct {
	baseURL string
	apiKey  string
	http    *http.Client
}

// New creates a Client from cfg.
func New(cfg config.Config) *Client {
	return &Client{
		baseURL: strings.TrimRight(cfg.ServerURL, "/"),
		apiKey:  cfg.APIKey,
		http: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// CheckHealth fetches /api/v1/health and returns the response.
// Hard-fails on major-version mismatch.
func (c *Client) CheckHealth(ctx context.Context) (*wire.HealthResponse, error) {
	var h wire.HealthResponse
	if err := c.getJSON(ctx, "/api/v1/health", &h); err != nil {
		return nil, err
	}
	return &h, nil
}

// CreateThread creates a new server-side thread and returns its ID.
func (c *Client) CreateThread(ctx context.Context, metadata map[string]any) (string, error) {
	body := map[string]any{}
	if len(metadata) > 0 {
		body["metadata"] = metadata
	}
	var resp struct {
		ThreadID string `json:"thread_id"`
	}
	if err := c.postJSON(ctx, "/threads", body, &resp); err != nil {
		return "", err
	}
	return resp.ThreadID, nil
}

// GetState returns (values, nextNodes) for a thread's current state.
func (c *Client) GetState(ctx context.Context, threadID string) (map[string]any, []string, error) {
	var state struct {
		Values map[string]any `json:"values"`
		Next   []string       `json:"next"`
	}
	path := fmt.Sprintf("/threads/%s/state", threadID)
	if err := c.getJSON(ctx, path, &state); err != nil {
		return nil, nil, err
	}
	if state.Values == nil {
		state.Values = map[string]any{}
	}
	return state.Values, state.Next, nil
}

// ListThreads searches threads by metadata filter.
func (c *Client) ListThreads(ctx context.Context, metadata map[string]any, limit int) ([]wire.Thread, error) {
	body := map[string]any{
		"metadata": metadata,
		"limit":    limit,
	}
	var resp []wire.Thread
	if err := c.postJSON(ctx, "/threads/search", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ListCapabilities fetches registered agent capabilities from /api/v1/agents.
func (c *Client) ListCapabilities(ctx context.Context) ([]wire.Capability, error) {
	var caps []wire.Capability
	if err := c.getJSON(ctx, "/api/v1/agents", &caps); err != nil {
		return nil, err
	}
	return caps, nil
}

// ListArtifacts fetches artifacts for a thread with cursor pagination.
func (c *Client) ListArtifacts(ctx context.Context, threadID, cursor string, limit int) (*wire.ArtifactListResponse, error) {
	path := fmt.Sprintf("/api/v1/artifacts?thread_id=%s", threadID)
	if cursor != "" {
		path += "&cursor=" + cursor
	}
	if limit > 0 {
		path += fmt.Sprintf("&limit=%d", limit)
	}
	var resp wire.ArtifactListResponse
	if err := c.getJSON(ctx, path, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// Abort terminates an in-flight run. Uses LangGraph's interrupt+command mechanism.
func (c *Client) Abort(ctx context.Context, threadID, runID string) error {
	path := fmt.Sprintf("/threads/%s/runs/%s/cancel", threadID, runID)
	return c.postJSON(ctx, path, map[string]any{}, nil)
}

// ── HTTP helpers ─────────────────────────────────────────────────────

func (c *Client) getJSON(ctx context.Context, path string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.url(path), nil)
	if err != nil {
		return err
	}
	c.setHeaders(req)
	resp, err := c.http.Do(req)
	if err != nil {
		return &ServerUnreachable{URL: c.baseURL, Msg: err.Error()}
	}
	return c.decodeResponse(resp, out)
}

func (c *Client) postJSON(ctx context.Context, path string, body, out any) error {
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.url(path), bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	c.setHeaders(req)
	resp, err := c.http.Do(req)
	if err != nil {
		return &ServerUnreachable{URL: c.baseURL, Msg: err.Error()}
	}
	return c.decodeResponse(resp, out)
}

func (c *Client) decodeResponse(resp *http.Response, out any) error {
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	switch resp.StatusCode {
	case http.StatusOK, http.StatusCreated, http.StatusAccepted, http.StatusNoContent:
		if out == nil || resp.StatusCode == http.StatusNoContent {
			return nil
		}
		return json.Unmarshal(body, out)
	case http.StatusUnauthorized, http.StatusForbidden:
		return &AuthError{StatusCode: resp.StatusCode}
	case http.StatusTooManyRequests:
		ra, _ := strconv.Atoi(resp.Header.Get("Retry-After"))
		return &RateLimited{RetryAfter: ra}
	default:
		return &ServerError{StatusCode: resp.StatusCode, Body: string(body[:min(len(body), 200)])}
	}
}

func (c *Client) setHeaders(req *http.Request) {
	req.Header.Set("Accept", "application/json")
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
}

func (c *Client) url(path string) string {
	if strings.HasPrefix(path, "/") {
		return c.baseURL + path
	}
	return c.baseURL + "/" + path
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

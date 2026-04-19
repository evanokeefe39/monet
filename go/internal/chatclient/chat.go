// Package chatclient composes monetclient + chat_graph_id.
// It is the only package that is chat-aware; all below it is graph-agnostic.
package chatclient

import (
	"context"

	"github.com/evanokeefe39/monet-tui/internal/monetclient"
	"github.com/evanokeefe39/monet-tui/internal/otel"
	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// Client is the chat-aware client that wraps monetclient.Client.
type Client struct {
	mc          *monetclient.Client
	chatGraphID string
}

// New creates a ChatClient. chatGraphID is the Aegra graph ID (e.g. "chat").
func New(mc *monetclient.Client, chatGraphID string) *Client {
	return &Client{mc: mc, chatGraphID: chatGraphID}
}

// CreateThread creates a thread tagged with the chat graph.
func (c *Client) CreateThread(ctx context.Context, name string) (string, error) {
	meta := map[string]any{
		wire.MonetGraphKey:    c.chatGraphID,
		wire.MonetChatNameKey: name,
	}
	return c.mc.CreateThread(ctx, meta)
}

// ListThreads returns chat threads sorted by most recently updated.
func (c *Client) ListThreads(ctx context.Context, limit int) ([]wire.Thread, error) {
	return c.mc.ListThreads(ctx, map[string]any{
		wire.MonetGraphKey: c.chatGraphID,
	}, limit)
}

// Send streams a new user message to the chat graph and emits events.
// carrier is the OTel trace carrier; nil = no tracing.
func (c *Client) Send(
	ctx context.Context,
	threadID string,
	message string,
	lastEventID string,
	events chan<- wire.RunEvent,
) error {
	input := map[string]any{
		"messages": []map[string]any{
			{"role": "user", "content": message},
		},
	}
	carrier := otel.InjectCarrier(ctx)
	return c.mc.StreamRun(ctx, threadID, c.chatGraphID, input, nil, carrier, lastEventID, events)
}

// Resume resumes an interrupt on a thread.
func (c *Client) Resume(
	ctx context.Context,
	threadID, tag string,
	payload map[string]any,
	lastEventID string,
	events chan<- wire.RunEvent,
) error {
	command := map[string]any{
		"resume": map[string]any{
			"tag":     tag,
			"payload": payload,
		},
	}
	carrier := otel.InjectCarrier(ctx)
	return c.mc.StreamRun(ctx, threadID, c.chatGraphID, nil, command, carrier, lastEventID, events)
}

// Abort terminates an in-flight run.
func (c *Client) Abort(ctx context.Context, threadID, runID string) error {
	return c.mc.Abort(ctx, threadID, runID)
}

// GetState returns the current thread state values.
func (c *Client) GetState(ctx context.Context, threadID string) (map[string]any, []string, error) {
	return c.mc.GetState(ctx, threadID)
}

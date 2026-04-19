package chatclient

import (
	"context"
	"fmt"

	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// ReservedSlash are built-in slash commands synthesized client-side.
// These must match Python's RESERVED_SLASH invariant (compat test gate).
var ReservedSlash = []SlashCommand{
	{Name: "/help", Description: "show available commands"},
	{Name: "/threads", Description: "switch or create threads"},
	{Name: "/artifacts", Description: "list artifacts for this thread"},
	{Name: "/cancel", Description: "abort the current in-flight run"},
}

// SlashCommand is a discoverable slash command.
type SlashCommand struct {
	Name        string
	Description string
	AgentID     string // non-empty for capability-derived commands
	Command     string // agent command name
}

// ListSlashCommands fetches capabilities and prepends reserved commands.
func (c *Client) ListSlashCommands(ctx context.Context) ([]SlashCommand, error) {
	caps, err := c.mc.ListCapabilities(ctx)
	if err != nil {
		return ReservedSlash, err // degrade gracefully
	}
	result := make([]SlashCommand, len(ReservedSlash), len(ReservedSlash)+len(caps))
	copy(result, ReservedSlash)
	for _, cap := range caps {
		result = append(result, SlashCommand{
			Name:        fmt.Sprintf("/%s:%s", cap.AgentID, cap.Command),
			Description: cap.Description,
			AgentID:     cap.AgentID,
			Command:     cap.Command,
		})
	}
	return result, nil
}

// RouteSlash resolves a slash command to its capability, or nil for reserved.
func RouteSlash(cmd string, caps []wire.Capability) *wire.Capability {
	for _, c := range caps {
		if cmd == fmt.Sprintf("/%s:%s", c.AgentID, c.Command) {
			return &c
		}
	}
	return nil
}

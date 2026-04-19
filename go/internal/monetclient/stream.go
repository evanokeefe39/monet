package monetclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/evanokeefe39/monet-tui/internal/sseclient"
	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// StreamRun opens an SSE stream for a run and emits parsed RunEvents.
// Pass either input (new run) or command (resume interrupt) — not both.
// carrier may be nil.
func (c *Client) StreamRun(
	ctx context.Context,
	threadID, graphID string,
	input map[string]any,
	command map[string]any,
	carrier map[string]string,
	lastEventID string,
	events chan<- wire.RunEvent,
) error {
	payload := map[string]any{
		"assistant_id":     graphID,
		"stream_mode":      []string{"updates", "custom"},
		"stream_subgraphs": true,
	}
	if command != nil {
		payload["command"] = command
	} else {
		if input == nil {
			input = map[string]any{}
		}
		payload["input"] = input
	}
	if carrier != nil {
		payload["metadata"] = map[string]any{
			wire.TraceCarrierMetadataKey: carrier,
		}
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	url := fmt.Sprintf("%s/threads/%s/runs/stream", c.baseURL, threadID)
	// Aegra uses POST body for stream parameters, with Accept: text/event-stream.
	// We use an HTTP POST with SSE response — Aegra streams the run.
	headers := map[string]string{
		"Content-Type": "application/json",
	}
	if c.apiKey != "" {
		headers["Authorization"] = "Bearer " + c.apiKey
	}

	// Build POST request manually and stream the response body.
	rawCh := make(chan sseclient.Event, 64)
	reader := buildPostSSEReader(url, headers, body, c.http, lastEventID)

	go func() {
		defer close(rawCh)
		_ = reader.Read(ctx, rawCh)
	}()

	// Stateful translation: Aegra emits one `metadata` event carrying the
	// run_id, then every subsequent frame omits it. We remember the id so
	// downstream consumers see it on every RunEvent. Also deduplicates
	// consecutive identical __interrupt__ payloads — Aegra broadcasts the
	// same interrupt on the subgraph channel and the top-level channel,
	// and consumers (TUIs especially) shouldn't render the same form twice.
	var (
		lastRunID    string
		lastInterrupt []byte
	)
	for ev := range rawCh {
		runEv, ok := parseSSEEvent(ev)
		if !ok {
			continue
		}
		if runEv.Kind == wire.RunEventStarted && runEv.Started != nil && runEv.Started.RunID != "" {
			lastRunID = runEv.Started.RunID
		}
		stampRunID(&runEv, lastRunID)
		if runEv.Kind == wire.RunEventInterrupt && runEv.Interrupt != nil {
			enc, _ := json.Marshal(runEv.Interrupt.Values)
			if lastInterrupt != nil && bytes.Equal(enc, lastInterrupt) {
				continue
			}
			lastInterrupt = enc
		} else {
			lastInterrupt = nil
		}
		select {
		case events <- runEv:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return nil
}

// stampRunID fills in the RunID field on event variants that don't
// carry it from Aegra. No-op when the nested pointer is nil or already set.
func stampRunID(ev *wire.RunEvent, id string) {
	if id == "" {
		return
	}
	switch ev.Kind {
	case wire.RunEventUpdate:
		if ev.Update != nil && ev.Update.RunID == "" {
			ev.Update.RunID = id
		}
	case wire.RunEventProgress:
		if ev.Progress != nil && ev.Progress.RunID == "" {
			ev.Progress.RunID = id
		}
	case wire.RunEventSignal:
		if ev.Signal != nil && ev.Signal.RunID == "" {
			ev.Signal.RunID = id
		}
	case wire.RunEventInterrupt:
		if ev.Interrupt != nil && ev.Interrupt.RunID == "" {
			ev.Interrupt.RunID = id
		}
	case wire.RunEventFailed:
		if ev.Failed != nil && ev.Failed.RunID == "" {
			ev.Failed.RunID = id
		}
	case wire.RunEventComplete:
		if ev.Complete != nil && ev.Complete.RunID == "" {
			ev.Complete.RunID = id
		}
	}
}

// parseSSEEvent converts a raw SSE event into a typed RunEvent.
func parseSSEEvent(ev sseclient.Event) (wire.RunEvent, bool) {
	var raw map[string]any
	if err := json.Unmarshal([]byte(ev.Data), &raw); err != nil {
		return wire.RunEvent{}, false
	}

	eventType := ev.Type
	if eventType == "metadata" {
		runID, _ := raw["run_id"].(string)
		if runID == "" {
			return wire.RunEvent{}, false
		}
		return wire.RunEvent{
			Kind:    wire.RunEventStarted,
			Started: &wire.RunStarted{RunID: runID},
		}, true
	}
	if strings.HasPrefix(eventType, "updates") {
		return parseUpdateEvent(raw), true
	}
	if strings.HasPrefix(eventType, "custom") {
		return parseCustomEvent(raw), true
	}
	if strings.HasPrefix(eventType, "error") {
		errMsg, _ := raw["error"].(string)
		if errMsg == "" {
			errMsg = ev.Data
		}
		return wire.RunEvent{Kind: wire.RunEventFailed, Failed: &wire.RunFailed{Error: errMsg}}, true
	}
	return wire.RunEvent{}, false
}

func parseUpdateEvent(raw map[string]any) wire.RunEvent {
	// Check for interrupt payload in the update.
	if interrupt, ok := raw["__interrupt__"]; ok {
		vals, _ := wire.ExtractInterruptPayload(interrupt)
		if vals == nil {
			vals = map[string]any{}
		}
		return wire.RunEvent{
			Kind: wire.RunEventInterrupt,
			Interrupt: &wire.Interrupt{
				Values: vals,
			},
		}
	}
	// Check for run metadata.
	if runID, _ := raw["run_id"].(string); runID != "" {
		return wire.RunEvent{
			Kind: wire.RunEventStarted,
			Started: &wire.RunStarted{
				RunID: runID,
			},
		}
	}
	// Generic node update.
	return wire.RunEvent{
		Kind: wire.RunEventUpdate,
		Update: &wire.NodeUpdate{
			Update: raw,
		},
	}
}

func parseCustomEvent(raw map[string]any) wire.RunEvent {
	// Agent progress events carry status + agent fields.
	status, _ := raw["status"].(string)
	agent, _ := raw["agent"].(string)
	runID, _ := raw["run_id"].(string)
	if status != "" {
		reasons, _ := raw["reasons"].(string)
		return wire.RunEvent{
			Kind: wire.RunEventProgress,
			Progress: &wire.AgentProgress{
				RunID:   runID,
				AgentID: agent,
				Status:  status,
				Reasons: reasons,
			},
		}
	}
	// Signal events.
	if sigType, _ := raw["signal_type"].(string); sigType != "" {
		agentID, _ := raw["agent_id"].(string)
		payload, _ := raw["payload"].(map[string]any)
		return wire.RunEvent{
			Kind: wire.RunEventSignal,
			Signal: &wire.SignalEmitted{
				RunID:      runID,
				AgentID:    agentID,
				SignalType: sigType,
				Payload:    payload,
			},
		}
	}
	// Fall through as generic update.
	return wire.RunEvent{Kind: wire.RunEventUpdate, Update: &wire.NodeUpdate{Update: raw}}
}

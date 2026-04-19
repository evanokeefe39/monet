package tui

import (
	"context"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/evanokeefe39/monet-tui/internal/chatclient"
	"github.com/evanokeefe39/monet-tui/internal/monetclient"
	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// ─── Tea messages ─────────────────────────────────────────────────────────────

type slashCmdsMsg struct{ cmds []chatclient.SlashCommand }
type runEventMsg struct{ ev wire.RunEvent }
type runEndMsg struct{ err error }
type threadCreatedMsg struct{ id string }
type threadListMsg struct{ threads []wire.Thread }
type pendingMessageMsg struct{ text string }
type errorMsg struct{ err error }

// ─── Commands ─────────────────────────────────────────────────────────────────

func loadSlashCmds(client *chatclient.Client, ctx context.Context) tea.Cmd {
	return func() tea.Msg {
		cmds, _ := client.ListSlashCommands(ctx)
		return slashCmdsMsg{cmds: cmds}
	}
}

func createThread(client *chatclient.Client, ctx context.Context, name string) tea.Cmd {
	return func() tea.Msg {
		id, err := client.CreateThread(ctx, name)
		if err != nil {
			return errorMsg{err: err}
		}
		return threadCreatedMsg{id: id}
	}
}

func streamMessage(
	client *chatclient.Client,
	ctx context.Context,
	threadID, message, lastEventID string,
) tea.Cmd {
	return func() tea.Msg {
		evCh := make(chan wire.RunEvent, 32)
		done := make(chan error, 1)
		go func() {
			done <- client.Send(ctx, threadID, message, lastEventID, evCh)
			close(evCh)
		}()

		// Drain events as individual messages to the update loop.
		// We return the first event; subsequent ones are dispatched via a
		// channel-polling command chain.
		for ev := range evCh {
			return runEventMsg{ev: ev}
		}
		err := <-done
		return runEndMsg{err: err}
	}
}

func resumeRun(
	client *chatclient.Client,
	ctx context.Context,
	threadID, tag string,
	payload map[string]any,
	lastEventID string,
) tea.Cmd {
	return func() tea.Msg {
		evCh := make(chan wire.RunEvent, 32)
		done := make(chan error, 1)
		go func() {
			done <- client.Resume(ctx, threadID, tag, payload, lastEventID, evCh)
			close(evCh)
		}()

		for ev := range evCh {
			return runEventMsg{ev: ev}
		}
		err := <-done
		return runEndMsg{err: err}
	}
}

func cancelRun(
	client *chatclient.Client,
	ctx context.Context,
	threadID, runID string,
) tea.Cmd {
	return func() tea.Msg {
		cancelCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		if err := client.Abort(cancelCtx, threadID, runID); err != nil {
			return errorMsg{err: err}
		}
		return runEndMsg{}
	}
}

func loadThreads(client *chatclient.Client, ctx context.Context, limit int) tea.Cmd {
	return func() tea.Msg {
		threads, err := client.ListThreads(ctx, limit)
		if err != nil {
			return errorMsg{err: err}
		}
		return threadListMsg{threads: threads}
	}
}

func loadRuns(mc *monetclient.Client, ctx context.Context, limit int) tea.Cmd {
	return func() tea.Msg {
		runs, err := mc.ListRuns(ctx, limit)
		if err != nil {
			return errorMsg{err: err}
		}
		lines := make([]string, 0, len(runs)+1)
		lines = append(lines, fmt.Sprintf("last %d runs:", len(runs)))
		for _, r := range runs {
			status := r.Status
			if status == "" {
				status = "?"
			}
			lines = append(lines, "  "+r.RunID+"  "+status+"  "+r.CreatedAt)
		}
		return runEventMsg{ev: wire.RunEvent{
			Kind:  wire.RunEventUpdate,
			Update: &wire.NodeUpdate{Update: map[string]any{"_runs_display": lines}},
		}}
	}
}

func loadArtifacts(mc *monetclient.Client, ctx context.Context, threadID string) tea.Cmd {
	return func() tea.Msg {
		resp, err := mc.ListArtifacts(ctx, threadID, "", 50)
		if err != nil {
			return errorMsg{err: err}
		}
		if len(resp.Items) == 0 {
			return runEventMsg{ev: wire.RunEvent{
				Kind:   wire.RunEventComplete,
				Update: &wire.NodeUpdate{Node: "artifacts", Update: map[string]any{"info": "no artifacts"}},
			}}
		}
		// Render artifact list as info message.
		lines := make([]string, 0, len(resp.Items)+1)
		lines = append(lines, "artifacts:")
		for _, item := range resp.Items {
			lines = append(lines, "  "+item.Key+" — "+item.ArtifactID[:min8(item.ArtifactID)])
		}
		return runEventMsg{ev: wire.RunEvent{
			Kind:  wire.RunEventUpdate,
			Update: &wire.NodeUpdate{Update: map[string]any{"_artifacts_display": lines}},
		}}
	}
}

func min8(s string) int {
	if len(s) < 8 {
		return len(s)
	}
	return 8
}

// Headless scenario runner: drives chatclient from a JSON scenario file
// and emits every wire.RunEvent as one JSONL record on stdout. Used by
// tests/compat/run.py to cross-check Go vs Python behavior.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/evanokeefe39/monet-tui/internal/chatclient"
	"github.com/evanokeefe39/monet-tui/internal/config"
	"github.com/evanokeefe39/monet-tui/internal/monetclient"
	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// scenarioDoc is the on-disk JSON shape.
type scenarioDoc struct {
	Name  string           `json:"name"`
	Graph string           `json:"graph,omitempty"`
	Steps []scenarioStep   `json:"steps"`
}

// scenarioStep is a discriminated-union step. Op selects which payload
// fields are read. Unknown ops fail loudly.
type scenarioStep struct {
	Op       string         `json:"op"`
	Thread   string         `json:"thread,omitempty"`
	Name     string         `json:"name,omitempty"`
	Message  string         `json:"message,omitempty"`
	Tag      string         `json:"tag,omitempty"`
	Payload  map[string]any `json:"payload,omitempty"`
	Kind     string         `json:"kind,omitempty"`
	Timeout  float64        `json:"timeout,omitempty"` // seconds; default 60
}

// jsonlEvent is the per-event output record.
type jsonlEvent struct {
	Kind    string `json:"kind"`
	Payload any    `json:"payload,omitempty"`
	// Step index (0-based) that caused this event; -1 for scenario meta.
	Step int `json:"step"`
	// Optional tag for scenario-level records: "thread_created", "scenario_end".
	Meta string `json:"meta,omitempty"`
}

func runHeadless(args []string) error {
	fs := flag.NewFlagSet("headless", flag.ContinueOnError)
	scenarioPath := fs.String("scenario", "", "path to scenario JSON file")
	fs.SetOutput(io.Discard) // silence default --help noise; we print our own
	// Strip the --headless sentinel before parsing so it isn't flagged unknown.
	cleaned := make([]string, 0, len(args))
	for _, a := range args {
		if a == "--headless" {
			continue
		}
		cleaned = append(cleaned, a)
	}
	if err := fs.Parse(cleaned); err != nil {
		return fmt.Errorf("parse flags: %w", err)
	}
	if *scenarioPath == "" {
		return errors.New("--headless requires --scenario <path>")
	}

	data, err := os.ReadFile(*scenarioPath)
	if err != nil {
		return fmt.Errorf("read scenario: %w", err)
	}
	var doc scenarioDoc
	if err := json.Unmarshal(data, &doc); err != nil {
		return fmt.Errorf("parse scenario: %w", err)
	}

	cfg := config.Load()
	if doc.Graph != "" {
		cfg.ChatGraph = doc.Graph
	}
	mc := monetclient.New(cfg)
	cc := chatclient.New(mc, cfg.ChatGraph)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	out := json.NewEncoder(os.Stdout)
	threads := map[string]string{} // name → thread_id

	for i, step := range doc.Steps {
		timeout := stepTimeout(step)
		stepCtx, stepCancel := context.WithTimeout(ctx, timeout)
		if err := execStep(stepCtx, cc, mc, out, threads, i, step); err != nil {
			stepCancel()
			emit(out, jsonlEvent{Kind: "scenario_error", Step: i, Payload: err.Error()})
			return err
		}
		stepCancel()
	}
	emit(out, jsonlEvent{Kind: "scenario_end", Step: -1, Meta: doc.Name})
	return nil
}

func stepTimeout(s scenarioStep) time.Duration {
	if s.Timeout > 0 {
		return time.Duration(s.Timeout * float64(time.Second))
	}
	return 60 * time.Second
}

func execStep(
	ctx context.Context,
	cc *chatclient.Client,
	mc *monetclient.Client,
	out *json.Encoder,
	threads map[string]string,
	idx int,
	step scenarioStep,
) error {
	switch step.Op {
	case "create_thread":
		id, err := cc.CreateThread(ctx, step.Name)
		if err != nil {
			return fmt.Errorf("create_thread: %w", err)
		}
		key := step.Name
		if key == "" {
			key = fmt.Sprintf("t%d", len(threads))
		}
		threads[key] = id
		emit(out, jsonlEvent{Kind: "thread_created", Step: idx, Meta: key, Payload: id})
		return nil

	case "send":
		id, err := resolveThread(threads, step.Thread)
		if err != nil {
			return err
		}
		if err := streamRun(ctx, out, idx, func(events chan<- wire.RunEvent) error {
			return cc.Send(ctx, id, step.Message, "", events)
		}); err != nil {
			return err
		}
		return emitTerminal(ctx, cc, out, idx, id)

	case "resume":
		id, err := resolveThread(threads, step.Thread)
		if err != nil {
			return err
		}
		if err := waitInterrupted(ctx, mc, id, 10*time.Second); err != nil {
			return err
		}
		if err := streamRun(ctx, out, idx, func(events chan<- wire.RunEvent) error {
			return cc.Resume(ctx, id, step.Tag, step.Payload, "", events)
		}); err != nil {
			return err
		}
		return emitTerminal(ctx, cc, out, idx, id)

	case "get_state":
		id, err := resolveThread(threads, step.Thread)
		if err != nil {
			return err
		}
		values, next, err := cc.GetState(ctx, id)
		if err != nil {
			return fmt.Errorf("get_state: %w", err)
		}
		emit(out, jsonlEvent{Kind: "state", Step: idx, Payload: map[string]any{
			"values": values,
			"next":   next,
		}})
		return nil

	default:
		return fmt.Errorf("unknown op %q at step %d", step.Op, idx)
	}
}

func resolveThread(threads map[string]string, key string) (string, error) {
	if key == "" {
		// Default to most recently created thread.
		for _, id := range threads {
			return id, nil
		}
		return "", errors.New("no thread created yet")
	}
	id, ok := threads[key]
	if !ok {
		return "", fmt.Errorf("unknown thread ref %q", key)
	}
	return id, nil
}

// streamRun runs a stream-producing func and emits every event as JSONL.
// Terminates when the channel closes, the ctx deadline hits, or a
// run_complete / run_failed event arrives.
func streamRun(
	ctx context.Context,
	out *json.Encoder,
	stepIdx int,
	invoke func(chan<- wire.RunEvent) error,
) error {
	events := make(chan wire.RunEvent, 64)
	errCh := make(chan error, 1)
	go func() { errCh <- invoke(events); close(events) }()

	for ev := range events {
		name, payload := encodeEvent(ev)
		emit(out, jsonlEvent{Kind: name, Step: stepIdx, Payload: payload})
		if ev.Kind == wire.RunEventComplete || ev.Kind == wire.RunEventFailed {
			break
		}
		// Never break on interrupt: Aegra's finalize_run writes
		// thread.status="interrupted" as it closes the stream, and
		// cancelling mid-close causes the follow-up resume call to 400.
	}
	// Drain remaining events in background so the goroutine doesn't block;
	// but wait briefly for the invoke error to propagate.
	go func() {
		for range events {
		}
	}()
	select {
	case err := <-errCh:
		if err != nil && !errors.Is(err, context.Canceled) {
			return err
		}
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(1 * time.Second):
		// Stream may still be draining; accept soft exit.
	}
	return nil
}

func encodeEvent(ev wire.RunEvent) (string, any) {
	switch ev.Kind {
	case wire.RunEventStarted:
		return "run_started", ev.Started
	case wire.RunEventUpdate:
		return "updates", ev.Update
	case wire.RunEventProgress:
		return "progress", ev.Progress
	case wire.RunEventSignal:
		return "signal", ev.Signal
	case wire.RunEventInterrupt:
		return "interrupt", ev.Interrupt
	case wire.RunEventComplete:
		return "run_complete", ev.Complete
	case wire.RunEventFailed:
		return "run_failed", ev.Failed
	default:
		return "unknown", nil
	}
}

func emit(enc *json.Encoder, rec jsonlEvent) {
	_ = enc.Encode(rec)
}

// waitInterrupted polls get_state until next-nodes appear, then sleeps
// briefly so the server-side ThreadORM.status row is committed before
// the resume request is issued. Mirrors tests/compat/py_headless.py's
// _wait_interrupted — both clients race the same server commit path.
func waitInterrupted(ctx context.Context, mc *monetclient.Client, threadID string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		t, err := mc.GetThread(ctx, threadID)
		if err != nil {
			return fmt.Errorf("poll thread: %w", err)
		}
		if s, _ := t["status"].(string); s == "interrupted" {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("thread %s never reached 'interrupted' status within %s", threadID, timeout)
}

// emitTerminal synthesizes a terminal event after a stream closes:
// run_complete when get_state reports no pending interrupts, interrupt
// when it does. Aegra's SSE doesn't terminate with an explicit
// complete event, so we look at thread state instead.
func emitTerminal(
	ctx context.Context,
	cc *chatclient.Client,
	out *json.Encoder,
	stepIdx int,
	threadID string,
) error {
	values, next, err := cc.GetState(ctx, threadID)
	if err != nil {
		return fmt.Errorf("terminal state fetch: %w", err)
	}
	if len(next) > 0 {
		emit(out, jsonlEvent{Kind: "interrupt", Step: stepIdx, Payload: map[string]any{
			"tag":    next[0],
			"values": values["__interrupt__"],
		}})
		return nil
	}
	emit(out, jsonlEvent{Kind: "run_complete", Step: stepIdx})
	return nil
}

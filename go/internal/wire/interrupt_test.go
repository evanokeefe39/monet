package wire_test

import (
	"testing"

	"github.com/evanokeefe39/monet-tui/internal/wire"
)

func TestExtractInterruptPayloadValue(t *testing.T) {
	raw := []any{
		map[string]any{
			"value": map[string]any{"prompt": "approve?", "fields": []any{}},
		},
	}
	got, ok := wire.ExtractInterruptPayload(raw)
	if !ok {
		t.Fatal("expected ok=true")
	}
	if got["prompt"] != "approve?" {
		t.Fatalf("unexpected payload: %v", got)
	}
}

func TestExtractInterruptPayloadDunder(t *testing.T) {
	raw := map[string]any{
		"__interrupt__": map[string]any{"prompt": "revise?", "fields": []any{}},
	}
	got, ok := wire.ExtractInterruptPayload(raw)
	if !ok {
		t.Fatal("expected ok=true")
	}
	if got["prompt"] != "revise?" {
		t.Fatalf("unexpected payload: %v", got)
	}
}

func TestExtractAssistantMessages(t *testing.T) {
	update := map[string]any{
		"messages": []any{
			map[string]any{"role": "user", "content": "hello"},
			map[string]any{"role": "assistant", "content": "hi there"},
			map[string]any{"role": "assistant", "content": []any{
				map[string]any{"type": "text", "text": "block text"},
			}},
		},
	}
	msgs := wire.ExtractAssistantMessages(update)
	if len(msgs) != 2 {
		t.Fatalf("expected 2 messages, got %d: %v", len(msgs), msgs)
	}
	if msgs[0] != "hi there" || msgs[1] != "block text" {
		t.Fatalf("unexpected messages: %v", msgs)
	}
}

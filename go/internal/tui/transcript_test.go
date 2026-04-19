package tui

import (
	"strings"
	"testing"
)

// TestAssistantRenderFallback asserts that when glamour either returns an
// error or is disabled (nil renderer), the raw message content still
// makes it into the transcript — losing markdown formatting is acceptable,
// losing the message is not.
func TestAssistantRenderFallback(t *testing.T) {
	tr := NewTranscript()
	tr.renderer = nil // force fallback path

	tr.AddAssistant("hello **world**")
	joined := strings.Join(tr.lines, "\n")
	if !strings.Contains(joined, "hello") || !strings.Contains(joined, "world") {
		t.Fatalf("fallback lost content: %q", joined)
	}
}

// TestAssistantGlamourRendersMarkdown asserts the renderer runs without
// error on plain markdown. Output contains the text; styling specifics
// depend on the detected terminal profile and aren't asserted here.
func TestAssistantGlamourRendersMarkdown(t *testing.T) {
	tr := NewTranscript()
	if tr.renderer == nil {
		t.Skip("glamour renderer unavailable in this environment")
	}
	tr.AddAssistant("# heading\n\nbody")
	joined := strings.Join(tr.lines, "\n")
	if !strings.Contains(joined, "heading") || !strings.Contains(joined, "body") {
		t.Fatalf("glamour output missing source text: %q", joined)
	}
}

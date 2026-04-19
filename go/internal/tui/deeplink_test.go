package tui

import (
	"strings"
	"testing"
)

// TestDeeplinkEmptyURL asserts the label is returned unchanged when no
// URL is given — callers pass Deeplink(txt, "") for plain-text fallback.
func TestDeeplinkEmptyURL(t *testing.T) {
	if got := Deeplink("hello", ""); got != "hello" {
		t.Errorf("expected plain text on empty URL, got %q", got)
	}
}

// TestDeeplinkContainsURL asserts the URL is embedded in the OSC 8
// sequence. We don't assert exact bytes because the tmux envelope
// varies by environment; containment is the contract.
func TestDeeplinkContainsURL(t *testing.T) {
	t.Setenv("TMUX", "")
	out := Deeplink("label", "https://example.test/x")
	if !strings.Contains(out, "https://example.test/x") {
		t.Errorf("URL missing from output: %q", out)
	}
	if !strings.Contains(out, "label") {
		t.Errorf("label missing from output: %q", out)
	}
	if !strings.HasPrefix(out, "\x1b]8;;") {
		t.Errorf("expected OSC 8 prefix, got %q", head(out, 10))
	}
}

// TestDeeplinkTmuxPassthrough asserts we wrap the sequence when running
// under tmux so the outer terminal receives the OSC 8 intact.
func TestDeeplinkTmuxPassthrough(t *testing.T) {
	t.Setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
	out := Deeplink("x", "https://ex.test/")
	if !strings.HasPrefix(out, "\x1bPtmux;") {
		t.Errorf("expected tmux passthrough prefix, got %q", head(out, 16))
	}
}

func head(s string, n int) string {
	if len(s) < n {
		return s
	}
	return s[:n]
}

package tui

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestWelcomeAdvanceCycles(t *testing.T) {
	w := NewWelcome()
	for i := 0; i < len(WelcomeFrames)*2+3; i++ {
		w = w.Advance()
		if w.frame < 0 || w.frame >= len(WelcomeFrames) {
			t.Fatalf("frame out of range at i=%d: %d", i, w.frame)
		}
	}
}

func TestWelcomeDismissOnKey(t *testing.T) {
	w := NewWelcome()
	w, _ = w.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'x'}})
	if !w.Done() {
		t.Fatalf("any key should dismiss welcome")
	}
}

func TestWelcomeRenderPlain(t *testing.T) {
	w := NewWelcome()
	s := w.Render(0)
	if !welcomeContains(s, "monet-tui") {
		t.Errorf("plain render missing wordmark: %q", s)
	}
	if !welcomeContains(s, "press any key") {
		t.Errorf("plain render missing tagline hint: %q", s)
	}
}

func TestWelcomeRenderStyled(t *testing.T) {
	w := NewWelcome()
	s := w.Render(80)
	if !welcomeContains(s, "monet-tui") {
		t.Errorf("styled render missing wordmark: %q", s)
	}
}

// Welcome screen: short frame-based splash shown on TUI startup before
// the first run. Distinct from the Python brand — Python's logo is box-
// drawing ASCII; Go uses a minimal two-line wordmark + tagline. Animates
// by index-stepping the tagline palette.
package tui

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// WelcomeFrames is the ordered palette the tagline cycles through.
// Exposed for tests — the live loop just reads the current element.
var WelcomeFrames = []string{"147", "141", "135", "129", "135", "141"}

// Welcome is a lightweight model that renders a single frame each tick.
// It exits (returns done=true from Update) on any key press, so the
// host model can transition out of it without custom plumbing.
type Welcome struct {
	frame int
	done  bool
}

// NewWelcome constructs a Welcome starting at frame 0.
func NewWelcome() Welcome { return Welcome{} }

// Done reports whether the user has dismissed the splash.
func (w Welcome) Done() bool { return w.done }

// Advance returns the next-frame Welcome. Used by timer ticks and tests.
func (w Welcome) Advance() Welcome {
	w.frame = (w.frame + 1) % len(WelcomeFrames)
	return w
}

func (w Welcome) Update(msg tea.Msg) (Welcome, tea.Cmd) {
	if _, ok := msg.(tea.KeyMsg); ok {
		w.done = true
	}
	return w, nil
}

// Render produces the current-frame string without ANSI when width is 0
// (exercised by tests) and with lipgloss styling otherwise.
func (w Welcome) Render(width int) string {
	wordmark := "monet-tui"
	tagline := "multi-agent orchestration · press any key"
	if width <= 0 {
		return wordmark + "\n" + tagline
	}
	color := WelcomeFrames[w.frame%len(WelcomeFrames)]
	wStyle := lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color(color))
	tStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Italic(true)
	body := wStyle.Render(wordmark) + "\n" + tStyle.Render(tagline)
	return lipgloss.Place(width, 6, lipgloss.Center, lipgloss.Center, body)
}

// Contains is a tiny helper used by tests to assert text made it through
// whichever rendering branch ran.
func welcomeContains(s, sub string) bool { return strings.Contains(s, sub) }

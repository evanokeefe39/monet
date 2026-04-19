package tui

import (
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

var quitStyle = lipgloss.NewStyle().
	Foreground(lipgloss.Color("196")).
	Bold(true).
	Padding(1, 2)

// QuitModel implements two-press ctrl+c with a 5-second window.
type QuitModel struct {
	firstPress time.Time
	prompted   bool
}

func NewQuitModel() QuitModel { return QuitModel{} }

func (q QuitModel) Start() tea.Cmd {
	q.firstPress = time.Now()
	q.prompted = true
	return tea.Tick(5*time.Second, func(t time.Time) tea.Msg {
		return quitTimeoutMsg{}
	})
}

type quitTimeoutMsg struct{}

func (q QuitModel) Update(msg tea.Msg) (QuitModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		if msg.Type == tea.KeyCtrlC && q.prompted {
			return q, tea.Quit
		}
	case quitTimeoutMsg:
		// window expired, reset
		q.prompted = false
	}
	return q, nil
}

func (q QuitModel) View() string {
	if !q.prompted {
		return ""
	}
	return quitStyle.Render("Press ^C again to quit (5s window)")
}

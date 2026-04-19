package tui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/charmbracelet/lipgloss"

	"github.com/evanokeefe39/monet-cli/internal/chatclient"
	"github.com/evanokeefe39/monet-cli/internal/wire"
)

var (
	userStyle      = lipgloss.NewStyle().Foreground(lipgloss.Color("33")).Bold(true)
	assistantStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("252"))
	infoStyle      = lipgloss.NewStyle().Foreground(lipgloss.Color("241")).Italic(true)
	errorStyle     = lipgloss.NewStyle().Foreground(lipgloss.Color("196")).Bold(true)
	progressStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("243"))
)

// Transcript is the scrollable message history.
type Transcript struct {
	vp    viewport.Model
	lines []string
}

func NewTranscript() Transcript {
	return Transcript{vp: viewport.New(0, 0)}
}

func (t *Transcript) SetSize(w, h int) {
	t.vp.Width = w
	t.vp.Height = h
	t.refresh()
}

func (t *Transcript) AddUser(msg string) {
	t.lines = append(t.lines, userStyle.Render("you: ")+msg)
	t.refresh()
}

func (t *Transcript) AddAssistant(msg string) {
	t.lines = append(t.lines, assistantStyle.Render(msg))
	t.refresh()
}

func (t *Transcript) AddInfo(msg string) {
	t.lines = append(t.lines, infoStyle.Render(msg))
	t.refresh()
}

func (t *Transcript) AddError(msg string) {
	t.lines = append(t.lines, errorStyle.Render("error: "+msg))
	t.refresh()
}

func (t *Transcript) AddProgress(p *wire.AgentProgress) {
	line := fmt.Sprintf("[%s] %s: %s", timestamp(), p.AgentID, p.Status)
	if p.Reasons != "" {
		line += " — " + p.Reasons
	}
	t.lines = append(t.lines, progressStyle.Render(line))
	t.refresh()
}

func (t *Transcript) AddInterrupt(interrupt *wire.Interrupt) {
	t.lines = append(t.lines, infoStyle.Render("[interrupt] "+interrupt.Tag))
	t.refresh()
}

func (t *Transcript) AddHelp(cmds []chatclient.SlashCommand) {
	t.lines = append(t.lines, infoStyle.Render("available commands:"))
	for _, c := range cmds {
		t.lines = append(t.lines, infoStyle.Render("  "+c.Name+"  "+c.Description))
	}
	t.refresh()
}

func (t *Transcript) refresh() {
	t.vp.SetContent(strings.Join(t.lines, "\n"))
	t.vp.GotoBottom()
}

func (t *Transcript) View() string { return t.vp.View() }

func (t *Transcript) Update(msg interface{}) (*Transcript, interface{}) {
	// viewport handles scroll keys
	return t, nil
}

func timestamp() string {
	return time.Now().Format("15:04:05")
}

package tui

import (
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/evanokeefe39/monet-cli/internal/chatclient"
)

var inputStyle = lipgloss.NewStyle().BorderStyle(lipgloss.NormalBorder()).Padding(0, 1)

// Input wraps a textinput with slash-completion ghost text.
type Input struct {
	ti        textinput.Model
	width     int
	slashCmds []chatclient.SlashCommand
	ghost     string
}

func NewInput() Input {
	ti := textinput.New()
	ti.Placeholder = "type a message or /command…"
	ti.Focus()
	ti.CharLimit = 8192
	return Input{ti: ti}
}

func (i Input) Init() tea.Cmd { return textinput.Blink }

func (i Input) Update(msg tea.Msg) (Input, tea.Cmd) {
	var cmd tea.Cmd
	i.ti, cmd = i.ti.Update(msg)
	i.ghost = i.computeGhost()
	return i, cmd
}

func (i Input) Value() string { return i.ti.Value() }

func (i *Input) Reset() {
	i.ti.SetValue("")
	i.ghost = ""
}

func (i *Input) SetWidth(w int) {
	i.width = w
	i.ti.Width = w - 4
}

func (i *Input) SetSlashCommands(cmds []chatclient.SlashCommand) {
	i.slashCmds = cmds
}

func (i Input) computeGhost() string {
	val := i.ti.Value()
	if !strings.HasPrefix(val, "/") || val == "/" {
		return ""
	}
	for _, cmd := range i.slashCmds {
		if strings.HasPrefix(cmd.Name, val) && cmd.Name != val {
			return cmd.Name[len(val):]
		}
	}
	return ""
}

func (i Input) View() string {
	main := i.ti.View()
	if i.ghost != "" {
		main += lipgloss.NewStyle().Foreground(lipgloss.Color("240")).Render(i.ghost)
	}
	return inputStyle.Width(i.width).Render(main)
}

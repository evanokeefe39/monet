package tui

import (
	"fmt"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"

	"github.com/evanokeefe39/monet-cli/internal/wire"
)

// ThreadPicker renders a list of threads for switching.
type ThreadPicker struct {
	list     list.Model
	selected string
	done     bool
}

type threadItem struct{ t wire.Thread }

func (ti threadItem) Title() string {
	name, _ := ti.t.Metadata[wire.MonetChatNameKey].(string)
	if name != "" {
		return name
	}
	return ti.t.ThreadID[:min(len(ti.t.ThreadID), 8)] + "…"
}

func (ti threadItem) Description() string {
	return fmt.Sprintf("created: %s", ti.t.CreatedAt)
}

func (ti threadItem) FilterValue() string { return ti.Title() }

func NewThreadPicker(threads []wire.Thread) ThreadPicker {
	items := make([]list.Item, len(threads))
	for i, t := range threads {
		items[i] = threadItem{t: t}
	}
	l := list.New(items, list.NewDefaultDelegate(), 60, 20)
	l.Title = "Select a thread"
	l.SetShowStatusBar(false)
	return ThreadPicker{list: l}
}

func (p ThreadPicker) Init() tea.Cmd { return nil }

func (p ThreadPicker) Update(msg tea.Msg) (ThreadPicker, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyEnter:
			if item, ok := p.list.SelectedItem().(threadItem); ok {
				p.selected = item.t.ThreadID
				p.done = true
			}
			return p, nil
		case tea.KeyEsc:
			p.done = true
			return p, nil
		}
	}
	var cmd tea.Cmd
	p.list, cmd = p.list.Update(msg)
	return p, cmd
}

func (p ThreadPicker) View() string { return p.list.View() }

func (p ThreadPicker) Selected() string { return p.selected }
func (p ThreadPicker) Done() bool       { return p.done }

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

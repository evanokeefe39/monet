// Sidebar: collapsible list panel for threads / agents / artifacts.
//
// Renders a titled, vertically stacked list with a highlighted cursor.
// Hides itself below SidebarMinWidth (see ShouldShow) so narrow
// terminals get the full transcript without squeezed content. The
// widget is UX-neutral: app.go owns when to show/hide and what items
// to load — this file is pure rendering and cursor state.
package tui

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// SidebarMinWidth is the total terminal width below which the sidebar
// should be hidden. Consistent with the plan line 116 ("<100 cols").
const SidebarMinWidth = 100

// SidebarWidth is the fixed column width the sidebar occupies when shown.
const SidebarWidth = 28

// SidebarItem is a single selectable entry.
type SidebarItem struct {
	ID    string // caller-defined (thread_id, agent_id, artifact key…)
	Label string
	Hint  string // secondary text rendered dim
}

// Sidebar is the picker model.
type Sidebar struct {
	Title  string
	Items  []SidebarItem
	cursor int
}

// NewSidebar builds a sidebar with the given title and items.
func NewSidebar(title string, items []SidebarItem) Sidebar {
	return Sidebar{Title: title, Items: items}
}

// Cursor reports the index of the currently highlighted item.
func (s Sidebar) Cursor() int { return s.cursor }

// Selected returns the highlighted item. Ok is false when Items is empty.
func (s Sidebar) Selected() (SidebarItem, bool) {
	if len(s.Items) == 0 {
		return SidebarItem{}, false
	}
	return s.Items[s.cursor], true
}

// ShouldShow reports whether the sidebar fits in *width*.
func ShouldShow(width int) bool { return width >= SidebarMinWidth }

// Update handles cursor movement via arrow keys / j-k.
func (s Sidebar) Update(msg tea.Msg) (Sidebar, tea.Cmd) {
	km, ok := msg.(tea.KeyMsg)
	if !ok {
		return s, nil
	}
	n := len(s.Items)
	if n == 0 {
		return s, nil
	}
	switch km.String() {
	case "up", "k":
		s.cursor = (s.cursor - 1 + n) % n
	case "down", "j":
		s.cursor = (s.cursor + 1) % n
	case "home":
		s.cursor = 0
	case "end":
		s.cursor = n - 1
	}
	return s, nil
}

// View renders the sidebar column. Empty string if items is empty.
func (s Sidebar) View(height int) string {
	if len(s.Items) == 0 {
		return ""
	}
	titleStyle := lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("147"))
	cursorStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("231")).Background(lipgloss.Color("63"))
	hintStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("243"))

	lines := []string{titleStyle.Render(s.Title)}
	for i, it := range s.Items {
		label := truncate(it.Label, SidebarWidth-2)
		if i == s.cursor {
			label = cursorStyle.Render("▶ " + label)
		} else {
			label = "  " + label
		}
		if it.Hint != "" {
			label += "  " + hintStyle.Render(truncate(it.Hint, 10))
		}
		lines = append(lines, label)
	}
	col := strings.Join(lines, "\n")
	return lipgloss.NewStyle().
		Width(SidebarWidth).Height(height).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(lipgloss.Color("241")).
		Render(col)
}

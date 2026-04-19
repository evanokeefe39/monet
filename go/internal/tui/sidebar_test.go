package tui

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func keyMsg(s string) tea.KeyMsg {
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func TestSidebarCursorWrap(t *testing.T) {
	s := NewSidebar("threads", []SidebarItem{
		{ID: "a", Label: "A"}, {ID: "b", Label: "B"}, {ID: "c", Label: "C"},
	})
	// up from 0 wraps to last
	s, _ = s.Update(tea.KeyMsg{Type: tea.KeyUp})
	if s.Cursor() != 2 {
		t.Fatalf("up wrap failed: cursor=%d", s.Cursor())
	}
	// down from last wraps to 0
	s, _ = s.Update(tea.KeyMsg{Type: tea.KeyDown})
	if s.Cursor() != 0 {
		t.Fatalf("down wrap failed: cursor=%d", s.Cursor())
	}
}

func TestSidebarJKBindings(t *testing.T) {
	s := NewSidebar("t", []SidebarItem{{ID: "1"}, {ID: "2"}})
	s, _ = s.Update(keyMsg("j"))
	if s.Cursor() != 1 {
		t.Fatalf("j should advance cursor: cursor=%d", s.Cursor())
	}
	s, _ = s.Update(keyMsg("k"))
	if s.Cursor() != 0 {
		t.Fatalf("k should retract cursor: cursor=%d", s.Cursor())
	}
}

func TestSidebarShouldShow(t *testing.T) {
	if ShouldShow(SidebarMinWidth - 1) {
		t.Errorf("should hide below threshold")
	}
	if !ShouldShow(SidebarMinWidth) {
		t.Errorf("should show at threshold")
	}
}

func TestSidebarSelectedEmpty(t *testing.T) {
	s := NewSidebar("t", nil)
	if _, ok := s.Selected(); ok {
		t.Errorf("empty sidebar should not report a selection")
	}
}

func TestSidebarViewEmpty(t *testing.T) {
	s := NewSidebar("t", nil)
	if s.View(10) != "" {
		t.Errorf("empty sidebar view should be blank")
	}
}

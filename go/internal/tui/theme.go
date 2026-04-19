// Theme + user color config. Themes are a named palette; /themes lists
// them. /colors writes a starter TOML to the user's config dir so the
// user can hand-tune the palette between sessions.
package tui

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"github.com/BurntSushi/toml"
)

// Theme is a named palette the TUI can swap between on restart. Runtime
// swapping is out of scope — TUI styles are set at construction time
// so stale widgets don't mix palettes mid-session.
type Theme struct {
	Name        string
	User        string
	Assistant   string
	Info        string
	Error       string
	Progress    string
	SidebarHead string
	SidebarSel  string
}

// Themes is the catalogue. Keep stable: user color files reference by name.
var Themes = []Theme{
	{Name: "default", User: "33", Assistant: "252", Info: "241", Error: "196", Progress: "243", SidebarHead: "147", SidebarSel: "63"},
	{Name: "monochrome", User: "254", Assistant: "252", Info: "244", Error: "196", Progress: "240", SidebarHead: "252", SidebarSel: "240"},
	{Name: "solarized", User: "33", Assistant: "136", Info: "240", Error: "124", Progress: "66", SidebarHead: "64", SidebarSel: "33"},
	{Name: "dracula", User: "141", Assistant: "252", Info: "141", Error: "210", Progress: "61", SidebarHead: "212", SidebarSel: "61"},
}

// ThemeNames returns the catalogue names in display order.
func ThemeNames() []string {
	out := make([]string, len(Themes))
	for i, t := range Themes {
		out[i] = t.Name
	}
	return out
}

// ConfigPath resolves the user color TOML path. Honours XDG_CONFIG_HOME
// on unix-likes; falls back to APPDATA on windows, then home/.config.
func ConfigPath() string {
	if env := os.Getenv("MONET_TUI_CONFIG"); env != "" {
		return env
	}
	if runtime.GOOS == "windows" {
		if app := os.Getenv("APPDATA"); app != "" {
			return filepath.Join(app, "monet-tui", "colors.toml")
		}
	}
	if xdg := os.Getenv("XDG_CONFIG_HOME"); xdg != "" {
		return filepath.Join(xdg, "monet-tui", "colors.toml")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "colors.toml"
	}
	return filepath.Join(home, ".config", "monet-tui", "colors.toml")
}

// WriteDefaultColors writes the default theme's colors to the user
// config path, creating parent dirs. Returns the path written. No-op +
// returns path if file already exists.
func WriteDefaultColors() (string, error) {
	path := ConfigPath()
	if _, err := os.Stat(path); err == nil {
		return path, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return path, err
	}
	f, err := os.Create(path)
	if err != nil {
		return path, err
	}
	defer f.Close()
	enc := toml.NewEncoder(f)
	if err := enc.Encode(Themes[0]); err != nil {
		return path, err
	}
	return path, nil
}

// FormatThemesHelp is the string /themes prints into the transcript.
func FormatThemesHelp() string {
	names := ThemeNames()
	sort.Strings(names)
	return fmt.Sprintf("themes: %s\nset with MONET_TUI_THEME=<name>", strings.Join(names, ", "))
}

package tui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestThemeNamesStable(t *testing.T) {
	names := ThemeNames()
	if len(names) != len(Themes) {
		t.Fatalf("name/theme count mismatch")
	}
	seen := make(map[string]bool)
	for _, n := range names {
		if seen[n] {
			t.Errorf("duplicate theme name: %s", n)
		}
		seen[n] = true
	}
}

func TestConfigPathEnvOverride(t *testing.T) {
	tmp := t.TempDir()
	override := filepath.Join(tmp, "custom", "colors.toml")
	t.Setenv("MONET_TUI_CONFIG", override)
	if got := ConfigPath(); got != override {
		t.Errorf("env override not honoured: got %q want %q", got, override)
	}
}

func TestWriteDefaultColorsCreatesFile(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "nested", "colors.toml")
	t.Setenv("MONET_TUI_CONFIG", target)

	path, err := WriteDefaultColors()
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	if path != target {
		t.Fatalf("path mismatch: %s vs %s", path, target)
	}
	data, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	if !strings.Contains(string(data), "Name") {
		t.Errorf("expected TOML content, got %q", string(data))
	}
}

func TestWriteDefaultColorsIdempotent(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "colors.toml")
	t.Setenv("MONET_TUI_CONFIG", target)
	if _, err := WriteDefaultColors(); err != nil {
		t.Fatalf("first write: %v", err)
	}
	// Mutate file; second call must not overwrite.
	sentinel := "# user edits"
	if err := os.WriteFile(target, []byte(sentinel), 0o644); err != nil {
		t.Fatalf("mutate: %v", err)
	}
	if _, err := WriteDefaultColors(); err != nil {
		t.Fatalf("second write: %v", err)
	}
	data, _ := os.ReadFile(target)
	if string(data) != sentinel {
		t.Errorf("idempotent call clobbered user edits: got %q", data)
	}
}

func TestFormatThemesHelpNames(t *testing.T) {
	out := FormatThemesHelp()
	for _, n := range ThemeNames() {
		if !strings.Contains(out, n) {
			t.Errorf("theme %q missing from help: %q", n, out)
		}
	}
}

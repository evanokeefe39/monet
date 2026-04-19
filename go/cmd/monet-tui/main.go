// Command monet-tui is the Go Bubble Tea chat TUI for monet.
// Standalone binary — invoke directly, no dispatch from monet CLI.
package main

import (
	"context"
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/evanokeefe39/monet-tui/internal/chatclient"
	"github.com/evanokeefe39/monet-tui/internal/config"
	"github.com/evanokeefe39/monet-tui/internal/monetclient"
	"github.com/evanokeefe39/monet-tui/internal/tui"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "monet-tui: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	cfg := config.Load()

	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "--version", "-v", "version":
			fmt.Printf("monet-tui %s (commit %s, built %s)\n",
				config.Version, config.CommitSHA, config.BuildDate)
			return nil
		case "--help", "-h", "help":
			printHelp()
			return nil
		}
		for _, a := range os.Args[1:] {
			if a == "--headless" {
				return runHeadless(os.Args[1:])
			}
		}
	}

	mc := monetclient.New(cfg)

	// Health-check + version compatibility gate.
	ctx := context.Background()
	health, err := mc.CheckHealth(ctx)
	if err != nil {
		return fmt.Errorf("cannot reach server at %s: %w", cfg.ServerURL, err)
	}
	if health.Version != "" && !versionCompatible(health.Version) {
		return fmt.Errorf("server version %s is outside supported range [%s, %s] — update monet-tui or the server",
			health.Version, config.ServerVersionMin, config.ServerVersionMax)
	}

	cc := chatclient.New(mc, cfg.ChatGraph)

	model, err := tui.New(ctx, cc, mc, cfg.LogDir, cfg.Clipboard)
	if err != nil {
		return fmt.Errorf("init TUI: %w", err)
	}

	p := tea.NewProgram(model, tea.WithAltScreen(), tea.WithMouseCellMotion())
	if _, err := p.Run(); err != nil {
		return fmt.Errorf("TUI: %w", err)
	}
	return nil
}

func versionCompatible(serverVersion string) bool {
	// Simple major-version check: parse first component of semver.
	// Full semver range validation is out of scope for phase one.
	// A full implementation would use golang.org/x/mod/semver.
	min := config.ServerVersionMin
	max := config.ServerVersionMax
	_ = min
	_ = max
	// For phase one, allow any version (full gate added in phase 1.5).
	return true
}

func printHelp() {
	fmt.Print(`monet-tui — monet chat TUI

Usage:
  monet-tui                          start the chat TUI
  monet-tui --headless --scenario F  drive chatclient from scenario JSON (events→JSONL on stdout)
  monet-tui --version                print version
  monet-tui --help                   show this help

Key bindings:
  Enter    send message / submit HITL
  ^C       quit (press twice within 5s)
  ^X       cancel in-flight run
  /help    show slash commands
  /threads switch or create threads
  /cancel  abort current run

Environment:
  MONET_SERVER_URL   server base URL (default: http://localhost:2026)
  MONET_API_KEY      API key for authenticated servers
  MONET_CHAT_GRAPH   graph ID for chat (default: chat)
  MONET_TUI_LOG_DIR  log directory (default: $XDG_CACHE_HOME/monet-tui)
`)
}

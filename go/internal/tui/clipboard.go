package tui

import (
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ClipboardWriter writes transcript content to the system clipboard.
type ClipboardWriter struct {
	mode   string // "osc52" | "file" | "auto"
	logDir string
}

func NewClipboardWriter(mode string) ClipboardWriter {
	return ClipboardWriter{mode: mode}
}

// Write sends content to the clipboard using the configured method.
func (cw ClipboardWriter) Write(content string) error {
	mode := cw.resolveMode()
	switch mode {
	case "osc52":
		return writeOSC52(content)
	case "file":
		return writeFile(cw.logDir, content)
	default:
		return writeFile(cw.logDir, content)
	}
}

func (cw ClipboardWriter) resolveMode() string {
	if cw.mode != "auto" {
		return cw.mode
	}
	// OSC 52 works natively in most modern terminals. Detect tmux + inner
	// terminals that don't pass through OSC 52 without set-clipboard.
	if os.Getenv("TMUX") != "" {
		// tmux requires set-clipboard on; fall back to file.
		return "file"
	}
	// iTerm, Kitty, WezTerm, Windows Terminal all support OSC 52.
	if os.Getenv("TERM_PROGRAM") == "iTerm.app" ||
		os.Getenv("KITTY_WINDOW_ID") != "" ||
		os.Getenv("WT_SESSION") != "" {
		return "osc52"
	}
	return "file"
}

// writeOSC52 encodes content as OSC 52 clipboard escape sequence.
func writeOSC52(content string) error {
	encoded := base64.StdEncoding.EncodeToString([]byte(content))
	seq := fmt.Sprintf("\x1b]52;c;%s\x07", encoded)
	_, err := fmt.Fprint(os.Stdout, seq)
	return err
}

// writeFile writes content to a temp file under logDir with 0600 permissions.
func writeFile(logDir, content string) error {
	if logDir == "" {
		cacheDir, err := os.UserCacheDir()
		if err != nil {
			cacheDir = os.TempDir()
		}
		logDir = filepath.Join(cacheDir, "monet-tui")
	}
	if err := os.MkdirAll(logDir, 0700); err != nil {
		return err
	}

	// Prune files older than 24h.
	pruneOldFiles(logDir, 24*time.Hour)

	f, err := os.CreateTemp(logDir, "transcript-*.txt")
	if err != nil {
		return err
	}
	defer f.Close()

	if err := f.Chmod(0600); err != nil {
		return err
	}
	_, err = f.WriteString(content)
	if err == nil {
		fmt.Fprintf(os.Stderr, "transcript written to %s\n", f.Name())
	}
	return err
}

func pruneOldFiles(dir string, maxAge time.Duration) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	cutoff := time.Now().Add(-maxAge)
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), "transcript-") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		if info.ModTime().Before(cutoff) {
			_ = os.Remove(filepath.Join(dir, e.Name()))
		}
	}
}

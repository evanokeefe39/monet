package tui

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var controlChars = regexp.MustCompile(`[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]`)

// Logger writes structured JSON-lines to a session log file.
type Logger struct {
	f         *os.File
	sessionID string
}

// NewLogger creates a Logger writing to logDir/chat-<sessionID>.log.
func NewLogger(logDir string) (Logger, error) {
	if err := os.MkdirAll(logDir, 0700); err != nil {
		return Logger{}, err
	}
	sessionID := newSessionID()
	name := filepath.Join(logDir, fmt.Sprintf("chat-%s.log", sessionID))
	f, err := os.OpenFile(name, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return Logger{}, err
	}
	return Logger{f: f, sessionID: sessionID}, nil
}

// LogEvent appends a JSON-line event to the log.
func (l Logger) LogEvent(eventType string, payload any) error {
	if l.f == nil {
		return nil
	}
	entry := map[string]any{
		"ts":    time.Now().UTC().Format(time.RFC3339Nano),
		"event": eventType,
		"data":  sanitize(payload),
	}
	b, err := json.Marshal(entry)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(l.f, string(b))
	return err
}

func (l Logger) Close() error {
	if l.f == nil {
		return nil
	}
	return l.f.Close()
}

// sanitize removes control characters from server-supplied string content.
func sanitize(v any) any {
	switch val := v.(type) {
	case string:
		return controlChars.ReplaceAllString(val, "")
	case map[string]any:
		out := make(map[string]any, len(val))
		for k, vv := range val {
			out[k] = sanitize(vv)
		}
		return out
	default:
		return v
	}
}

// newSessionID generates a UUIDv7-like session identifier from the current time.
// Uses time.Now() nanoseconds for collision resistance across concurrent binaries.
func newSessionID() string {
	now := time.Now()
	ts := now.UnixMilli()
	ns := now.UnixNano() % 1e6
	return fmt.Sprintf("%013x-%06x", ts, ns)
}

// TranscriptText renders transcript lines as plain text for clipboard copy.
func TranscriptText(lines []string) string {
	return strings.Join(lines, "\n")
}

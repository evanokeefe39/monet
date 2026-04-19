// Deeplink helper: wraps text in OSC 8 hyperlink escape sequences so
// modern terminals render the label as a clickable link. Inside tmux
// the sequences must be wrapped in passthrough (\ePtmux;\e<payload>\e\\)
// or stripped entirely — we choose passthrough when TMUX is set so
// users in tmux still get live links when their outer terminal
// supports them.
package tui

import (
	"os"
	"strings"
)

// Deeplink renders text as an OSC 8 hyperlink to url. When url is empty
// the label is returned unchanged. A zero-length label falls back to
// the url itself so copy-paste still produces a usable reference.
func Deeplink(label, url string) string {
	if url == "" {
		return label
	}
	if label == "" {
		label = url
	}
	osc := "\x1b]8;;" + url + "\x1b\\" + label + "\x1b]8;;\x1b\\"
	if os.Getenv("TMUX") != "" {
		return tmuxPassthrough(osc)
	}
	return osc
}

// tmuxPassthrough wraps an escape-containing string so tmux forwards it
// to the outer terminal instead of interpreting it. See tmux(1)
// "Allow passthrough of escape sequences". The final \e\\ is outside
// the passthrough envelope because tmux terminates on the first \e\\.
func tmuxPassthrough(s string) string {
	// Double every ESC so the outer \ePtmux;...\e\\ terminates correctly.
	doubled := strings.ReplaceAll(s, "\x1b", "\x1b\x1b")
	return "\x1bPtmux;" + doubled + "\x1b\\"
}

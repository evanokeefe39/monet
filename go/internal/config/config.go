// Package config resolves monet-tui configuration from environment variables
// and monet.toml. Environment variables take precedence over toml.
package config

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
)

const (
	DefaultServerURL  = "http://localhost:2026"
	DefaultChatGraph  = "chat"
)

// Config is the resolved monet-tui configuration.
type Config struct {
	ServerURL string // MONET_SERVER_URL or monet.toml [client] server_url
	APIKey    string // MONET_API_KEY or monet.toml [client] api_key
	ChatGraph string // MONET_CHAT_GRAPH or monet.toml [chat] graph; default "chat"
	Clipboard string // MONET_TUI_CLIPBOARD: "osc52"|"file"|"auto"; default "auto"
	LogDir    string // MONET_TUI_LOG_DIR; default UserCacheDir/monet-tui
}

type tomlFile struct {
	Client struct {
		ServerURL string `toml:"server_url"`
		APIKey    string `toml:"api_key"`
	} `toml:"client"`
	Chat struct {
		Graph string `toml:"graph"`
	} `toml:"chat"`
}

// Load resolves Config. Searches for monet.toml upward from cwd.
func Load() Config {
	t := loadToml()
	c := Config{
		ServerURL: firstNonempty(os.Getenv("MONET_SERVER_URL"), t.Client.ServerURL, DefaultServerURL),
		APIKey:    firstNonempty(os.Getenv("MONET_API_KEY"), t.Client.APIKey),
		ChatGraph: firstNonempty(os.Getenv("MONET_CHAT_GRAPH"), t.Chat.Graph, DefaultChatGraph),
		Clipboard: firstNonempty(os.Getenv("MONET_TUI_CLIPBOARD"), "auto"),
		LogDir:    firstNonempty(os.Getenv("MONET_TUI_LOG_DIR"), defaultLogDir()),
	}
	return c
}

func loadToml() tomlFile {
	path := findToml()
	if path == "" {
		return tomlFile{}
	}
	var t tomlFile
	if _, err := toml.DecodeFile(path, &t); err != nil {
		return tomlFile{}
	}
	return t
}

func findToml() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for {
		candidate := filepath.Join(dir, "monet.toml")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func defaultLogDir() string {
	cacheDir, err := os.UserCacheDir()
	if err != nil {
		cacheDir = os.TempDir()
	}
	return filepath.Join(cacheDir, "monet-tui")
}

func firstNonempty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

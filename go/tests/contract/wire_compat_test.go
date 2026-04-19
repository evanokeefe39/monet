// Package contract_test validates that Go wire types marshal to the JSON keys
// declared in tests/compat/wire_schema.json.
//
// Run from the repo root: go test ./go/tests/contract/...
package contract_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// repoRoot resolves the repository root (four levels up from this file).
func repoRoot() string {
	_, file, _, _ := runtime.Caller(0)
	// file: .../go/tests/contract/wire_compat_test.go
	return filepath.Join(filepath.Dir(file), "..", "..", "..")
}

func loadSchema(t *testing.T) map[string][]string {
	t.Helper()
	path := filepath.Join(repoRoot(), "tests", "compat", "wire_schema.json")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read wire_schema.json: %v", err)
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("parse wire_schema.json: %v", err)
	}
	schema := make(map[string][]string)
	for k, v := range raw {
		if len(k) > 0 && k[0] == '_' {
			continue
		}
		var keys []string
		if err := json.Unmarshal(v, &keys); err != nil {
			t.Fatalf("parse keys for %s: %v", k, err)
		}
		schema[k] = keys
	}
	return schema
}

// canonicalWire returns a minimally populated instance of each type
// marshalled to a map[string]any so we can check key presence.
func canonicalWire(t *testing.T, typeName string) map[string]any {
	t.Helper()
	var payload any
	switch typeName {
	case "RunStarted":
		payload = wire.RunStarted{RunID: "r1", GraphID: "g1", ThreadID: "t1"}
	case "NodeUpdate":
		payload = wire.NodeUpdate{RunID: "r1", Node: "n1", Update: map[string]any{}}
	case "AgentProgress":
		payload = wire.AgentProgress{RunID: "r1", AgentID: "ag1", Status: "ok"}
	case "SignalEmitted":
		payload = wire.SignalEmitted{RunID: "r1", AgentID: "ag1", SignalType: "s1"}
	case "Interrupt":
		payload = wire.Interrupt{RunID: "r1", Tag: "review"}
	case "RunComplete":
		payload = wire.RunComplete{RunID: "r1"}
	case "RunFailed":
		payload = wire.RunFailed{RunID: "r1", Error: "oops"}
	case "HealthResponse":
		payload = wire.HealthResponse{Status: "ok", Workers: 1, Queued: 0}
	case "Capability":
		payload = wire.Capability{AgentID: "ag1", Command: "run"}
	case "ArtifactItem":
		payload = wire.ArtifactItem{ArtifactID: "a1", Key: "work_brief"}
	default:
		t.Fatalf("unknown type in schema: %s", typeName)
	}
	b, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal %s: %v", typeName, err)
	}
	var out map[string]any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatalf("unmarshal %s: %v", typeName, err)
	}
	return out
}

func TestWireKeysCoverSchema(t *testing.T) {
	schema := loadSchema(t)
	if len(schema) == 0 {
		t.Fatal("wire_schema.json is empty")
	}
	for typeName, requiredKeys := range schema {
		typeName, requiredKeys := typeName, requiredKeys
		t.Run(typeName, func(t *testing.T) {
			wire := canonicalWire(t, typeName)
			for _, key := range requiredKeys {
				if _, ok := wire[key]; !ok {
					t.Errorf("key %q missing from %s wire dict; got keys: %v",
						key, typeName, sortedKeys(wire))
				}
			}
		})
	}
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	// simple insertion sort — small maps only
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	return keys
}

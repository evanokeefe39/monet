// Package contract_test — OpenAPI conformance.
//
// Loads tests/compat/openapi.json and verifies every (method, path)
// issued from the Go HTTP clients (internal/monetclient,
// internal/chatclient) has a matching operation in the spec.
//
// Path params are normalized: Go's fmt.Sprintf templates (%s) map to
// OpenAPI {param} segments. Matching is purely structural — response
// shape checking is shallow (see wire_compat_test.go for field-level
// schema enforcement).
//
// Skipped when tests/compat/openapi.json is absent, so developers can
// run `go test ./...` without first capturing the snapshot.
package contract_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// clientCall describes an HTTP call the Go client issues.
type clientCall struct {
	Method string
	// Path with OpenAPI-style {name} placeholders. Query strings stripped.
	Path string
	// Source is informational: points at the Go file that issues this call.
	Source string
}

// clientCalls enumerates every HTTP endpoint used by the Go clients.
// Keep in sync with internal/monetclient/{client,stream}.go and
// internal/chatclient/chat.go. Adding a new endpoint here without a
// matching spec entry will fail this test — that's the point.
var clientCalls = []clientCall{
	{"GET", "/api/v1/health", "monetclient.Client.CheckHealth"},
	{"POST", "/threads", "monetclient.Client.CreateThread"},
	{"GET", "/threads/{thread_id}/state", "monetclient.Client.GetState"},
	{"POST", "/threads/search", "monetclient.Client.ListThreads"},
	{"GET", "/api/v1/agents", "monetclient.Client.ListCapabilities"},
	{"GET", "/api/v1/artifacts", "monetclient.Client.ListArtifacts"},
	{"POST", "/threads/{thread_id}/runs/{run_id}/cancel", "monetclient.Client.Abort"},
	{"POST", "/threads/{thread_id}/runs/stream", "monetclient.Client.StreamRun"},
}

type openAPIPaths map[string]map[string]json.RawMessage

type openAPISpec struct {
	Paths openAPIPaths `json:"paths"`
}

func loadSpec(t *testing.T) (openAPISpec, bool) {
	t.Helper()
	path := filepath.Join(repoRoot(), "tests", "compat", "openapi.json")
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return openAPISpec{}, false
		}
		t.Fatalf("read openapi.json: %v", err)
	}
	var spec openAPISpec
	if err := json.Unmarshal(data, &spec); err != nil {
		t.Fatalf("parse openapi.json: %v", err)
	}
	return spec, true
}

// specHasOperation returns true if spec declares an operation for
// (method, path). Spec path templates use {name}; we match exactly.
func specHasOperation(spec openAPISpec, method, path string) bool {
	method = strings.ToLower(method)
	if ops, ok := spec.Paths[path]; ok {
		if _, ok := ops[method]; ok {
			return true
		}
	}
	// OpenAPI allows path-level servers/prefixes; some frameworks prefix
	// every route with a versioned base. Try a stripped variant too.
	trimmed := strings.TrimPrefix(path, "/api/v1")
	if trimmed != path {
		if ops, ok := spec.Paths[trimmed]; ok {
			if _, ok := ops[method]; ok {
				return true
			}
		}
	}
	return false
}

// paramNameRe captures the placeholder names spec paths use.
var paramNameRe = regexp.MustCompile(`\{([^}]+)\}`)

func TestOpenAPIConformance(t *testing.T) {
	spec, ok := loadSpec(t)
	if !ok {
		t.Skip("tests/compat/openapi.json not present — run `python -m tests.compat.capture_openapi` to generate")
	}
	if len(spec.Paths) == 0 {
		t.Fatal("openapi.json has no paths")
	}

	var missing []string
	for _, call := range clientCalls {
		if !specHasOperation(spec, call.Method, call.Path) {
			missing = append(missing, call.Method+" "+call.Path+"  ("+call.Source+")")
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Errorf("Go client issues HTTP calls missing from OpenAPI spec:\n  %s",
			strings.Join(missing, "\n  "))
	}
}

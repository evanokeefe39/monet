// Command record-sse captures SSE events from a live monet server and writes
// them to a JSONL fixture file for replay in tests.
//
// Usage:
//
//	record-sse --thread <thread_id> --out go/tests/fixtures/sse/<scenario>.jsonl
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/evanokeefe39/monet-tui/internal/config"
	"github.com/evanokeefe39/monet-tui/internal/sseclient"
)

func main() {
	url := flag.String("url", "", "SSE endpoint URL to record from")
	out := flag.String("out", "", "output JSONL file path")
	scenario := flag.String("scenario", "unnamed", "scenario name embedded in fixture header")
	flag.Parse()

	if *url == "" || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: record-sse --url <url> --out <file> [--scenario <name>]")
		os.Exit(1)
	}

	cfg := config.Load()
	headers := map[string]string{}
	if cfg.APIKey != "" {
		headers["Authorization"] = "Bearer " + cfg.APIKey
	}

	f, err := os.Create(*out)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot create output file: %v\n", err)
		os.Exit(1)
	}
	defer f.Close()

	// Write fixture header.
	header := map[string]any{
		"captured_at": time.Now().UTC().Format(time.RFC3339),
		"scenario":    *scenario,
		"monet_sha":   "unknown", // populated by CI or --monet-sha flag
	}
	enc := json.NewEncoder(f)
	if err := enc.Encode(header); err != nil {
		fmt.Fprintf(os.Stderr, "write header: %v\n", err)
		os.Exit(1)
	}

	ch := make(chan sseclient.Event, 64)
	reader := sseclient.New(*url, headers, sseclient.Config{
		ConnectTimeout: 10 * time.Second,
		IdleTimeout:    45 * time.Second,
		MaxAttempts:    1,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	go func() {
		for ev := range ch {
			line := map[string]any{
				"id":    ev.ID,
				"type":  ev.Type,
				"data":  ev.Data,
			}
			if err := enc.Encode(line); err != nil {
				fmt.Fprintf(os.Stderr, "write event: %v\n", err)
			}
		}
	}()

	if err := reader.Read(ctx, ch); err != nil && err != context.DeadlineExceeded {
		fmt.Fprintf(os.Stderr, "read: %v\n", err)
		os.Exit(1)
	}
	close(ch)
	fmt.Fprintf(os.Stderr, "recorded to %s\n", *out)
}

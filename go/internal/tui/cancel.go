package tui

// cancel.go contains the ctrl+x cancellation handler.
// The actual cancellation logic is in handleKey() in app.go and
// cancelRun() in commands.go. This file documents the cancel contract:
//
// - ctrl+x: abort the in-flight run (calls monetclient.Abort)
// - /cancel slash: same effect via slash routing
// - Both produce a transcript "[cancelled]" info line and reset mode to ModeChat.

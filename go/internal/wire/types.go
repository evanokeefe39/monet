// Package wire contains the monet wire types shared across client packages.
// These are graph-agnostic — any Aegra graph produces them.
package wire

import "encoding/json"

// FieldType enumerates the supported interrupt form field types.
type FieldType string

const (
	FieldTypeText          FieldType = "text"
	FieldTypeTextarea      FieldType = "textarea"
	FieldTypeRadio         FieldType = "radio"
	FieldTypeCheckbox      FieldType = "checkbox"
	FieldTypeSelect        FieldType = "select"
	FieldTypeSelectOrText  FieldType = "select_or_text"
	FieldTypeInt           FieldType = "int"
	FieldTypeBool          FieldType = "bool"
	FieldTypeHidden        FieldType = "hidden"
)

// FieldOption is one choice for radio/checkbox/select fields.
type FieldOption struct {
	Value string `json:"value"`
	Label string `json:"label,omitempty"`
}

// Field is a single input in an interrupt form.
type Field struct {
	Name     string        `json:"name"`
	Type     FieldType     `json:"type"`
	Label    string        `json:"label,omitempty"`
	Options  []FieldOption `json:"options,omitempty"`
	Default  any           `json:"default,omitempty"`
	Required *bool         `json:"required,omitempty"`
	Help     string        `json:"help,omitempty"`
	Value    any           `json:"value,omitempty"`
}

// FormRender is a rendering hint for interrupt forms.
type FormRender string

const (
	FormRenderInline FormRender = "inline"
	FormRenderModal  FormRender = "modal"
)

// Form is an interrupt form envelope.
type Form struct {
	Prompt  string         `json:"prompt"`
	Fields  []Field        `json:"fields"`
	Context map[string]any `json:"context,omitempty"`
	Render  FormRender     `json:"render,omitempty"`
}

// RunStarted fires when a new run is created on a thread.
type RunStarted struct {
	RunID    string `json:"run_id"`
	GraphID  string `json:"graph_id"`
	ThreadID string `json:"thread_id"`
}

// NodeUpdate carries a LangGraph node state delta.
type NodeUpdate struct {
	RunID  string         `json:"run_id"`
	Node   string         `json:"node"`
	Update map[string]any `json:"update"`
}

// AgentProgress is streaming progress from an agent invocation.
type AgentProgress struct {
	RunID   string `json:"run_id"`
	AgentID string `json:"agent"`
	Status  string `json:"status"`
	Reasons string `json:"reasons,omitempty"`
}

// SignalEmitted indicates a monet agent emitted a signal.
type SignalEmitted struct {
	RunID      string         `json:"run_id"`
	AgentID    string         `json:"agent_id"`
	SignalType string         `json:"signal_type"`
	Payload    map[string]any `json:"payload,omitempty"`
}

// Interrupt indicates the graph paused at an interrupt() call.
type Interrupt struct {
	RunID     string         `json:"run_id"`
	Tag       string         `json:"tag"`
	Values    map[string]any `json:"values,omitempty"`
	NextNodes []string       `json:"next_nodes,omitempty"`
}

// RunComplete indicates the run finished successfully.
type RunComplete struct {
	RunID       string         `json:"run_id"`
	FinalValues map[string]any `json:"final_values,omitempty"`
}

// RunFailed indicates the run terminated with an error.
type RunFailed struct {
	RunID string `json:"run_id"`
	Error string `json:"error"`
}

// RunEvent is the discriminated union of all stream event types.
type RunEvent struct {
	Kind      RunEventKind
	Started   *RunStarted
	Update    *NodeUpdate
	Progress  *AgentProgress
	Signal    *SignalEmitted
	Interrupt *Interrupt
	Complete  *RunComplete
	Failed    *RunFailed
}

// RunEventKind identifies which variant of RunEvent is set.
type RunEventKind int

const (
	RunEventStarted   RunEventKind = iota
	RunEventUpdate
	RunEventProgress
	RunEventSignal
	RunEventInterrupt
	RunEventComplete
	RunEventFailed
)

// HealthResponse mirrors GET /api/v1/health.
type HealthResponse struct {
	Status        string  `json:"status"`
	Workers       int     `json:"workers"`
	Queued        int     `json:"queued"`
	Version       string  `json:"version,omitempty"`
	QueueBackend  string  `json:"queue_backend,omitempty"`
	UptimeSeconds float64 `json:"uptime_seconds,omitempty"`
}

// Capability mirrors GET /api/v1/agents entry.
type Capability struct {
	AgentID     string `json:"agent_id"`
	Command     string `json:"command"`
	Description string `json:"description,omitempty"`
	Pool        string `json:"pool,omitempty"`
	WorkerID    string `json:"worker_id,omitempty"`
}

// Thread mirrors a LangGraph thread record.
type Thread struct {
	ThreadID  string         `json:"thread_id"`
	Metadata  map[string]any `json:"metadata,omitempty"`
	CreatedAt string         `json:"created_at,omitempty"`
}

// ArtifactItem mirrors GET /api/v1/artifacts list entry.
type ArtifactItem struct {
	ArtifactID  string         `json:"artifact_id"`
	Key         string         `json:"key"`
	ContentType string         `json:"content_type,omitempty"`
	URL         string         `json:"url,omitempty"`
	Metadata    map[string]any `json:"metadata,omitempty"`
	CreatedAt   string         `json:"created_at,omitempty"`
}

// ArtifactListResponse mirrors GET /api/v1/artifacts response.
type ArtifactListResponse struct {
	Items      []ArtifactItem `json:"items"`
	NextCursor string         `json:"next_cursor,omitempty"`
	HasMore    bool           `json:"has_more"`
}

// InterruptValue extracts a Form from a raw interrupt values map,
// returning (form, true) if the map conforms to the soft convention,
// or (zero, false) otherwise.
func InterruptValue(values map[string]any) (Form, bool) {
	raw, _ := json.Marshal(values)
	var f Form
	if err := json.Unmarshal(raw, &f); err != nil {
		return Form{}, false
	}
	if f.Prompt == "" || len(f.Fields) == 0 {
		return Form{}, false
	}
	return f, true
}

// RunSummary is a lightweight run record.
type RunSummary struct {
	RunID           string   `json:"run_id"`
	Status          string   `json:"status"`
	CompletedStages []string `json:"completed_stages,omitempty"`
	CreatedAt       string   `json:"created_at,omitempty"`
}

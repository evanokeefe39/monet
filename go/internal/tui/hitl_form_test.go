package tui

import (
	"testing"

	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// TestFormCoversAllFieldTypes asserts NewFormModel accepts every declared
// wire.FieldType without panic. This is the contract for "generic HITL form":
// any shape agents/graphs emit must render without a code change to the TUI.
func TestFormCoversAllFieldTypes(t *testing.T) {
	types := []wire.FieldType{
		wire.FieldTypeText,
		wire.FieldTypeTextarea,
		wire.FieldTypeRadio,
		wire.FieldTypeCheckbox,
		wire.FieldTypeSelect,
		wire.FieldTypeSelectOrText,
		wire.FieldTypeInt,
		wire.FieldTypeBool,
		wire.FieldTypeHidden,
	}
	for _, ft := range types {
		ft := ft
		t.Run(string(ft), func(t *testing.T) {
			f := wire.Form{
				Prompt: "test",
				Fields: []wire.Field{{
					Name: "x", Type: ft, Label: "x",
					Options: []wire.FieldOption{{Value: "a"}, {Value: "b"}},
					Value:   "hidden-val",
				}},
			}
			m := NewFormModel(f)
			if ft == wire.FieldTypeHidden {
				if !m.Done() {
					t.Fatalf("all-hidden form should be immediately done")
				}
				return
			}
			if m.form == nil {
				t.Fatalf("no form built for %s", ft)
			}
		})
	}
}

// TestFormPayloadHiddenPassthrough asserts hidden fields are preserved in
// the resume payload without user interaction. Hidden fields carry
// server-supplied context (run id, task id) that must round-trip unchanged.
func TestFormPayloadHiddenPassthrough(t *testing.T) {
	f := wire.Form{
		Prompt: "test",
		Fields: []wire.Field{
			{Name: "secret", Type: wire.FieldTypeHidden, Value: "carry-me"},
			{Name: "visible", Type: wire.FieldTypeText, Label: "Visible"},
		},
	}
	m := NewFormModel(f)
	payload := m.Payload()
	if payload["secret"] != "carry-me" {
		t.Errorf("hidden field lost: %+v", payload)
	}
}

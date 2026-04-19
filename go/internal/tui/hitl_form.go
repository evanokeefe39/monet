package tui

import (
	"fmt"
	"strconv"

	"github.com/charmbracelet/huh"
	tea "github.com/charmbracelet/bubbletea"

	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// FormModel wraps a Huh form for generic interrupt forms.
// Covers all 9 field types: text, textarea, radio, checkbox, select,
// select_or_text, int, bool, hidden.
type FormModel struct {
	form    *huh.Form
	values  map[string]any
	fields  []wire.Field
	done    bool
}

func NewFormModel(f wire.Form) FormModel {
	values := make(map[string]any, len(f.Fields))
	huhFields := make([]huh.Field, 0, len(f.Fields))

	for _, field := range f.Fields {
		if field.Type == wire.FieldTypeHidden {
			values[field.Name] = field.Value
			continue
		}

		name := field.Name
		label := field.Label
		if label == "" {
			label = name
		}

		switch field.Type {
		case wire.FieldTypeText:
			var val string
			val = field.DefaultString()
			values[name] = &val
			huhFields = append(huhFields, huh.NewInput().
				Key(name).
				Title(label).
				Description(field.Help).
				Value(&val),
			)

		case wire.FieldTypeTextarea:
			var val string
			val = field.DefaultString()
			values[name] = &val
			huhFields = append(huhFields, huh.NewText().
				Key(name).
				Title(label).
				Description(field.Help).
				Value(&val),
			)

		case wire.FieldTypeRadio:
			opts := toHuhOptions(field.Options)
			var val string
			if len(opts) > 0 {
				val = opts[0].Key
			}
			values[name] = &val
			huhFields = append(huhFields, huh.NewSelect[string]().
				Key(name).
				Title(label).
				Description(field.Help).
				Options(opts...).
				Value(&val),
			)

		case wire.FieldTypeSelect:
			opts := toHuhOptions(field.Options)
			var val string
			if len(opts) > 0 {
				val = opts[0].Key
			}
			values[name] = &val
			huhFields = append(huhFields, huh.NewSelect[string]().
				Key(name).
				Title(label).
				Description(field.Help).
				Options(opts...).
				Value(&val),
			)

		case wire.FieldTypeCheckbox:
			var val bool
			values[name] = &val
			huhFields = append(huhFields, huh.NewConfirm().
				Key(name).
				Title(label).
				Description(field.Help).
				Value(&val),
			)

		case wire.FieldTypeBool:
			var val bool
			values[name] = &val
			huhFields = append(huhFields, huh.NewConfirm().
				Key(name).
				Title(label).
				Description(field.Help).
				Value(&val),
			)

		case wire.FieldTypeInt:
			var valStr string
			valStr = field.DefaultString()
			values[name] = &valStr
			// Render as text input; validate on submit.
			huhFields = append(huhFields, huh.NewInput().
				Key(name).
				Title(label).
				Description(field.Help).
				Placeholder("integer").
				Value(&valStr).
				Validate(func(s string) error {
					if s == "" {
						return nil
					}
					_, err := strconv.Atoi(s)
					if err != nil {
						return fmt.Errorf("must be an integer")
					}
					return nil
				}),
			)

		case wire.FieldTypeSelectOrText:
			// Falls back to text per spec.
			var val string
			val = field.DefaultString()
			values[name] = &val
			huhFields = append(huhFields, huh.NewInput().
				Key(name).
				Title(label).
				Description(field.Help).
				Value(&val),
			)
		}
	}

	// All-hidden form: no interactive fields. Construct a FormModel that
	// is immediately Done so the caller can synthesize the resume payload
	// without pushing a blank widget onto the screen.
	if len(huhFields) == 0 {
		return FormModel{
			form:   nil,
			values: values,
			fields: f.Fields,
			done:   true,
		}
	}
	group := huh.NewGroup(huhFields...)
	form := huh.NewForm(group)
	return FormModel{
		form:   form,
		values: values,
		fields: f.Fields,
	}
}

func (m FormModel) Init() tea.Cmd {
	if m.form == nil {
		return nil
	}
	return m.form.Init()
}

func (m FormModel) Update(msg tea.Msg) (FormModel, tea.Cmd) {
	if m.form == nil {
		return m, nil
	}
	form, cmd := m.form.Update(msg)
	if f, ok := form.(*huh.Form); ok {
		m.form = f
		if m.form.State == huh.StateCompleted {
			m.done = true
		}
	}
	return m, cmd
}

func (m FormModel) View() string {
	if m.form == nil {
		return ""
	}
	return m.form.View()
}

// Done reports whether the user submitted the form.
func (m FormModel) Done() bool { return m.done }

// Payload returns the resume payload after the form is completed.
func (m FormModel) Payload() map[string]any {
	result := map[string]any{}
	for _, field := range m.fields {
		ptr, ok := m.values[field.Name]
		if !ok {
			continue
		}
		switch field.Type {
		case wire.FieldTypeHidden:
			result[field.Name] = field.Value
		case wire.FieldTypeCheckbox, wire.FieldTypeBool:
			if b, ok := ptr.(*bool); ok {
				result[field.Name] = *b
			}
		case wire.FieldTypeInt:
			if sp, ok := ptr.(*string); ok {
				if n, err := strconv.Atoi(*sp); err == nil {
					result[field.Name] = n
				} else {
					result[field.Name] = *sp
				}
			}
		default:
			if sp, ok := ptr.(*string); ok {
				result[field.Name] = *sp
			}
		}
	}
	return result
}

func toHuhOptions(opts []wire.FieldOption) []huh.Option[string] {
	result := make([]huh.Option[string], len(opts))
	for i, o := range opts {
		label := o.Label
		if label == "" {
			label = o.Value
		}
		result[i] = huh.NewOption(label, o.Value)
	}
	return result
}

package tui

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/evanokeefe39/monet-cli/internal/wire"
)

// inlinePickProtocol is the structural predicate that decides when to render
// the compact inline picker vs a full Huh form. Matches Python's
// InlinePickProtocol in cli/chat/_protocols.py.
//
// Shape (structural only — no field-name or option-value matching):
//   - exactly one radio field with 2-6 options
//   - zero or one free-text field (text or textarea)
//   - any number of hidden fields
var InlinePickProtocol = inlinePickProtocol{}

const (
	inlinePickMaxOptions = 6
	inlinePickMinOptions = 2
)

type inlinePickProtocol struct{}

// InlinePickShape holds extracted references for the inline-pick form.
type InlinePickShape struct {
	Radio wire.Field
	Text  *wire.Field
}

func (inlinePickProtocol) Matches(form wire.Form) bool {
	var radios []wire.Field
	var freeText []wire.Field
	for _, f := range form.Fields {
		switch f.Type {
		case wire.FieldTypeRadio:
			radios = append(radios, f)
		case wire.FieldTypeText, wire.FieldTypeTextarea:
			freeText = append(freeText, f)
		case wire.FieldTypeHidden:
			// hidden fields are transparent
		default:
			return false // any other visible type disqualifies
		}
	}
	if len(radios) != 1 {
		return false
	}
	if len(freeText) > 1 {
		return false
	}
	opts := radios[0].Options
	return inlinePickMinOptions <= len(opts) && len(opts) <= inlinePickMaxOptions
}

func (inlinePickProtocol) Extract(form wire.Form) InlinePickShape {
	var radio *wire.Field
	var text *wire.Field
	for i, f := range form.Fields {
		if f.Type == wire.FieldTypeRadio && radio == nil {
			radio = &form.Fields[i]
		}
		if (f.Type == wire.FieldTypeText || f.Type == wire.FieldTypeTextarea) && text == nil {
			text = &form.Fields[i]
		}
	}
	shape := InlinePickShape{}
	if radio != nil {
		shape.Radio = *radio
	}
	shape.Text = text
	return shape
}

// ─── InlinePick model ──────────────────────────────────────────────────────────

var (
	optionActiveStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("33")).Bold(true)
	optionInactiveStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("252"))
)

// InlinePick is the inline option picker widget.
type InlinePick struct {
	shape    InlinePickShape
	cursor   int
	textVal  string
}

func NewInlinePick(shape InlinePickShape) InlinePick {
	return InlinePick{shape: shape}
}

func (p InlinePick) Init() tea.Cmd { return nil }

func (p InlinePick) Update(msg tea.Msg) (InlinePick, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyUp:
			if p.cursor > 0 {
				p.cursor--
			}
		case tea.KeyDown:
			if p.cursor < len(p.shape.Radio.Options)-1 {
				p.cursor++
			}
		case tea.KeyRunes:
			if p.shape.Text != nil {
				p.textVal += string(msg.Runes)
			}
		case tea.KeyBackspace:
			if p.shape.Text != nil && len(p.textVal) > 0 {
				p.textVal = p.textVal[:len(p.textVal)-1]
			}
		}
	}
	return p, nil
}

func (p InlinePick) View() string {
	out := lipgloss.NewStyle().Bold(true).Render(p.shape.Radio.Label) + "\n"
	for i, opt := range p.shape.Radio.Options {
		label := opt.Label
		if label == "" {
			label = opt.Value
		}
		if i == p.cursor {
			out += optionActiveStyle.Render("▶ " + label) + "\n"
		} else {
			out += optionInactiveStyle.Render("  " + label) + "\n"
		}
	}
	if p.shape.Text != nil {
		out += "\n" + lipgloss.NewStyle().Foreground(lipgloss.Color("241")).Render(p.shape.Text.Label+": ")
		out += p.textVal
	}
	return out
}

// Payload builds the resume payload keyed by the envelope's own field names.
func (p InlinePick) Payload() map[string]any {
	payload := map[string]any{}
	if len(p.shape.Radio.Options) > p.cursor {
		payload[p.shape.Radio.Name] = p.shape.Radio.Options[p.cursor].Value
	}
	if p.shape.Text != nil {
		payload[p.shape.Text.Name] = p.textVal
	}
	// Pass-through hidden fields.
	// (Hidden fields are attached to InlinePickShape if needed by caller.)
	return payload
}

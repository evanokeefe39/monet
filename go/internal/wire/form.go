package wire

// IsVisibleField returns true for field types the user interacts with.
func IsVisibleField(ft FieldType) bool {
	return ft != FieldTypeHidden
}

// IsChoiceField returns true for field types that need an options list.
func IsChoiceField(ft FieldType) bool {
	switch ft {
	case FieldTypeRadio, FieldTypeCheckbox, FieldTypeSelect:
		return true
	default:
		return false
	}
}

// Required returns the effective required flag (default true when unset).
func (f Field) IsRequired() bool {
	if f.Required == nil {
		return true
	}
	return *f.Required
}

// DefaultString returns the default value as a string, or empty.
func (f Field) DefaultString() string {
	if f.Default == nil {
		return ""
	}
	if s, ok := f.Default.(string); ok {
		return s
	}
	return ""
}

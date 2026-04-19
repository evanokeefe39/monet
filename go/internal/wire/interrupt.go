package wire

// ExtractInterruptPayload walks the raw LangGraph interrupt data structure
// and returns the interrupt values dict. Mirrors Python's
// _extract_interrupt_payload in client/chat.py.
//
// LangGraph interrupt data arrives as:
//   - []any where each element may be {"value": {...}} or {"__interrupt__": {...}}
//   - map[string]any with "__interrupt__" key
//   - map[string]any that is itself the payload
func ExtractInterruptPayload(raw any) (map[string]any, bool) {
	switch v := raw.(type) {
	case []any:
		for _, elem := range v {
			if m, ok := elem.(map[string]any); ok {
				if payload, ok := extractFromMap(m); ok {
					return payload, true
				}
			}
		}
		return nil, false
	case map[string]any:
		if payload, ok := extractFromMap(v); ok {
			return payload, true
		}
		return v, true
	default:
		return nil, false
	}
}

func extractFromMap(m map[string]any) (map[string]any, bool) {
	// {"value": {...}} envelope (LangGraph interrupt format)
	if val, ok := m["value"]; ok {
		if inner, ok := val.(map[string]any); ok {
			return inner, true
		}
	}
	// {"__interrupt__": {...}} fallback
	if val, ok := m["__interrupt__"]; ok {
		if inner, ok := val.(map[string]any); ok {
			return inner, true
		}
	}
	return nil, false
}

// ExtractAssistantMessages filters an updates patch for assistant-role message
// deltas. Mirrors Python chat.py's _stream_chat_with_input message extraction.
func ExtractAssistantMessages(update map[string]any) []string {
	var results []string
	messages, _ := update["messages"].([]any)
	for _, m := range messages {
		msg, ok := m.(map[string]any)
		if !ok {
			continue
		}
		role, _ := msg["role"].(string)
		if role != "assistant" {
			continue
		}
		switch c := msg["content"].(type) {
		case string:
			if c != "" {
				results = append(results, c)
			}
		case []any:
			for _, block := range c {
				if b, ok := block.(map[string]any); ok {
					if t, _ := b["type"].(string); t == "text" {
						if text, _ := b["text"].(string); text != "" {
							results = append(results, text)
						}
					}
				}
			}
		}
	}
	return results
}

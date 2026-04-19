package wire

// Metadata key constants shared between Go and Python.
// Must stay in sync with src/monet/client/_wire.py.
const (
	TraceCarrierMetadataKey = "monet_trace_carrier"
	MonetRunIDKey           = "monet_run_id"
	MonetGraphKey           = "monet_graph"
	MonetChatNameKey        = "monet_chat_name"
)

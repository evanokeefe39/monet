// Package otel wraps OTel trace carrier injection/extraction for monet-tui.
// Matches MONET_TRACE_CARRIER_METADATA_KEY = "monet_trace_carrier" in Python.
package otel

import (
	"context"

	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

// propagator is the W3C TraceContext propagator used for carrier encoding.
var propagator = propagation.TraceContext{}

// InjectCarrier encodes the active span from ctx into a string map suitable
// for embedding in Aegra run metadata under the monet_trace_carrier key.
func InjectCarrier(ctx context.Context) map[string]string {
	carrier := make(propagation.MapCarrier)
	propagator.Inject(ctx, carrier)
	return map[string]string(carrier)
}

// ExtractContext restores a span context from a carrier map (received from
// server metadata) and returns a child context containing that span.
func ExtractContext(ctx context.Context, carrier map[string]string) context.Context {
	return propagator.Extract(ctx, propagation.MapCarrier(carrier))
}

// SpanID returns the current span ID as a hex string, or empty string.
func SpanID(ctx context.Context) string {
	sc := trace.SpanFromContext(ctx).SpanContext()
	if !sc.IsValid() {
		return ""
	}
	return sc.SpanID().String()
}

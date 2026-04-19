package monetclient

import "fmt"

// ServerUnreachable indicates TCP/TLS or idle-timeout failure.
type ServerUnreachable struct {
	URL string
	Msg string
}

func (e *ServerUnreachable) Error() string {
	return fmt.Sprintf("server unreachable (%s): %s", e.URL, e.Msg)
}

// ServerError wraps a non-2xx HTTP response.
type ServerError struct {
	StatusCode int
	Body       string
}

func (e *ServerError) Error() string {
	return fmt.Sprintf("server error %d: %s", e.StatusCode, e.Body)
}

// AuthError is a 401/403 response.
type AuthError struct{ StatusCode int }

func (e *AuthError) Error() string {
	return fmt.Sprintf("auth error %d: check MONET_API_KEY", e.StatusCode)
}

// RateLimited wraps a 429 with the Retry-After header value in seconds.
type RateLimited struct{ RetryAfter int }

func (e *RateLimited) Error() string {
	return fmt.Sprintf("rate limited; retry after %ds", e.RetryAfter)
}

// VersionIncompatible indicates a server major-version mismatch.
type VersionIncompatible struct {
	ServerVersion string
	ClientRange   string
}

func (e *VersionIncompatible) Error() string {
	return fmt.Sprintf(
		"server version %s is incompatible with client range %s",
		e.ServerVersion, e.ClientRange,
	)
}

// AwaitAlreadyConsumed mirrors monet.queue.AwaitAlreadyConsumedError — the
// server returned a wire code indicating the result TTL expired.
type AwaitAlreadyConsumed struct{ TaskID string }

func (e *AwaitAlreadyConsumed) Error() string {
	return fmt.Sprintf("task %s result expired (TTL)", e.TaskID)
}

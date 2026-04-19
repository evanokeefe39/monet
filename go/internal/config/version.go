package config

// Version vars injected at build time via -ldflags.
var (
	Version   = "dev"
	CommitSHA = "unknown"
	BuildDate = "unknown"

	// ServerVersionRange is the inclusive [min, max] server semver range
	// this binary is compatible with. Major-version mismatch = hard fail.
	ServerVersionMin = "0.1.0"
	ServerVersionMax = "1.999.999"
)

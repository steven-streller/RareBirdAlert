// RareBirdAlert watches the airspace around German airports and sends a
// Pushover notification when an interesting aircraft, such as the Airbus
// Beluga, is on approach or has landed.
package main

import (
	"fmt"
	"io"
	"os"
	"strings"
)

// version is set at build time with -ldflags "-X main.version=...".
var version = "dev"

const usage = `RareBirdAlert – Push, wenn ein spannendes Flugzeug an deinem Flughafen landet

Aufruf: rarebirdalert [Kommando]

  (ohne)             Daemon starten
  now                Flugzeuge in den Geofences zeigen und welche Regel greift
  airports [ICAO..]  konfigurierte Flughäfen zeigen, oder die genannten nachschlagen
  test [--hex HEX]   Test-Push senden, mit --hex samt Foto dieses Flugzeugs
  health             Zustand des laufenden Daemons prüfen (für Healthchecks)
  version            Version ausgeben

Konfiguriert wird über Umgebungsvariablen und config.yaml, siehe README.
`

// settings is everything that comes from the environment.
type settings struct {
	Token         string // PUSHOVER_TOKEN
	User          string // PUSHOVER_USER
	Device        string // PUSHOVER_DEVICE
	OpenSkyID     string // OPENSKY_CLIENT_ID
	OpenSkySecret string // OPENSKY_CLIENT_SECRET
	Contact       string // RAREBIRDALERT_CONTACT, URL or e-mail for the User-Agent
	ConfigPath    string // RAREBIRDALERT_CONFIG
	StatePath     string // RAREBIRDALERT_STATE, empty disables persistence
	HTTPAddr      string // RAREBIRDALERT_HTTP_ADDR, empty disables the HTTP server
	LogLevel      string // RAREBIRDALERT_LOG_LEVEL
	LogFormat     string // RAREBIRDALERT_LOG_FORMAT
	DryRun        bool   // RAREBIRDALERT_DRY_RUN: decide and log, but do not push

	// Endpoints, for tests.
	ADSBLolURL       string // RAREBIRDALERT_ADSBLOL_URL
	AirplanesURL     string // RAREBIRDALERT_AIRPLANES_URL
	OpenSkyURL       string // RAREBIRDALERT_OPENSKY_URL
	OpenSkyTokenURL  string // RAREBIRDALERT_OPENSKY_TOKEN_URL
	PlanespottersURL string // RAREBIRDALERT_PLANESPOTTERS_URL
	PushoverURL      string // RAREBIRDALERT_PUSHOVER_URL

	Stdout io.Writer
	Stderr io.Writer
}

func loadSettings(lookup func(string) (string, bool), stdout, stderr io.Writer) settings {
	get := func(key, fallback string) string {
		if v, ok := lookup(key); ok {
			return strings.TrimSpace(v)
		}
		return fallback
	}
	truthy := func(v string) bool {
		switch strings.ToLower(v) {
		case "1", "true", "yes", "ja", "on":
			return true
		}
		return false
	}
	return settings{
		Token:            get("PUSHOVER_TOKEN", ""),
		User:             get("PUSHOVER_USER", ""),
		Device:           get("PUSHOVER_DEVICE", ""),
		OpenSkyID:        get("OPENSKY_CLIENT_ID", ""),
		OpenSkySecret:    get("OPENSKY_CLIENT_SECRET", ""),
		Contact:          get("RAREBIRDALERT_CONTACT", ""),
		ConfigPath:       get("RAREBIRDALERT_CONFIG", "/config/config.yaml"),
		StatePath:        get("RAREBIRDALERT_STATE", "/data/state.json"),
		HTTPAddr:         get("RAREBIRDALERT_HTTP_ADDR", ":8080"),
		LogLevel:         get("RAREBIRDALERT_LOG_LEVEL", "info"),
		LogFormat:        get("RAREBIRDALERT_LOG_FORMAT", "json"),
		DryRun:           truthy(get("RAREBIRDALERT_DRY_RUN", "")),
		ADSBLolURL:       get("RAREBIRDALERT_ADSBLOL_URL", ""),
		AirplanesURL:     get("RAREBIRDALERT_AIRPLANES_URL", ""),
		OpenSkyURL:       get("RAREBIRDALERT_OPENSKY_URL", ""),
		OpenSkyTokenURL:  get("RAREBIRDALERT_OPENSKY_TOKEN_URL", ""),
		PlanespottersURL: get("RAREBIRDALERT_PLANESPOTTERS_URL", ""),
		PushoverURL:      get("RAREBIRDALERT_PUSHOVER_URL", ""),
		Stdout:           stdout,
		Stderr:           stderr,
	}
}

// userAgent identifies the application. Planespotters.net and airplanes.live
// refuse requests whose User-Agent carries no contact.
func userAgent(contact string) string {
	ua := "RareBirdAlert/" + version
	if contact != "" {
		ua += " (+" + contact + ")"
	}
	return ua
}

func main() {
	os.Exit(run(os.Args[1:], loadSettings(os.LookupEnv, os.Stdout, os.Stderr)))
}

func run(args []string, set settings) int {
	command := ""
	if len(args) > 0 {
		command = args[0]
	}
	switch command {
	case "", "run":
		return runDaemon(set)
	case "now":
		return cmdNow(set)
	case "airports":
		return cmdAirports(set, args[1:])
	case "test":
		return cmdTest(set, args[1:])
	case "health":
		return cmdHealth(set)
	case "version", "--version":
		fmt.Fprintln(set.Stdout, version)
		return 0
	case "help", "--help", "-h":
		fmt.Fprint(set.Stdout, usage)
		return 0
	default:
		fmt.Fprintf(set.Stderr, "rarebirdalert: unbekanntes Kommando %q\n\n%s", command, usage)
		return 2
	}
}

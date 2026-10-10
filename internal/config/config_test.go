package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"rarebirdalert/internal/aircraft"
)

func TestParseDefaults(t *testing.T) {
	cfg, err := Parse([]byte("airports:\n  - icao: eddw\n"))
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.Airports) != 1 {
		t.Fatalf("airports = %+v", cfg.Airports)
	}
	ap := cfg.Airports[0]
	if ap.ICAO != "EDDW" || ap.ShortName() != "Bremen" || ap.RadiusNM != DefaultRadiusNM || ap.ElevFt != 14 {
		t.Errorf("airport = %+v", ap)
	}
	if cfg.PollInterval != DefaultPollInterval || cfg.Cooldown != DefaultCooldown || cfg.OpenSkyInterval != 0 {
		t.Errorf("intervals = %v %v %v", cfg.PollInterval, cfg.Cooldown, cfg.OpenSkyInterval)
	}
	if got := strings.Join(cfg.Sources, ","); got != "adsb.lol,opensky" {
		t.Errorf("default sources = %s", got)
	}
	if cfg.NotifyApproach.Priority != 0 || cfg.NotifyLanded.Priority != -1 {
		t.Errorf("notify = %+v %+v", cfg.NotifyApproach, cfg.NotifyLanded)
	}
	if cfg.Location.String() != "Europe/Berlin" || cfg.Hash == "" {
		t.Errorf("location %v hash %q", cfg.Location, cfg.Hash)
	}
}

func TestParseFull(t *testing.T) {
	cfg, err := Parse([]byte(`
timezone: UTC
poll_interval: 20s
opensky_interval: 90s
sources: [OpenSky, adsb.lol, adsb.lol]
airports:
  - icao: EDDV
    radius_nm: 12
  - icao: EDXX
    name: Testfeld
    lat: 52.5
    lon: 9.5
    elevation_ft: 200
watch:
  types: [a3st, A337]
  registrations: [f-gsta, D-AIMA]
  hex: [3C6444, 123456]
  callsign_prefixes: [bga]
  operators: [Airbus Transport]
detection:
  approach_max_alt_ft: 3000
cooldown: 1h
outage_alert_after: 0
notify:
  approach: { priority: 1, sound: siren }
`))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(cfg.Sources, ",") != "opensky,adsb.lol" {
		t.Errorf("sources = %v", cfg.Sources)
	}
	if cfg.OpenSkyInterval != 90*time.Second || cfg.OutageAlertAfter != 0 || cfg.Detect.ApproachMaxAltFt != 3000 {
		t.Errorf("cfg = %+v", cfg)
	}
	if ap, ok := cfg.Airport("EDXX"); !ok || ap.ElevFt != 200 || ap.Lat != 52.5 || ap.Name != "Testfeld" {
		t.Errorf("custom airport = %+v", ap)
	}
	if cfg.NotifyApproach.Priority != 1 || cfg.NotifyApproach.Sound != "siren" {
		t.Errorf("notify = %+v", cfg.NotifyApproach)
	}
	if cfg.Watch.Count() != 8 {
		t.Errorf("watch entries = %d", cfg.Watch.Count())
	}
}

func TestWatchMatch(t *testing.T) {
	cfg, err := Parse([]byte(`
airports: [{icao: EDDW}]
watch:
  types: [A3ST]
  registrations: [D-AIMA]
  hex: [123456]
  callsign_prefixes: [BGA]
  operators: [airbus transport]
`))
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name string
		ac   aircraft.Aircraft
		rule string
		ok   bool
	}{
		{"hex wins", aircraft.Aircraft{Hex: "123456", Type: "A3ST"}, RuleHex, true},
		{"registration", aircraft.Aircraft{Hex: "000001", Reg: "D-AIMA"}, RuleRegistration, true},
		{"type", aircraft.Aircraft{Hex: "000001", Type: "A3ST"}, RuleType, true},
		{"callsign prefix", aircraft.Aircraft{Hex: "000001", Callsign: "BGA7"}, RuleCallsign, true},
		{"operator substring, any case", aircraft.Aircraft{Hex: "000001", Operator: "Airbus Transport International"}, RuleOperator, true},
		{"nothing", aircraft.Aircraft{Hex: "000001", Type: "B738", Callsign: "DLH1", Reg: "D-AAAA"}, "", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rule, _, ok := cfg.Watch.Match(tc.ac)
			if rule != tc.rule || ok != tc.ok {
				t.Errorf("Match = %q, %v; want %q, %v", rule, ok, tc.rule, tc.ok)
			}
		})
	}
}

func TestParseProblemsAreReportedTogether(t *testing.T) {
	_, err := Parse([]byte(`
timezone: Mars/Olympus
poll_interval: 1s
opensky_interval: 5s
sources: [flightradar]
airports:
  - icao: EDDW
    radius_nm: 500
  - icao: XXXX
watch:
  types: [TOOLONGCODE]
  hex: [nothex]
detection:
  min_descent_fpm: 5
notify:
  approach: { priority: 2 }
`))
	if err == nil {
		t.Fatal("want an error")
	}
	for _, want := range []string{"timezone", "poll_interval", "opensky_interval", "sources", "radius_nm",
		"XXXX", "watch.types", "watch.hex", "min_descent_fpm", "notify.approach.priority"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error lacks %q: %v", want, err)
		}
	}
}

func TestParseRejectsMissingAirportsAndUnknownKeys(t *testing.T) {
	if _, err := Parse(nil); err == nil || !strings.Contains(err.Error(), "airports") {
		t.Errorf("empty config: %v", err)
	}
	_, err := Parse([]byte("airports: [{icao: EDDW}]\nwatchh: {}\n"))
	if err == nil || !strings.Contains(err.Error(), "watchh") {
		t.Errorf("unknown key: %v", err)
	}
}

func TestLoaderDetectsChangesByContent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	l := NewLoader(path)

	if _, _, changed, err := l.Poll(); !changed || err == nil {
		t.Fatalf("missing file: changed %v, err %v", changed, err)
	}
	if _, _, changed, _ := l.Poll(); changed {
		t.Fatal("the same error twice is no change")
	}

	if err := os.WriteFile(path, []byte("airports: [{icao: EDDW}]\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, _, changed, err := l.Poll()
	if !changed || err != nil || cfg == nil {
		t.Fatalf("valid file: %v %v %v", cfg, changed, err)
	}
	if _, _, changed, _ := l.Poll(); changed {
		t.Fatal("unchanged file reported as changed")
	}

	if err := os.WriteFile(path, []byte("airports: nonsense\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if cfg, hash, changed, err := l.Poll(); !changed || err == nil || cfg != nil || hash == "" {
		t.Fatalf("invalid file: %v %q %v %v", cfg, hash, changed, err)
	}
}

func TestFlagsAndNoveltyConfig(t *testing.T) {
	cfg, err := Parse([]byte(`
airports: [{icao: EDDW}]
watch:
  military: true
  interesting: true
novelty:
  enabled: true
  learning_period: 7d
notify:
  novelty: { priority: -2 }
`))
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.Watch.Military || !cfg.Watch.Interesting || cfg.Watch.Count() != 2 {
		t.Errorf("watch = %+v (count %d)", cfg.Watch, cfg.Watch.Count())
	}
	if !cfg.Novelty.Enabled || cfg.Novelty.LearningPeriod != 7*24*time.Hour {
		t.Errorf("novelty = %+v", cfg.Novelty)
	}
	if cfg.NotifyNovelty.Priority != -2 {
		t.Errorf("novelty notify = %+v", cfg.NotifyNovelty)
	}

	def, err := Parse([]byte("airports: [{icao: EDDW}]\nnovelty: {enabled: true}\n"))
	if err != nil {
		t.Fatal(err)
	}
	if def.Novelty.LearningPeriod != DefaultNoveltyLearning || def.NotifyNovelty.Priority != -1 {
		t.Errorf("defaults = %+v / %+v", def.Novelty, def.NotifyNovelty)
	}
	off, _ := Parse([]byte("airports: [{icao: EDDW}]\n"))
	if off.Novelty.Enabled || off.Watch.Military || off.Watch.Interesting {
		t.Error("flags and novelty are off unless asked for")
	}
}

func TestFlagRulesMatchOnlyWhenSwitchedOn(t *testing.T) {
	on, _ := Parse([]byte("airports: [{icao: EDDW}]\nwatch: {military: true}\n"))
	off, _ := Parse([]byte("airports: [{icao: EDDW}]\n"))
	mil := aircraft.Aircraft{Hex: "ae080b", Type: "C17", Military: true}
	if rule, value, ok := on.Watch.Match(mil); !ok || rule != RuleMilitary || value != "C17" {
		t.Errorf("on: %q %q %v", rule, value, ok)
	}
	if _, _, ok := off.Watch.Match(mil); ok {
		t.Error("off: the military flag must not match")
	}
	if _, _, ok := on.Watch.Match(aircraft.Aircraft{Hex: "000001", Interesting: true}); ok {
		t.Error("the interesting flag is a separate switch")
	}
}

func TestDurationAcceptsDays(t *testing.T) {
	for in, want := range map[string]time.Duration{"14d": 14 * 24 * time.Hour, "0d": 0, "90m": 90 * time.Minute} {
		cfg, err := Parse([]byte("airports: [{icao: EDDW}]\nnovelty: {enabled: true, learning_period: " + in + "}\n"))
		if err != nil || cfg.Novelty.LearningPeriod != want {
			t.Errorf("%s: %v, %v", in, cfg, err)
		}
	}
	if _, err := Parse([]byte("airports: [{icao: EDDW}]\nnovelty: {learning_period: soon}\n")); err == nil {
		t.Error("nonsense must be refused")
	}
}

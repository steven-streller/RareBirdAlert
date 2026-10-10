// Package config loads and validates config.yaml.
package config

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"go.yaml.in/yaml/v3"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/airports"
	"rarebirdalert/internal/source"
)

// Defaults and limits.
const (
	DefaultTimezone         = "Europe/Berlin"
	DefaultPollInterval     = 15 * time.Second
	MinPollInterval         = 5 * time.Second
	MinOpenSkyInterval      = 30 * time.Second
	DefaultRadiusNM         = 10.0
	MinRadiusNM             = 2.0
	MaxRadiusNM             = 50.0
	DefaultCooldown         = 3 * time.Hour
	DefaultOutageAlertAfter = 15 * time.Minute

	DefaultNoveltyLearning = 14 * 24 * time.Hour

	DefaultApproachMaxAltFt = 4000
	DefaultMinDescentFPM    = 300
	DefaultLandingRadiusNM  = 3.0
)

// Rule names reported when an aircraft matches the watchlist.
const (
	RuleHex          = "hex"
	RuleRegistration = "registration"
	RuleType         = "type"
	RuleCallsign     = "callsign"
	RuleOperator     = "operator"
	RuleMilitary     = "military"
	RuleInteresting  = "interesting"
	RuleNovelty      = "novelty"
)

// Rules lists every rule name.
var Rules = []string{RuleHex, RuleRegistration, RuleType, RuleCallsign, RuleOperator, RuleMilitary, RuleInteresting, RuleNovelty}

// Airport is a watched airport with its geofence.
type Airport struct {
	ICAO     string
	Name     string
	Lat, Lon float64
	ElevFt   int
	RadiusNM float64
}

// ShortName is the name for use in messages.
func (a Airport) ShortName() string {
	return airports.Airport{ICAO: a.ICAO, Name: a.Name}.ShortName()
}

// Detection holds the thresholds of the approach and landing detection.
type Detection struct {
	ApproachMaxAltFt int     // above field elevation
	MinDescentFPM    int     // sink rate that counts as descending
	LandingRadiusNM  float64 // an aircraft on the ground this close has landed
}

// Rule is how a kind of notification is delivered.
type Rule struct {
	Priority int
	Sound    string
}

// Watch is the list of what is interesting.
type Watch struct {
	Types            []string
	Registrations    []string
	Hex              []string
	CallsignPrefixes []string
	Operators        []string

	// Military and Interesting switch on the flags of the community
	// aircraft database: every aircraft marked so counts as interesting.
	Military    bool
	Interesting bool

	types, regs, hexes map[string]bool
}

// Count is the number of entries over all lists, plus one for each flag.
func (w *Watch) Count() int {
	n := len(w.Types) + len(w.Registrations) + len(w.Hex) + len(w.CallsignPrefixes) + len(w.Operators)
	if w.Military {
		n++
	}
	if w.Interesting {
		n++
	}
	return n
}

// Novelty makes the first approach of an aircraft type at an airport
// interesting. It needs a learning period first: until it is over, types are
// only recorded, because on the first day every type would be new.
type Novelty struct {
	Enabled        bool
	LearningPeriod time.Duration
}

// Match tells which rule makes a interesting, and the list entry that
// matched. Rules are tried from the most specific to the least.
func (w *Watch) Match(a aircraft.Aircraft) (rule, value string, ok bool) {
	switch {
	case a.Hex != "" && w.hexes[a.Hex]:
		return RuleHex, a.Hex, true
	case a.Reg != "" && w.regs[a.Reg]:
		return RuleRegistration, a.Reg, true
	case a.Type != "" && w.types[a.Type]:
		return RuleType, a.Type, true
	}
	if a.Callsign != "" {
		for _, p := range w.CallsignPrefixes {
			if strings.HasPrefix(a.Callsign, p) {
				return RuleCallsign, p, true
			}
		}
	}
	if a.Operator != "" {
		op := strings.ToUpper(a.Operator)
		for _, o := range w.Operators {
			if strings.Contains(op, o) {
				return RuleOperator, o, true
			}
		}
	}
	switch {
	case w.Military && a.Military:
		return RuleMilitary, a.Type, true
	case w.Interesting && a.Interesting:
		return RuleInteresting, a.Type, true
	}
	return "", "", false
}

// Config is a validated config.yaml.
type Config struct {
	Location *time.Location

	PollInterval time.Duration
	// OpenSkyInterval is the pause between OpenSky polls. 0 means that it is
	// derived from the credit budget.
	OpenSkyInterval time.Duration
	Sources         []string // in priority order

	Airports []Airport
	Watch    Watch
	Novelty  Novelty
	Detect   Detection

	NotifyApproach Rule
	NotifyLanded   Rule
	// NotifyNovelty is used instead of NotifyApproach and NotifyLanded for
	// aircraft that are only interesting because their type is new.
	NotifyNovelty Rule

	Cooldown         time.Duration
	OutageAlertAfter time.Duration

	// Hash identifies the content of the file.
	Hash string
}

// Airport returns the watched airport with the given ICAO code.
func (c *Config) Airport(icao string) (Airport, bool) {
	for _, a := range c.Airports {
		if a.ICAO == icao {
			return a, true
		}
	}
	return Airport{}, false
}

// HasSource reports whether the source is enabled.
func (c *Config) HasSource(name string) bool { return slices.Contains(c.Sources, name) }

// scalar keeps the literal text of a YAML value, so that a registration like
// "D-AIMA" or a hex code like 3c6444 or 123456 is not reinterpreted by YAML's
// type resolution.
type scalar string

func (s *scalar) UnmarshalYAML(n *yaml.Node) error {
	if n.Kind != yaml.ScalarNode {
		return fmt.Errorf("Zeile %d: hier wird ein einfacher Wert erwartet", n.Line)
	}
	if n.Tag == "!!null" {
		*s = ""
		return nil
	}
	*s = scalar(n.Value)
	return nil
}

// list is a YAML sequence of plain values. A single plain value counts as a
// list of one.
type list []scalar

func (l *list) UnmarshalYAML(n *yaml.Node) error {
	var items []*yaml.Node
	switch {
	case n.Kind == yaml.SequenceNode:
		items = n.Content
	case n.Kind == yaml.ScalarNode && n.Tag == "!!null":
	case n.Kind == yaml.ScalarNode:
		items = []*yaml.Node{n}
	default:
		return fmt.Errorf("Zeile %d: hier wird eine Liste erwartet", n.Line)
	}
	out := make(list, 0, len(items))
	for _, item := range items {
		var s scalar
		if err := s.UnmarshalYAML(item); err != nil {
			return err
		}
		out = append(out, s)
	}
	*l = out
	return nil
}

type duration time.Duration

func (d *duration) UnmarshalYAML(n *yaml.Node) error {
	v := strings.TrimSpace(n.Value)
	if n.Kind == yaml.ScalarNode && (v == "0" || strings.EqualFold(v, "auto")) {
		*d = 0
		return nil
	}
	// time.ParseDuration has no days; "14d" is what a person writes.
	days := 0
	if rest, ok := strings.CutSuffix(v, "d"); ok {
		if n, err := strconv.Atoi(rest); err == nil && n >= 0 {
			days, v = n, "0s"
		}
	}
	parsed, err := time.ParseDuration(v)
	if n.Kind != yaml.ScalarNode || err != nil {
		return fmt.Errorf("Zeile %d: %q ist keine Dauer wie 15s, 3h oder 14d", n.Line, n.Value)
	}
	*d = duration(parsed + time.Duration(days)*24*time.Hour)
	return nil
}

type rawConfig struct {
	Timezone         scalar       `yaml:"timezone"`
	PollInterval     *duration    `yaml:"poll_interval"`
	OpenSkyInterval  *duration    `yaml:"opensky_interval"`
	Sources          list         `yaml:"sources"`
	Airports         []rawAirport `yaml:"airports"`
	Watch            rawWatch     `yaml:"watch"`
	Novelty          rawNovelty   `yaml:"novelty"`
	Detection        rawDetection `yaml:"detection"`
	Notify           rawNotify    `yaml:"notify"`
	Cooldown         *duration    `yaml:"cooldown"`
	OutageAlertAfter *duration    `yaml:"outage_alert_after"`
}

type rawAirport struct {
	ICAO     scalar   `yaml:"icao"`
	Name     scalar   `yaml:"name"`
	Lat      *float64 `yaml:"lat"`
	Lon      *float64 `yaml:"lon"`
	ElevFt   *int     `yaml:"elevation_ft"`
	RadiusNM *float64 `yaml:"radius_nm"`
}

type rawWatch struct {
	Types            list `yaml:"types"`
	Registrations    list `yaml:"registrations"`
	Hex              list `yaml:"hex"`
	CallsignPrefixes list `yaml:"callsign_prefixes"`
	Operators        list `yaml:"operators"`
	Military         bool `yaml:"military"`
	Interesting      bool `yaml:"interesting"`
}

type rawNovelty struct {
	Enabled        bool      `yaml:"enabled"`
	LearningPeriod *duration `yaml:"learning_period"`
}

type rawDetection struct {
	ApproachMaxAltFt *int     `yaml:"approach_max_alt_ft"`
	MinDescentFPM    *int     `yaml:"min_descent_fpm"`
	LandingRadiusNM  *float64 `yaml:"landing_radius_nm"`
}

type rawNotify struct {
	Approach rawRule `yaml:"approach"`
	Landed   rawRule `yaml:"landed"`
	Novelty  rawRule `yaml:"novelty"`
}

type rawRule struct {
	Priority *int   `yaml:"priority"`
	Sound    scalar `yaml:"sound"`
}

var typeCode = regexp.MustCompile(`^[A-Z0-9]{2,4}$`)

// Parse validates the content of a config file. All problems found are
// reported together.
func Parse(data []byte) (*Config, error) {
	var raw rawConfig
	dec := yaml.NewDecoder(bytes.NewReader(data))
	dec.KnownFields(true)
	if err := dec.Decode(&raw); err != nil && !errors.Is(err, io.EOF) {
		return nil, cleanYAMLError(err)
	}

	sum := sha256.Sum256(data)
	c := &Config{
		PollInterval:     DefaultPollInterval,
		Cooldown:         DefaultCooldown,
		OutageAlertAfter: DefaultOutageAlertAfter,
		Detect: Detection{
			ApproachMaxAltFt: DefaultApproachMaxAltFt,
			MinDescentFPM:    DefaultMinDescentFPM,
			LandingRadiusNM:  DefaultLandingRadiusNM,
		},
		Hash: hex.EncodeToString(sum[:])[:12],
	}
	var problems []string
	fail := func(format string, args ...any) {
		problems = append(problems, fmt.Sprintf(format, args...))
	}

	tz := strings.TrimSpace(string(raw.Timezone))
	if tz == "" {
		tz = DefaultTimezone
	}
	loc, err := time.LoadLocation(tz)
	if err != nil {
		fail("timezone: unbekannte Zeitzone %q", tz)
		loc = time.UTC
	}
	c.Location = loc

	if raw.PollInterval != nil {
		c.PollInterval = time.Duration(*raw.PollInterval)
		if c.PollInterval < MinPollInterval {
			fail("poll_interval: mindestens %s, angegeben %s", MinPollInterval, c.PollInterval)
		}
	}
	if raw.OpenSkyInterval != nil {
		c.OpenSkyInterval = time.Duration(*raw.OpenSkyInterval)
		if c.OpenSkyInterval != 0 && c.OpenSkyInterval < MinOpenSkyInterval {
			fail("opensky_interval: mindestens %s oder auto, angegeben %s", MinOpenSkyInterval, c.OpenSkyInterval)
		}
	}
	if raw.Cooldown != nil {
		c.Cooldown = time.Duration(*raw.Cooldown)
		if c.Cooldown < 0 {
			fail("cooldown: darf nicht negativ sein")
		}
	}
	if raw.OutageAlertAfter != nil {
		c.OutageAlertAfter = time.Duration(*raw.OutageAlertAfter)
		if c.OutageAlertAfter < 0 {
			fail("outage_alert_after: darf nicht negativ sein")
		}
	}

	c.Sources = parseSources(raw.Sources, fail)
	c.Airports = parseAirports(raw.Airports, fail)
	c.Watch = parseWatch(raw.Watch, fail)
	c.Watch.Military, c.Watch.Interesting = raw.Watch.Military, raw.Watch.Interesting

	c.Novelty = Novelty{Enabled: raw.Novelty.Enabled, LearningPeriod: DefaultNoveltyLearning}
	if v := raw.Novelty.LearningPeriod; v != nil {
		c.Novelty.LearningPeriod = time.Duration(*v)
		if c.Novelty.LearningPeriod < 0 {
			fail("novelty.learning_period: darf nicht negativ sein")
		}
	}

	if v := raw.Detection.ApproachMaxAltFt; v != nil {
		c.Detect.ApproachMaxAltFt = *v
		if *v < 500 || *v > 10000 {
			fail("detection.approach_max_alt_ft: erlaubt sind 500 bis 10000, angegeben %d", *v)
		}
	}
	if v := raw.Detection.MinDescentFPM; v != nil {
		c.Detect.MinDescentFPM = *v
		if *v < 100 || *v > 3000 {
			fail("detection.min_descent_fpm: erlaubt sind 100 bis 3000, angegeben %d", *v)
		}
	}
	if v := raw.Detection.LandingRadiusNM; v != nil {
		c.Detect.LandingRadiusNM = *v
		if *v < 0.5 || *v > 10 {
			fail("detection.landing_radius_nm: erlaubt sind 0,5 bis 10, angegeben %g", *v)
		}
	}

	c.NotifyApproach = parseRule("notify.approach", raw.Notify.Approach, 0, fail)
	c.NotifyLanded = parseRule("notify.landed", raw.Notify.Landed, -1, fail)
	c.NotifyNovelty = parseRule("notify.novelty", raw.Notify.Novelty, -1, fail)

	if len(problems) > 0 {
		return nil, errors.New(strings.Join(problems, "; "))
	}
	return c, nil
}

func parseSources(in list, fail func(string, ...any)) []string {
	if len(in) == 0 {
		return []string{source.NameADSBLol, source.NameOpenSky}
	}
	var out []string
	for _, s := range in {
		name := strings.ToLower(strings.TrimSpace(string(s)))
		if !slices.Contains(source.Names, name) {
			fail("sources: unbekannte Quelle %q (bekannt: %s)", string(s), strings.Join(source.Names, ", "))
			continue
		}
		if !slices.Contains(out, name) {
			out = append(out, name)
		}
	}
	return out
}

func parseAirports(in []rawAirport, fail func(string, ...any)) []Airport {
	if len(in) == 0 {
		fail("airports: mindestens ein Flughafen wird gebraucht, etwa icao: EDDW")
		return nil
	}
	var out []Airport
	seen := map[string]bool{}
	for i, r := range in {
		icao := strings.ToUpper(strings.TrimSpace(string(r.ICAO)))
		if len(icao) != 4 {
			fail("airports: Eintrag %d: icao muss ein vierstelliger ICAO-Code sein, angegeben %q", i+1, string(r.ICAO))
			continue
		}
		if seen[icao] {
			fail("airports: %s steht mehrfach in der Liste", icao)
			continue
		}
		seen[icao] = true

		a := Airport{ICAO: icao, Name: strings.TrimSpace(string(r.Name)), RadiusNM: DefaultRadiusNM}
		if known, ok := airports.Lookup(icao); ok {
			a.Lat, a.Lon, a.ElevFt = known.Lat, known.Lon, known.ElevFt
			if a.Name == "" {
				a.Name = known.Name
			}
		} else if r.Lat == nil || r.Lon == nil {
			fail("airports: %s ist nicht in der eingebauten Liste deutscher Flughäfen, lat und lon müssen angegeben werden", icao)
			continue
		}
		if r.Lat != nil {
			a.Lat = *r.Lat
		}
		if r.Lon != nil {
			a.Lon = *r.Lon
		}
		if r.ElevFt != nil {
			a.ElevFt = *r.ElevFt
		}
		if a.Name == "" {
			a.Name = icao
		}
		if a.Lat < -90 || a.Lat > 90 || a.Lon < -180 || a.Lon > 180 {
			fail("airports: %s: lat/lon außerhalb des gültigen Bereichs", icao)
			continue
		}
		if r.RadiusNM != nil {
			a.RadiusNM = *r.RadiusNM
			if a.RadiusNM < MinRadiusNM || a.RadiusNM > MaxRadiusNM {
				fail("airports: %s: radius_nm muss zwischen %g und %g liegen, angegeben %g", icao, MinRadiusNM, MaxRadiusNM, a.RadiusNM)
				continue
			}
		}
		out = append(out, a)
	}
	return out
}

func parseWatch(r rawWatch, fail func(string, ...any)) Watch {
	w := Watch{types: map[string]bool{}, regs: map[string]bool{}, hexes: map[string]bool{}}
	for _, s := range r.Types {
		v := strings.ToUpper(strings.TrimSpace(string(s)))
		if !typeCode.MatchString(v) {
			fail("watch.types: %q ist kein ICAO-Typcode (2 bis 4 Zeichen, etwa A3ST)", string(s))
			continue
		}
		if !w.types[v] {
			w.types[v] = true
			w.Types = append(w.Types, v)
		}
	}
	for _, s := range r.Registrations {
		v := strings.ToUpper(strings.TrimSpace(string(s)))
		if v == "" {
			fail("watch.registrations: leerer Eintrag")
			continue
		}
		if !w.regs[v] {
			w.regs[v] = true
			w.Registrations = append(w.Registrations, v)
		}
	}
	for _, s := range r.Hex {
		v := aircraft.NormalizeHex(string(s))
		if v == "" {
			fail("watch.hex: %q ist kein ICAO24-Hexcode (6 Hexziffern)", string(s))
			continue
		}
		if !w.hexes[v] {
			w.hexes[v] = true
			w.Hex = append(w.Hex, v)
		}
	}
	for _, s := range r.CallsignPrefixes {
		v := strings.ToUpper(strings.TrimSpace(string(s)))
		if v == "" {
			fail("watch.callsign_prefixes: leerer Eintrag")
			continue
		}
		w.CallsignPrefixes = append(w.CallsignPrefixes, v)
	}
	for _, s := range r.Operators {
		v := strings.ToUpper(strings.Join(strings.Fields(string(s)), " "))
		if v == "" {
			fail("watch.operators: leerer Eintrag")
			continue
		}
		w.Operators = append(w.Operators, v)
	}
	return w
}

func parseRule(key string, r rawRule, defaultPriority int, fail func(string, ...any)) Rule {
	p := defaultPriority
	if r.Priority != nil {
		p = *r.Priority
	}
	// Priority 2 (emergency) needs retry and expire parameters and is not supported.
	if p < -2 || p > 1 {
		fail("%s.priority: erlaubt sind -2 bis 1, angegeben %d", key, p)
	}
	return Rule{Priority: p, Sound: strings.TrimSpace(string(r.Sound))}
}

var (
	goTypeInError = regexp.MustCompile(` in type \S+`)
	unknownField  = regexp.MustCompile(`field (\S+) not found`)
	wrongValue    = regexp.MustCompile("cannot unmarshal !!\\w+ `([^`]*)` into \\S+")
	wrongShape    = regexp.MustCompile(`cannot unmarshal !!\w+ into \S+`)
	lineNumber    = regexp.MustCompile(`\bline (\d+):`)
)

// cleanYAMLError turns the multi-line errors of the YAML library into one
// line, drops the Go type names they mention and translates the parts whose
// wording is known.
func cleanYAMLError(err error) error {
	msg := goTypeInError.ReplaceAllString(err.Error(), "")
	msg = strings.Join(strings.Fields(msg), " ")
	msg = strings.TrimPrefix(msg, "yaml: ")
	msg = strings.TrimPrefix(msg, "unmarshal errors: ")
	msg = unknownField.ReplaceAllString(msg, `unbekannter Schlüssel "$1"`)
	msg = wrongValue.ReplaceAllString(msg, `ungültiger Wert "$1"`)
	msg = wrongShape.ReplaceAllString(msg, "an dieser Stelle wird ein anderer Aufbau erwartet")
	msg = lineNumber.ReplaceAllString(msg, "Zeile $1:")
	return errors.New(msg)
}

// Loader re-reads a config file and detects changes by content, which also
// works when an editor or a Kubernetes ConfigMap mount replaces the file.
type Loader struct {
	path string
	seen string
	read bool
}

// NewLoader returns a loader for the file at path.
func NewLoader(path string) *Loader { return &Loader{path: path} }

// Path returns the file the loader reads.
func (l *Loader) Path() string { return l.path }

// Poll reads the file. changed is false if the content is the same as on the
// previous call; cfg and err are then nil. On a change, either cfg or err is
// set. hash identifies the content and is empty if the file is unreadable.
func (l *Loader) Poll() (cfg *Config, hash string, changed bool, err error) {
	data, readErr := os.ReadFile(l.path)
	fingerprint := "content:" + string(data)
	if readErr != nil {
		fingerprint = "error:" + readErr.Error()
	}
	if l.read && fingerprint == l.seen {
		return nil, "", false, nil
	}
	l.read, l.seen = true, fingerprint
	if readErr != nil {
		return nil, "", true, fmt.Errorf("Datei nicht lesbar: %w", readErr)
	}
	cfg, err = Parse(data)
	if err != nil {
		sum := sha256.Sum256(data)
		return nil, hex.EncodeToString(sum[:])[:12], true, err
	}
	return cfg, cfg.Hash, true, nil
}

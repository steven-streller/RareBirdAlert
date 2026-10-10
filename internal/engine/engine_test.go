package engine

import (
	"math"
	"path/filepath"
	"testing"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/config"
)

const testConfig = `
airports:
  - icao: EDDW
watch:
  types: [A3ST]
cooldown: 3h
`

// EDDW as in the embedded table.
const (
	eddwLat = 53.0468
	eddwLon = 8.7893
)

var t0 = time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC)

func mustConfig(t *testing.T, yaml string) *config.Config {
	t.Helper()
	cfg, err := config.Parse([]byte(yaml))
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

// west returns a position distNM nautical miles due west of EDDW.
func west(distNM float64) (lat, lon float64) {
	return eddwLat, eddwLon - distNM/(60*math.Cos(eddwLat*math.Pi/180))
}

type flight struct {
	hex, typ string
}

var beluga = flight{hex: "3c6444", typ: "A3ST"}

func (f flight) at(distNM float64, altFt, vrate int, ground bool, at time.Time) aircraft.Aircraft {
	lat, lon := west(distNM)
	return aircraft.Aircraft{
		Hex: f.hex, Type: f.typ, Reg: "F-GSTA", Callsign: "BGA123",
		Lat: lat, Lon: lon, HasPos: true,
		AltFt: altFt, HasAlt: !ground, OnGround: ground,
		VRateFPM: vrate, HasVRate: true,
		SeenAt: at, Sources: []string{"adsb.lol"},
	}
}

// run feeds the observations to the engine, one cycle each 15 s, and returns
// what happened in the last cycle.
type harness struct {
	t     *testing.T
	cfg   *config.Config
	state State
	now   time.Time
}

func newHarness(t *testing.T, yaml string) *harness {
	return &harness{t: t, cfg: mustConfig(t, yaml), state: NewState(), now: t0}
}

func (h *harness) cycle(covered bool, list ...aircraft.Aircraft) ([]Event, []Notification) {
	h.now = h.now.Add(15 * time.Second)
	next, events, due := Evaluate(h.state, h.cfg, []Input{{Airport: "EDDW", Covered: covered, Aircraft: list}}, h.now)
	h.state = next
	return events, due
}

func (h *harness) settle(n Notification) {
	h.state.MarkNotified(n.Key, n.Kind, h.now, h.cfg.Cooldown)
}

func only(t *testing.T, events []Event, kind EventKind) Event {
	t.Helper()
	var found []Event
	for _, e := range events {
		if e.Kind == kind {
			found = append(found, e)
		}
	}
	if len(found) != 1 {
		t.Fatalf("want exactly one %s event, got %d (%v)", kind, len(found), events)
	}
	return found[0]
}

func TestApproachAndLanding(t *testing.T) {
	h := newHarness(t, testConfig)

	// One cycle meeting the criteria is not enough.
	events, due := h.cycle(true, beluga.at(8, 2500, -700, false, h.now))
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("after one cycle: events %v, due %v", events, due)
	}

	events, due = h.cycle(true, beluga.at(7, 2200, -700, false, h.now))
	e := only(t, events, EventApproach)
	if e.Decision != DecisionNotifyApproach || e.Rule != config.RuleType || e.RuleValue != "A3ST" {
		t.Fatalf("approach event = %+v", e)
	}
	if len(due) != 1 || due[0].Kind != KindApproach || due[0].Airport.ICAO != "EDDW" {
		t.Fatalf("due = %+v", due)
	}

	// Unsettled, the notification stays due.
	_, due = h.cycle(true, beluga.at(6, 1900, -700, false, h.now))
	if len(due) != 1 || due[0].Kind != KindApproach {
		t.Fatalf("unsettled approach must stay due, got %+v", due)
	}
	h.settle(due[0])

	_, due = h.cycle(true, beluga.at(3, 800, -600, false, h.now))
	if len(due) != 0 {
		t.Fatalf("settled approach must not be due again: %+v", due)
	}

	events, due = h.cycle(true, beluga.at(1, 0, 0, true, h.now))
	e = only(t, events, EventLanded)
	if e.Presumed || e.Decision != DecisionNotifyLanded {
		t.Fatalf("landed event = %+v", e)
	}
	if len(due) != 1 || due[0].Kind != KindLanded || due[0].Presumed {
		t.Fatalf("due = %+v", due)
	}
	h.settle(due[0])

	// Taxiing around on the ground changes nothing.
	events, due = h.cycle(true, beluga.at(0.5, 0, 0, true, h.now))
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("on the ground: events %v, due %v", events, due)
	}
}

func TestOverflightAndOtherTypesAreIgnored(t *testing.T) {
	h := newHarness(t, testConfig)
	boeing := flight{hex: "abcdef", typ: "B738"}
	for i := 0; i < 5; i++ {
		events, due := h.cycle(true,
			beluga.at(5, 12000, 0, false, h.now),   // cruising overhead
			boeing.at(5, 2000, -700, false, h.now), // on approach, but not watched
		)
		if len(events) != 0 || len(due) != 0 {
			t.Fatalf("cycle %d: events %v, due %v", i, events, due)
		}
	}
	if len(h.state.Tracks) != 1 {
		t.Fatalf("only the watched aircraft is tracked, got %d tracks", len(h.state.Tracks))
	}
}

func TestDepartureOfClimbingAircraftDoesNotTrigger(t *testing.T) {
	h := newHarness(t, testConfig)
	for _, d := range []float64{1, 2, 3} {
		events, due := h.cycle(true, beluga.at(d, 1000*int(d), 1500, false, h.now))
		if len(events) != 0 || len(due) != 0 {
			t.Fatalf("climbing at %g NM: events %v, due %v", d, events, due)
		}
	}
}

func TestSignalLostLowAndCloseCountsAsLanded(t *testing.T) {
	h := newHarness(t, testConfig)
	h.cycle(true, beluga.at(5, 1500, -700, false, h.now))
	_, due := h.cycle(true, beluga.at(4, 1100, -700, false, h.now))
	h.settle(due[0])
	h.cycle(true, beluga.at(2.5, 500, -600, false, h.now))

	// Not seen any more. Without coverage the engine concludes nothing.
	for i := 0; i < 6; i++ {
		events, due := h.cycle(false)
		if len(events) != 0 || len(due) != 0 {
			t.Fatalf("blind cycle %d: events %v, due %v", i, events, due)
		}
	}

	var landed Event
	var notifications []Notification
	for i := 0; i < 6 && landed.Kind == ""; i++ {
		events, due := h.cycle(true)
		for _, e := range events {
			if e.Kind == EventLanded {
				landed, notifications = e, due
			}
		}
	}
	if landed.Kind == "" || !landed.Presumed {
		t.Fatalf("expected a presumed landing, got %+v", landed)
	}
	if len(notifications) != 1 || notifications[0].Kind != KindLanded || !notifications[0].Presumed {
		t.Fatalf("due = %+v", notifications)
	}
}

func TestSignalLostHighIsNotALanding(t *testing.T) {
	h := newHarness(t, testConfig)
	h.cycle(true, beluga.at(9, 3500, -700, false, h.now))
	h.cycle(true, beluga.at(8.5, 3300, -700, false, h.now))
	var lost bool
	for i := 0; i < 8; i++ {
		events, due := h.cycle(true)
		for _, e := range events {
			if e.Kind == EventLanded {
				t.Fatalf("a high aircraft that vanishes has not landed: %+v", e)
			}
			lost = lost || e.Kind == EventLost
		}
		if len(due) > 0 && due[0].Kind == KindLanded {
			t.Fatalf("unexpected landing notification")
		}
	}
	if !lost {
		t.Fatal("the approach should have been closed as lost")
	}
}

func TestCooldownSuppressesSecondApproach(t *testing.T) {
	h := newHarness(t, testConfig)

	approach := func(startDist float64) ([]Event, []Notification) {
		h.cycle(true, beluga.at(startDist, 2000, -700, false, h.now))
		return h.cycle(true, beluga.at(startDist-1, 1800, -700, false, h.now))
	}

	events, due := approach(6)
	only(t, events, EventApproach)
	h.settle(due[0])
	_, due = h.cycle(true, beluga.at(1, 0, 0, true, h.now))
	h.settle(due[0])

	// Takes off again (touch and go) and comes back within the cooldown.
	events, _ = h.cycle(true, beluga.at(1, 800, 1500, false, h.now))
	only(t, events, EventDeparted)
	h.cycle(true, beluga.at(4, 2000, 1500, false, h.now))
	h.cycle(true) // leaves the geofence's attention for a cycle
	h.now = h.now.Add(10 * time.Minute)

	events, due = approach(6)
	e := only(t, events, EventApproach)
	if e.Decision != DecisionNone || e.Reason != ReasonCooldown {
		t.Fatalf("second approach = %+v", e)
	}
	if len(due) != 0 {
		t.Fatalf("no push during the cooldown, got %+v", due)
	}
}

func TestApproachNotifiesAgainAfterCooldown(t *testing.T) {
	h := newHarness(t, testConfig)
	h.cycle(true, beluga.at(6, 2000, -700, false, h.now))
	_, due := h.cycle(true, beluga.at(5, 1800, -700, false, h.now))
	h.settle(due[0])
	_, due = h.cycle(true, beluga.at(1, 0, 0, true, h.now))
	h.settle(due[0])
	h.cycle(true, beluga.at(1, 900, 1500, false, h.now)) // departs

	h.now = h.now.Add(4 * time.Hour)
	h.cycle(true, beluga.at(6, 2000, -700, false, h.now))
	events, due := h.cycle(true, beluga.at(5, 1800, -700, false, h.now))
	if e := only(t, events, EventApproach); e.Decision != DecisionNotifyApproach {
		t.Fatalf("approach after the cooldown = %+v", e)
	}
	if len(due) != 1 {
		t.Fatalf("due = %+v", due)
	}
}

func TestGoAroundReturnsToNone(t *testing.T) {
	h := newHarness(t, testConfig)
	h.cycle(true, beluga.at(5, 1500, -700, false, h.now))
	_, due := h.cycle(true, beluga.at(4, 1200, -700, false, h.now))
	h.settle(due[0])

	h.cycle(true, beluga.at(2, 600, 1800, false, h.now))
	events, _ := h.cycle(true, beluga.at(3, 1200, 1800, false, h.now))
	only(t, events, EventGoAround)

	// Coming back in the same visit does not push again.
	h.now = h.now.Add(5 * time.Minute)
	h.cycle(true, beluga.at(6, 2000, -700, false, h.now))
	events, due = h.cycle(true, beluga.at(5, 1800, -700, false, h.now))
	if e := only(t, events, EventApproach); e.Reason != ReasonAlreadyNotified {
		t.Fatalf("second approach in the visit = %+v", e)
	}
	if len(due) != 0 {
		t.Fatalf("due = %+v", due)
	}
}

func TestFallbackToAltitudeTrendWithoutVRate(t *testing.T) {
	h := newHarness(t, testConfig)
	noRate := func(d float64, alt int) aircraft.Aircraft {
		a := beluga.at(d, alt, 0, false, h.now)
		a.HasVRate = false
		return a
	}
	h.cycle(true, noRate(7, 3000))
	h.cycle(true, noRate(6.5, 2900)) // first cycle with a trend
	events, _ := h.cycle(true, noRate(6, 2800))
	only(t, events, EventApproach)
}

func TestStoreRoundTripKeepsNotifiedFlags(t *testing.T) {
	h := newHarness(t, testConfig)
	h.cycle(true, beluga.at(6, 2000, -700, false, h.now))
	_, due := h.cycle(true, beluga.at(5, 1800, -700, false, h.now))
	h.settle(due[0])

	path := filepath.Join(t.TempDir(), "state.json")
	store := NewStore(path)
	if err := store.Flush(h.state, h.now); err != nil {
		t.Fatal(err)
	}

	loaded, restored, discarded, err := NewStore(path).Load(h.now.Add(time.Minute))
	if err != nil || restored != 1 || discarded != 0 {
		t.Fatalf("Load = %d restored, %d discarded, %v", restored, discarded, err)
	}
	tr := loaded.Tracks[due[0].Key]
	if !tr.ApproachNotified || tr.Phase != PhaseApproaching || !tr.CooldownUntil.After(h.now) {
		t.Fatalf("track after restart = %+v", tr)
	}

	// A restart must not repeat the approach push.
	next, _, notifications := Evaluate(loaded, h.cfg, []Input{{Airport: "EDDW", Covered: true,
		Aircraft: []aircraft.Aircraft{beluga.at(4, 1500, -700, false, h.now.Add(time.Minute))}}}, h.now.Add(time.Minute))
	if len(notifications) != 0 {
		t.Fatalf("restart repeated the push: %+v", notifications)
	}
	_ = next
}

func TestStoreResetsStaleApproach(t *testing.T) {
	st := NewState()
	st.Tracks["3c6444@EDDW"] = Track{Hex: "3c6444", Airport: "EDDW", Phase: PhaseApproaching, LastSeen: t0}
	path := filepath.Join(t.TempDir(), "state.json")
	if err := NewStore(path).Flush(st, t0); err != nil {
		t.Fatal(err)
	}
	loaded, _, _, err := NewStore(path).Load(t0.Add(2 * time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if got := loaded.Tracks["3c6444@EDDW"].Phase; got != PhaseNone {
		t.Fatalf("a two hour old approach must not survive a restart, phase = %q", got)
	}
}

const noveltyConfig = `
airports:
  - icao: EDDW
watch:
  types: [A3ST]
  military: true
novelty:
  enabled: true
  learning_period: 2h
`

var (
	bizjet = flight{hex: "aaaaaa", typ: "C25B"}
	airbus = flight{hex: "bbbbbb", typ: "A320"}
)

// approach flies f through two confirming cycles and returns the second one.
func (h *harness) approach(f flight) ([]Event, []Notification) {
	h.cycle(true, f.at(6, 2000, -700, false, h.now))
	return h.cycle(true, f.at(5, 1800, -700, false, h.now))
}

func (h *harness) leave(f flight) {
	h.cycle(true, f.at(1, 0, 0, true, h.now))
	h.cycle(true, f.at(1, 900, 1500, false, h.now)) // takes off again
	h.now = h.now.Add(10 * time.Minute)
}

func TestMilitaryFlagMakesAnAircraftInteresting(t *testing.T) {
	h := newHarness(t, noveltyConfig)
	mil := flight{hex: "ae080b", typ: "C17"}
	at := func(d float64, alt int, now time.Time) aircraft.Aircraft {
		a := mil.at(d, alt, -700, false, now)
		a.Military = true
		return a
	}
	h.cycle(true, at(6, 2000, h.now))
	events, due := h.cycle(true, at(5, 1800, h.now))
	e := only(t, events, EventApproach)
	if e.Rule != config.RuleMilitary || e.Decision != DecisionNotifyApproach {
		t.Fatalf("approach = %+v", e)
	}
	if len(due) != 1 || due[0].Rule != config.RuleMilitary {
		t.Fatalf("due = %+v", due)
	}
}

func TestMilitaryFlagIsIgnoredWhenSwitchedOff(t *testing.T) {
	h := newHarness(t, testConfig)
	a := func(d float64, alt int, now time.Time) aircraft.Aircraft {
		x := airbus.at(d, alt, -700, false, now)
		x.Military = true
		return x
	}
	h.cycle(true, a(6, 2000, h.now))
	events, due := h.cycle(true, a(5, 1800, h.now))
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("events %v, due %v", events, due)
	}
}

func TestNoveltyIsSilentWhileLearning(t *testing.T) {
	h := newHarness(t, noveltyConfig)

	events, due := h.approach(bizjet)
	e := only(t, events, EventApproach)
	if e.Rule != config.RuleNovelty || e.Decision != DecisionNone || e.Reason != ReasonLearning {
		t.Fatalf("approach while learning = %+v", e)
	}
	if len(due) != 0 {
		t.Fatalf("learning must not push: %+v", due)
	}
	if h.state.Seen["EDDW"]["C25B"].IsZero() {
		t.Fatal("the type must be recorded while learning")
	}

	// The landing of a silent visit stays silent as well.
	events, due = h.cycle(true, bizjet.at(1, 0, 0, true, h.now))
	e = only(t, events, EventLanded)
	if e.Decision != DecisionNone || e.Reason != ReasonLearning || len(due) != 0 {
		t.Fatalf("landing while learning = %+v, due %v", e, due)
	}
}

func TestNoveltyPushesOnlyForTypesNotSeenBefore(t *testing.T) {
	h := newHarness(t, noveltyConfig)

	// Learning: A320 and C25B become known.
	h.approach(airbus)
	h.leave(airbus)
	h.approach(bizjet)
	h.leave(bizjet)

	h.now = h.now.Add(3 * time.Hour) // the learning period (2 h) is over

	// A known type is nothing special.
	events, due := h.approach(airbus)
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("known type: events %v, due %v", events, due)
	}

	// A type nobody has seen at EDDW before is.
	rare := flight{hex: "cccccc", typ: "B461"}
	events, due = h.approach(rare)
	e := only(t, events, EventApproach)
	if e.Rule != config.RuleNovelty || e.RuleValue != "B461" || e.Decision != DecisionNotifyApproach {
		t.Fatalf("novelty approach = %+v", e)
	}
	if len(due) != 1 || due[0].Kind != KindApproach || due[0].Rule != config.RuleNovelty {
		t.Fatalf("due = %+v", due)
	}
	h.settle(due[0])

	// The type is now known; the visit is still followed to the landing.
	events, due = h.cycle(true, rare.at(3, 700, -600, false, h.now))
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("mid-approach: events %v, due %v", events, due)
	}
	events, due = h.cycle(true, rare.at(1, 0, 0, true, h.now))
	only(t, events, EventLanded)
	if len(due) != 1 || due[0].Kind != KindLanded {
		t.Fatalf("landing of a novelty visit = %+v", due)
	}
	h.settle(due[0])
	h.leave(rare)

	// A second aircraft of the same type is no longer new.
	again := flight{hex: "dddddd", typ: "B461"}
	events, due = h.approach(again)
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("second aircraft of a known type: events %v, due %v", events, due)
	}
}

func TestNoveltyNeedsATypeAndIsOff(t *testing.T) {
	h := newHarness(t, noveltyConfig)
	h.now = h.now.Add(3 * time.Hour)
	h.state.Learning["EDDW"] = t0 // learning period over
	noType := flight{hex: "eeeeee", typ: ""}
	events, due := h.approach(noType)
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("an aircraft without a type has no novelty: %v %v", events, due)
	}

	off := newHarness(t, testConfig)
	off.state.Learning["EDDW"] = t0.Add(-100 * time.Hour)
	events, due = off.approach(bizjet)
	if len(events) != 0 || len(due) != 0 {
		t.Fatalf("novelty is off by default: %v %v", events, due)
	}
}

func TestStoreKeepsSeenTypesAndLearningStart(t *testing.T) {
	h := newHarness(t, noveltyConfig)
	h.approach(bizjet)

	path := filepath.Join(t.TempDir(), "state.json")
	if err := NewStore(path).Flush(h.state, h.now); err != nil {
		t.Fatal(err)
	}
	loaded, _, _, err := NewStore(path).Load(h.now.Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Seen["EDDW"]["C25B"].IsZero() || loaded.Learning["EDDW"].IsZero() {
		t.Fatalf("seen = %v, learning = %v", loaded.Seen, loaded.Learning)
	}
	if !loaded.Learning["EDDW"].Equal(h.state.Learning["EDDW"]) {
		t.Error("the learning period must not restart with the process")
	}
}

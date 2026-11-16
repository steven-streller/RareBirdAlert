package engine

import (
	"path/filepath"
	"testing"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/airports"
	"rarebirdalert/internal/config"
)

const fleetTestConfig = `
airports:
  - icao: EDDW
fleet:
  home: EDVH
  aircraft:
    - registration: D-EZIP
`

// Positions used by the fleet tests.
var (
	atEDVH     = [2]float64{52.7619, 9.6106}  // Hodenhagen, 79 ft
	atEDVC     = [2]float64{52.6882, 10.1128} // Celle-Arloh, 207 ft
	enRoute    = [2]float64{52.7400, 9.8000}  // between the two, away from any airfield
	nearEDVH   = [2]float64{52.7700, 9.6400}  // in the circuit of Hodenhagen
	fleetKey   = "DEZIP"
	fleetEntry = aircraft.Aircraft{Hex: "3d1a2b", Reg: "D-EZIP", Type: "P28A"}
)

type fleetHarness struct {
	t     *testing.T
	cfg   *config.Config
	state State
	now   time.Time
}

func newFleetHarness(t *testing.T) *fleetHarness {
	if _, _, ok := airports.Nearest(enRoute[0], enRoute[1], fieldRadiusNM); ok {
		t.Fatal("the en-route position of the tests is at an airfield")
	}
	return &fleetHarness{t: t, cfg: mustConfig(t, fleetTestConfig), state: NewState(), now: t0}
}

// see runs one cycle in which the aircraft is received at pos.
func (h *fleetHarness) see(pos [2]float64, altFt int, gs float64) []FlightEvent {
	h.now = h.now.Add(15 * time.Second)
	ac := fleetEntry
	ac.Lat, ac.Lon, ac.HasPos = pos[0], pos[1], true
	ac.AltFt, ac.HasAlt = altFt, true
	ac.GroundSpeedKt, ac.HasSpeed = gs, true
	ac.SeenAt = h.now
	return h.eval(FleetInput{Covered: true, Seen: map[string]aircraft.Aircraft{fleetKey: ac}})
}

// miss runs cycles for the given time in which the aircraft is not received.
func (h *fleetHarness) miss(d time.Duration, covered bool) []FlightEvent {
	var all []FlightEvent
	for end := h.now.Add(d); h.now.Before(end); {
		h.now = h.now.Add(15 * time.Second)
		all = append(all, h.eval(FleetInput{Covered: covered})...)
	}
	return all
}

func (h *fleetHarness) eval(in FleetInput) []FlightEvent {
	next, events := EvaluateFleet(h.state, h.cfg, in, h.now)
	h.state = next
	return events
}

func (h *fleetHarness) track() FleetTrack { return h.state.Fleet[fleetKey] }

func (h *fleetHarness) takeOff() {
	h.t.Helper()
	h.see(atEDVH, 100, 6)
	h.see(atEDVH, 250, 60)
	if !started(h.see(nearEDVH, 800, 80)) {
		h.t.Fatal("no flight after two fast observations")
	}
}

func started(events []FlightEvent) bool {
	for _, e := range events {
		if e.Kind == FlightStarted {
			return true
		}
	}
	return false
}

func ended(t *testing.T, events []FlightEvent) Flight {
	t.Helper()
	for _, e := range events {
		if e.Kind == FlightEnded {
			return e.Flight
		}
	}
	t.Fatalf("no flight ended in %+v", events)
	return Flight{}
}

func TestFleetFlightToAnotherAirfield(t *testing.T) {
	h := newFleetHarness(t)

	if ev := h.see(atEDVH, 100, 6); len(ev) != 0 {
		t.Fatalf("taxiing: %+v", ev)
	}
	if ev := h.see(atEDVH, 250, 60); len(ev) != 0 {
		t.Fatalf("one fast observation must not begin a flight: %+v", ev)
	}
	first := h.now
	ev := h.see(nearEDVH, 800, 80)
	if len(ev) != 3 || ev[0].Kind != FlightStarted || ev[1].Kind != FlightPosition || ev[2].Kind != FlightPosition {
		t.Fatalf("events = %+v", ev)
	}
	f := ev[0].Flight
	if f.From != "EDVH" || f.FromAssumed || !f.Start.Equal(first) || f.ID != "DEZIP-20261010-120030" || f.Hex != "3d1a2b" {
		t.Errorf("flight = %+v", f)
	}

	h.see(enRoute, 2400, 105)
	h.see(atEDVC, 700, 65)
	h.see(atEDVC, 210, 10)
	touchdown := h.now
	if ev := h.see(atEDVC, 210, 4); len(ev) != 1 || ev[0].Kind != FlightPosition {
		t.Fatalf("30 s on the ground is not yet a landing: %+v", ev)
	}
	h.see(atEDVC, 210, 0)
	got := ended(t, h.see(atEDVC, 210, 0))
	if got.To != "EDVC" || got.Outcome != OutcomeLanded || !got.Away || !got.End.Equal(touchdown) {
		t.Errorf("ended = %+v", got)
	}
	if got.MaxAltFt != 2400 || got.DistanceNM < 18 || got.DistanceNM > 21 {
		t.Errorf("max alt %d, distance %.1f", got.MaxAltFt, got.DistanceNM)
	}

	tr := h.track()
	if tr.InFlight || tr.Pending == nil || tr.Pending.ID != got.ID || tr.LastField != "EDVC" {
		t.Errorf("track = %+v", tr)
	}
	h.state.SettleFleet(fleetKey)
	if h.track().Pending != nil {
		t.Error("the push must be settled")
	}
	if ev := h.see(atEDVC, 210, 0); len(ev) != 0 {
		t.Errorf("parked: %+v", ev)
	}
}

func TestFleetCircuitsAreOneFlight(t *testing.T) {
	h := newFleetHarness(t)
	h.takeOff()

	for range 3 {
		// Final and climb-out are below the coverage; the touch-and-go shows
		// up as a single observation on the ground, if at all.
		if ev := h.miss(2*time.Minute, true); len(ev) != 0 {
			t.Fatalf("a gap of two minutes ended the flight: %+v", ev)
		}
		h.see(atEDVH, 90, 25)
		if ev := h.see(nearEDVH, 700, 75); len(ev) != 1 || ev[0].Kind != FlightPosition {
			t.Fatalf("a touch-and-go must not end the flight: %+v", ev)
		}
	}
	if !h.track().InFlight {
		t.Fatal("the flight ended during the circuits")
	}

	h.see(nearEDVH, 500, 65)
	last := h.now
	got := ended(t, h.miss(6*time.Minute, true))
	if got.Outcome != OutcomePresumed || got.To != "EDVH" || got.From != "EDVH" || got.Away || !got.End.Equal(last) {
		t.Errorf("ended = %+v", got)
	}
	if h.track().Pending != nil {
		t.Error("a landing at home must not be pushed")
	}
}

func TestFleetSignalLostAwayFromAnyAirfield(t *testing.T) {
	h := newFleetHarness(t)
	h.takeOff()
	h.see(enRoute, 2400, 105)
	last := h.now

	if ev := h.miss(2*time.Hour, false); len(ev) != 0 {
		t.Fatalf("without data nothing may be concluded: %+v", ev)
	}
	// With data again, the aircraft has been gone for more than an hour.
	got := ended(t, h.miss(15*time.Second, true))
	if got.Outcome != OutcomeLost || got.To != "" || got.Away || !got.End.Equal(last) {
		t.Errorf("ended = %+v", got)
	}
	if tr := h.track(); tr.Pending != nil || tr.LastField != "" {
		t.Errorf("track = %+v", tr)
	}
}

func TestFleetFirstSeenInTheAirAssumesTheLastAirfield(t *testing.T) {
	h := newFleetHarness(t)
	h.see(atEDVH, 100, 0) // parked at home, then taken off unseen

	h.miss(10*time.Minute, true)
	h.see(enRoute, 2400, 100)
	ev := h.see(enRoute, 2400, 100)
	if !started(ev) || ev[0].Flight.From != "EDVH" || !ev[0].Flight.FromAssumed {
		t.Fatalf("events = %+v", ev)
	}
}

func TestFleetAircraftRemovedFromTheConfigIsForgotten(t *testing.T) {
	h := newFleetHarness(t)
	h.takeOff()
	h.cfg = mustConfig(t, testConfig)
	if ev := h.miss(15*time.Second, true); len(ev) != 0 || len(h.state.Fleet) != 0 {
		t.Errorf("events %+v, fleet %+v", ev, h.state.Fleet)
	}
}

func TestFleetSurvivesTheStateFile(t *testing.T) {
	h := newFleetHarness(t)
	h.takeOff()
	store := NewStore(filepath.Join(t.TempDir(), "state.json"))
	if _, err := store.Save(h.state, h.now); err != nil {
		t.Fatal(err)
	}
	st, _, _, err := NewStore(store.path).Load(h.now)
	if err != nil {
		t.Fatal(err)
	}
	got, want := st.Fleet[fleetKey], h.track()
	if !got.InFlight || got.Flight.ID != want.Flight.ID || !got.Last.SeenAt.Equal(want.Last.SeenAt) || got.Flight.Points != want.Flight.Points {
		t.Errorf("restored = %+v", got)
	}

	// A flight in progress keeps the state file fresh.
	if written, _ := store.Save(h.state, h.now.Add(heartbeat+time.Second)); !written {
		t.Error("the heartbeat must rewrite the state while a flight is open")
	}
}

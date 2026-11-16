package main

import (
	"bytes"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

const fleetConfig = `
airports:
  - icao: EDDW
sources: [adsb.lol]
fleet:
  home: EDVH
  aircraft:
    - registration: D-EZIP
      name: Piper Archer III
    - registration: D-EUAC
      name: Aquila AT01
`

// leg is where D-EZIP is in one cycle of a flight from Hodenhagen (EDVH) to
// Celle-Arloh (EDVC). seen false means that nobody receives it.
type leg struct {
	seen     bool
	lat, lon float64
	altFt    int
	gs       float64
}

var crossCountry = []leg{
	{true, 52.7619, 9.6106, 100, 8},    // taxiing at EDVH
	{true, 52.7625, 9.6200, 300, 65},   // rolling, lifting off
	{true, 52.7630, 9.6400, 900, 85},   // the flight begins with the second fast observation
	{true, 52.7400, 9.8000, 2400, 105}, // on the way
	{false, 0, 0, 0, 0},                // a gap in the coverage
	{true, 52.7000, 10.0500, 1200, 90}, // descending towards EDVC
	{true, 52.6890, 10.1050, 400, 62},  // short final
	{true, 52.6882, 10.1128, 210, 12},  // rolling out
	{true, 52.6882, 10.1130, 210, 5},
	{true, 52.6882, 10.1131, 210, 0},
	{true, 52.6882, 10.1131, 210, 0}, // on the ground for 45 s: landed
	{true, 52.6882, 10.1131, 210, 0},
}

func newFleetRig(t *testing.T, script []leg) *rig {
	t.Helper()
	gap := requestGap
	requestGap = time.Millisecond
	t.Cleanup(func() { requestGap = gap })

	r := newRig(t)
	// Around the airport nothing happens: the Beluga passes high overhead.
	r.script = []step{{8, 30000, 0, false}}
	if err := os.WriteFile(r.set.ConfigPath, []byte(fleetConfig), 0o644); err != nil {
		t.Fatal(err)
	}
	r.fleet = func(string) string {
		l := script[r.index]
		if !l.seen {
			return fmt.Sprintf(`{"ac":[],"msg":"No error","now":%d}`, r.now.UnixMilli())
		}
		return fmt.Sprintf(`{"ac":[{"hex":"3d1a2b","flight":"DEZIP   ","r":"D-EZIP","t":"P28A","alt_baro":%d,"gs":%g,"lat":%.5f,"lon":%.5f,"seen_pos":0.4}],"msg":"No error","now":%d}`,
			l.altFt, l.gs, l.lat, l.lon, r.now.UnixMilli())
	}
	return r
}

func TestFleetFlightIsLoggedAndLandingAwayIsPushed(t *testing.T) {
	r := newFleetRig(t, crossCountry)
	r.start()
	for i := range crossCountry {
		r.cycle(i)
	}

	if got := r.fleetLookup[0]; got != "/v2/reg/D-EZIP,D-EUAC" {
		t.Errorf("fleet lookup path = %q", got)
	}

	if len(r.pushes) != 1 {
		t.Fatalf("want one push for the landing away from home, got %d: %+v", len(r.pushes), r.pushes)
	}
	p := r.pushes[0].fields
	if p["title"] != "D-EZIP in EDVC Celle-Arloh gelandet" {
		t.Errorf("push = %v", p)
	}
	for _, want := range []string{"Piper Archer III", "Von EDVH Hodenhagen", "Flugzeit 2 min · 18,5 NM", "Gelandet um 14:01 Uhr"} {
		if !strings.Contains(p["message"], want) {
			t.Errorf("message lacks %q:\n%s", want, p["message"])
		}
	}
	if p["url"] != "https://adsb.lol/?icao=3d1a2b" {
		t.Errorf("url = %q", p["url"])
	}

	logs := r.logs.String()
	for _, want := range []string{
		`"event":"flight.started","registration":"D-EZIP","flight_id":"DEZIP-20261010-120029","name":"Piper Archer III","hex":"3d1a2b","type":"P28A","callsign":"DEZIP","from":"EDVH","from_name":"EDVH Hodenhagen","from_assumed":false`,
		`"event":"flight.position","registration":"D-EZIP","flight_id":"DEZIP-20261010-120029","lat":52.74,"lon":9.8,"altitude_ft":2400,"ground_speed_kt":105`,
		`"to":"EDVC","to_name":"EDVC Celle-Arloh","outcome":"landed","away":true`,
		`"max_altitude_ft":2400`,
		`"kind":"away_landing"`,
	} {
		if !strings.Contains(logs, want) {
			t.Errorf("log lacks %s", want)
		}
	}
	if n := strings.Count(logs, `"event":"flight.position"`); n != 9 {
		t.Errorf("want 9 track points until the landing is settled, got %d", n)
	}
	if n := strings.Count(logs, `"event":"flight.ended"`); n != 1 {
		t.Errorf("want one flight.ended, got %d", n)
	}

	r.wantMetric(`rarebirdalert_fleet_flights_total{registration="D-EZIP",outcome="landed"} 1`)
	r.wantMetric(`rarebirdalert_fleet_flights_total{registration="D-EUAC",outcome="landed"} 0`)
	r.wantMetric(`rarebirdalert_fleet_airborne{registration="D-EZIP"} 0`)
	r.wantMetric(`rarebirdalert_fleet_airborne{registration="D-EUAC"} 0`)
	r.wantMetric(`rarebirdalert_notifications_total{kind="away_landing",result="sent"} 1`)
	r.wantMetric(`rarebirdalert_fleet_flight_seconds_total{registration="D-EZIP"} 90`)
}

func TestFleetGaugesWhileInFlightAndRestartKeepsTheFlight(t *testing.T) {
	r := newFleetRig(t, crossCountry)
	r.start()
	for i := range 4 {
		r.cycle(i)
	}
	r.wantMetric(`rarebirdalert_fleet_airborne{registration="D-EZIP"} 1`)
	r.wantMetric(`rarebirdalert_fleet_altitude_feet{registration="D-EZIP"} 2400`)
	r.wantMetric(`rarebirdalert_fleet_ground_speed_knots{registration="D-EZIP"} 105`)

	// A restart in the air must neither lose the flight nor begin a new one.
	r.logs.Reset()
	r.start()
	for i := 4; i < len(crossCountry); i++ {
		r.cycle(i)
	}
	logs := r.logs.String()
	if strings.Contains(logs, `"event":"flight.started"`) {
		t.Error("the restart began a second flight")
	}
	if !strings.Contains(logs, `"event":"flight.ended","registration":"D-EZIP","flight_id":"DEZIP-20261010-120029"`) {
		t.Errorf("the flight was lost over the restart:\n%s", logs)
	}
	if len(r.pushes) != 1 {
		t.Errorf("pushes = %d", len(r.pushes))
	}
}

func TestFailedAwayLandingPushIsRetried(t *testing.T) {
	r := newFleetRig(t, crossCountry)
	r.pushStatus = func(attempt int) int {
		if attempt == 1 {
			return 500
		}
		return 200
	}
	r.start()
	for i := range crossCountry {
		r.cycle(i)
	}
	if r.attempts != 2 || len(r.pushes) != 1 {
		t.Errorf("attempts %d, pushes %d", r.attempts, len(r.pushes))
	}
}

func TestNowShowsTheFleet(t *testing.T) {
	r := newFleetRig(t, crossCountry)
	r.index = 3
	var out bytes.Buffer
	r.set.Stdout = &out
	if code := run([]string{"now"}, r.set); code != 0 {
		t.Fatalf("exit %d:\n%s", code, out.String())
	}
	text := out.String()
	for _, want := range []string{"Flotte, Heimatplatz EDVH", "adsb.lol: 1 von 2 empfangen", "D-EUAC  Aquila AT01       –       nicht empfangen", "3d1a2b", "2400", "105"} {
		if !strings.Contains(text, want) {
			t.Errorf("output lacks %q:\n%s", want, text)
		}
	}
}

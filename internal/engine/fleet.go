package engine

import (
	"maps"
	"slices"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/airports"
	"rarebirdalert/internal/config"
)

// The fleet is followed wherever it flies, and its flights are recorded:
//
//	on the ground -> in flight -> on the ground
//
// Small aircraft rarely say that they are on the ground, and close to the
// ground they are often not received at all. A flight therefore begins when
// an aircraft is seen moving at flying speed twice in a row, and ends when
//
//   - it is seen on the ground or slow for a while ("landed"), or
//   - it disappears low and close to an airfield ("presumed"), or
//   - it has not been seen for a long time ("lost": nobody knows where it went).
//
// A touch-and-go is too short for the first rule and a circuit too short for
// the second, so a session of circuits is one flight.

// Tunables of the flight detection.
const (
	flyingSpeedKt   = 40.0             // ground speed from which an aircraft counts as flying
	taxiSpeedKt     = 30.0             // ground speed below which a flying aircraft has landed
	airConfirm      = 2                // consecutive observations at flying speed that begin a flight
	airConfirmGap   = 2 * time.Minute  // observations further apart do not confirm each other
	groundConfirm   = 45 * time.Second // on the ground for this long: landed, not a touch-and-go
	fieldRadiusNM   = 3.0              // an aircraft this close to an airfield is at that airfield
	fieldMaxAGLFt   = 2500             // ... if it is not higher than this above it
	presumedAfter   = 5 * time.Minute  // unseen for this long, low and at an airfield: presumed landed
	presumedMaxAGL  = 1000             // ft above the airfield
	lostFlightAfter = time.Hour        // unseen for this long anywhere else: the flight is closed as lost
	offFieldAGLFt   = 300              // without a speed, this high above an airfield counts as flying
)

// Outcome says how a flight ended.
type Outcome string

const (
	OutcomeLanded   Outcome = "landed"   // seen on the ground
	OutcomePresumed Outcome = "presumed" // signal lost low at an airfield
	OutcomeLost     Outcome = "lost"     // signal lost somewhere else
)

// Outcomes lists every outcome.
var Outcomes = []Outcome{OutcomeLanded, OutcomePresumed, OutcomeLost}

// Flight is one flight of a fleet aircraft. From and To are ICAO codes of
// the built-in table, or empty if no airfield of the table was close.
type Flight struct {
	ID    string    `json:"id"`
	Hex   string    `json:"hex"`
	Start time.Time `json:"start"`
	From  string    `json:"from"`
	// FromAssumed marks a flight that was first seen away from any airfield:
	// From is then where the previous flight ended.
	FromAssumed bool      `json:"from_assumed,omitempty"`
	End         time.Time `json:"end"`
	To          string    `json:"to"`
	Outcome     Outcome   `json:"outcome,omitempty"`
	// Away is set for a landing at another airfield than the home ones.
	Away       bool    `json:"away,omitempty"`
	DistanceNM float64 `json:"distance_nm"` // along the track
	MaxAltFt   int     `json:"max_alt_ft"`  // barometric
	Points     int     `json:"points"`
}

// Duration is how long the flight took, or has taken so far.
func (f Flight) Duration(now time.Time) time.Duration {
	if !f.End.IsZero() {
		now = f.End
	}
	return now.Sub(f.Start)
}

// FleetTrack is what the engine remembers about one aircraft of the fleet.
type FleetTrack struct {
	Reg string `json:"reg"`

	// Last is the latest observation with a position.
	Last    aircraft.Aircraft `json:"last"`
	HasLast bool              `json:"has_last"`

	// AirCount counts consecutive observations at flying speed while no
	// flight is open; Last is then the latest of them and First the first.
	AirCount int               `json:"air_count,omitempty"`
	First    aircraft.Aircraft `json:"first,omitzero"`

	InFlight bool   `json:"in_flight"`
	Flight   Flight `json:"flight,omitzero"`
	// GroundSince is set while an aircraft in flight looks landed.
	GroundSince time.Time `json:"ground_since,omitzero"`

	// LastField is the airfield where the aircraft was last known to be on
	// the ground, empty if that is unknown.
	LastField string `json:"last_field,omitempty"`

	// Pending is a landing away from home whose push is still due.
	Pending *Flight `json:"pending,omitempty"`
}

// FleetInput is what the sources know about the fleet in this cycle.
type FleetInput struct {
	// Covered is false when no source delivered current data. The engine then
	// does not conclude anything from aircraft that are missing.
	Covered bool
	// Seen holds the aircraft that are being received with a position, by
	// config.FleetAircraft.Key.
	Seen map[string]aircraft.Aircraft
}

// FlightEventKind is what happened to a flight.
type FlightEventKind string

const (
	FlightStarted  FlightEventKind = "started"
	FlightPosition FlightEventKind = "position"
	FlightEnded    FlightEventKind = "ended"
)

// FlightEvent describes a step of a flight. Aircraft is the observation the
// event is about; for FlightEnded it is the last one of the flight.
type FlightEvent struct {
	Kind     FlightEventKind
	Entry    config.FleetAircraft
	Aircraft aircraft.Aircraft
	Flight   Flight
}

// SettleFleet records that the push for the landing away from home is
// settled, by delivery or by rejection.
func (s *State) SettleFleet(key string) {
	if t, ok := s.Fleet[key]; ok {
		t.Pending = nil
		s.Fleet[key] = t
	}
}

func (s State) fleetInFlight() bool {
	for _, t := range s.Fleet {
		if t.InFlight {
			return true
		}
	}
	return false
}

// EvaluateFleet advances the fleet by one cycle.
func EvaluateFleet(prev State, cfg *config.Config, in FleetInput, now time.Time) (State, []FlightEvent) {
	next := prev
	next.Fleet = maps.Clone(prev.Fleet)
	if next.Fleet == nil {
		next.Fleet = map[string]FleetTrack{}
	}

	entries := map[string]config.FleetAircraft{}
	for _, e := range cfg.Fleet.Aircraft {
		entries[e.Key()] = e
	}
	for key := range next.Fleet {
		if _, ok := entries[key]; !ok {
			delete(next.Fleet, key)
		}
	}

	var events []FlightEvent
	for _, key := range slices.Sorted(maps.Keys(entries)) {
		e := entries[key]
		t := next.Fleet[key]
		t.Reg = e.Reg
		emit := func(kind FlightEventKind, ac aircraft.Aircraft, f Flight) {
			events = append(events, FlightEvent{Kind: kind, Entry: e, Aircraft: ac, Flight: f})
		}

		if ac, ok := in.Seen[key]; ok && ac.HasPos && (!t.HasLast || ac.SeenAt.After(t.Last.SeenAt)) {
			observe(&t, ac, emit)
		}
		if t.InFlight {
			conclude(&t, &cfg.Fleet, in.Covered, now, emit)
		}
		next.Fleet[key] = t
	}
	return next, events
}

// observe applies a new observation.
func observe(t *FleetTrack, ac aircraft.Aircraft, emit func(FlightEventKind, aircraft.Aircraft, Flight)) {
	if t.InFlight {
		t.point(ac)
		emit(FlightPosition, ac, t.Flight)
		switch {
		case !down(ac):
			t.GroundSince = time.Time{}
		case t.GroundSince.IsZero():
			t.GroundSince = ac.SeenAt
		}
		return
	}

	if !flying(ac) {
		if f, ok := fieldAt(ac); ok {
			t.LastField = f.ICAO
		}
		t.AirCount, t.First = 0, aircraft.Aircraft{}
		t.Last, t.HasLast = ac, true
		return
	}

	// One fast observation can be noise in a computed position, so a flight
	// begins with the second one in a row.
	if t.AirCount == 0 || ac.SeenAt.Sub(t.Last.SeenAt) > airConfirmGap {
		t.AirCount, t.First = 0, ac
	}
	t.AirCount++
	if t.AirCount < airConfirm {
		t.Last, t.HasLast = ac, true
		return
	}

	first := t.First
	t.InFlight, t.AirCount, t.First, t.GroundSince = true, 0, aircraft.Aircraft{}, time.Time{}
	t.Flight = Flight{
		ID:    config.RegKey(t.Reg) + "-" + first.SeenAt.UTC().Format("20060102-150405"),
		Hex:   ac.Hex,
		Start: first.SeenAt,
	}
	if f, ok := fieldAt(first); ok {
		t.Flight.From = f.ICAO
	} else if t.LastField != "" {
		t.Flight.From, t.Flight.FromAssumed = t.LastField, true
	}
	t.point(first)
	emit(FlightStarted, first, t.Flight)
	emit(FlightPosition, first, t.Flight)
	if ac.SeenAt.After(first.SeenAt) {
		t.point(ac)
		emit(FlightPosition, ac, t.Flight)
	}
}

// conclude ends the open flight if there is reason to.
func conclude(t *FleetTrack, fleet *config.Fleet, covered bool, now time.Time, emit func(FlightEventKind, aircraft.Aircraft, Flight)) {
	unseen := now.Sub(t.Last.SeenAt)
	f := t.Flight
	switch {
	case !t.GroundSince.IsZero() && now.Sub(t.GroundSince) >= groundConfirm:
		f.Outcome, f.End = OutcomeLanded, t.GroundSince
		if field, _, ok := airports.Nearest(t.Last.Lat, t.Last.Lon, fieldRadiusNM); ok {
			f.To = field.ICAO
		}
	case !covered:
		return
	case unseen > presumedAfter && lowAtField(t.Last) != "":
		f.Outcome, f.End, f.To = OutcomePresumed, t.Last.SeenAt, lowAtField(t.Last)
	case unseen > lostFlightAfter:
		f.Outcome, f.End = OutcomeLost, t.Last.SeenAt
	default:
		return
	}

	f.Away = f.Outcome != OutcomeLost && len(fleet.Home) > 0 && !fleet.IsHome(f.To)
	if f.Away {
		t.Pending = &f
	}
	t.InFlight, t.Flight, t.GroundSince = false, Flight{}, time.Time{}
	// After a lost flight nobody knows where the aircraft is.
	t.LastField = f.To
	emit(FlightEnded, t.Last, f)
}

// point adds an observation to the track of the open flight.
func (t *FleetTrack) point(ac aircraft.Aircraft) {
	if t.Flight.Points > 0 {
		t.Flight.DistanceNM += airports.DistanceNM(t.Last.Lat, t.Last.Lon, ac.Lat, ac.Lon)
	}
	t.Flight.Points++
	if ac.HasAlt && !ac.OnGround {
		t.Flight.MaxAltFt = max(t.Flight.MaxAltFt, ac.AltFt)
	}
	t.Last, t.HasLast = ac, true
}

// flying reports whether an aircraft that is not in a flight has taken off.
func flying(ac aircraft.Aircraft) bool {
	switch {
	case ac.OnGround:
		return false
	case ac.HasSpeed:
		return ac.GroundSpeedKt >= flyingSpeedKt
	case !ac.HasAlt:
		return false
	}
	// No speed: judge by the height above the airfield, if there is one.
	if f, _, ok := airports.Nearest(ac.Lat, ac.Lon, fieldRadiusNM); ok {
		return ac.AltFt-f.ElevFt > offFieldAGLFt
	}
	return true
}

// down reports whether an aircraft in flight looks landed. The thresholds
// for taking off and for landing differ, so that an aircraft flying slowly
// into the wind does not flip between the two.
func down(ac aircraft.Aircraft) bool {
	switch {
	case ac.OnGround:
		return true
	case ac.HasSpeed:
		return ac.GroundSpeedKt < taxiSpeedKt
	}
	return !flying(ac)
}

// fieldAt returns the airfield an aircraft is at: close and not high above.
func fieldAt(ac aircraft.Aircraft) (airports.Airport, bool) {
	f, _, ok := airports.Nearest(ac.Lat, ac.Lon, fieldRadiusNM)
	if !ok || (ac.HasAlt && !ac.OnGround && ac.AltFt-f.ElevFt > fieldMaxAGLFt) {
		return airports.Airport{}, false
	}
	return f, true
}

// lowAtField returns the airfield at which an aircraft that disappeared has
// presumably landed, or "".
func lowAtField(ac aircraft.Aircraft) string {
	f, _, ok := airports.Nearest(ac.Lat, ac.Lon, fieldRadiusNM)
	if !ok || (ac.HasAlt && !ac.OnGround && ac.AltFt-f.ElevFt > presumedMaxAGL) {
		return ""
	}
	return f.ICAO
}

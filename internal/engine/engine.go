// Package engine decides when a push is due. It does no I/O: it gets the
// aircraft around each airport and the previous state, and returns the new
// state, what happened and the notifications that are due.
//
// None of the sources says where an aircraft is going, so approach and
// landing are derived from positions. Per aircraft and airport the engine
// walks through three phases:
//
//	none -> approaching -> landed -> none (departed)
//
// An aircraft is approaching when it is inside the airport's geofence, low
// enough, descending and getting closer, in two consecutive cycles. It has
// landed when it is reported on the ground close to the airport, or when it
// disappears at low level close to the airport ("presumed": ADS-B coverage
// often ends just above the ground).
package engine

import (
	"maps"
	"slices"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/airports"
	"rarebirdalert/internal/config"
)

// Phase is where an aircraft is in its visit of an airport.
type Phase string

const (
	PhaseNone        Phase = ""
	PhaseApproaching Phase = "approaching"
	PhaseLanded      Phase = "landed"
)

// Tunables that are not worth a config key.
const (
	confirmCycles    = 2                // consecutive cycles that confirm an approach
	closingTolerance = 0.3              // NM an aircraft may drift away between cycles
	goAroundVRate    = 500              // fpm
	goAroundAGL      = 300              // ft above the field
	goAroundCycles   = 2                //
	departedAGL      = 300              // ft above the field: a landed aircraft above this has taken off
	lostBelowAGL     = 1500             // ft: lost below this, close in, counts as landed
	lostWithinNM     = 5.0              //
	minLostAfter     = 45 * time.Second //
	trendMaxAge      = 90 * time.Second // older observations say nothing about a trend
	dropAfter        = 30 * time.Minute // a track nobody has seen for this long is forgotten
)

// Track is what the engine remembers about one aircraft at one airport.
type Track struct {
	Hex     string `json:"hex"`
	Airport string `json:"airport"`
	Phase   Phase  `json:"phase"`

	Confirm int `json:"confirm"` // consecutive cycles that met the approach criteria
	Climb   int `json:"climb"`   // consecutive cycles that looked like a go-around

	// ApproachNotified and LandedNotified are per visit. CooldownUntil spans
	// visits: it keeps a pattern of touch-and-goes from becoming a stream of
	// pushes.
	ApproachNotified bool      `json:"approach_notified"`
	LandedNotified   bool      `json:"landed_notified"`
	CooldownUntil    time.Time `json:"cooldown_until"`
	Presumed         bool      `json:"presumed"`

	FirstSeen  time.Time `json:"first_seen"`
	LastSeen   time.Time `json:"last_seen"`
	HasLast    bool      `json:"has_last"`
	LastDistNM float64   `json:"last_dist_nm"`
	LastAGLFt  int       `json:"last_agl_ft"`

	// Silent marks a visit that is recorded but never pushed: the approach of
	// a type that only looked new because the learning period was running.
	Silent bool `json:"silent"`

	Rule      string            `json:"rule"`
	RuleValue string            `json:"rule_value"`
	Last      aircraft.Aircraft `json:"last"`
}

// State is everything the engine remembers.
type State struct {
	Tracks map[string]Track `json:"tracks"`
	// Seen holds, per airport, the aircraft types that have approached it and
	// when they did so for the first time. Novelty is judged against it.
	Seen map[string]map[string]time.Time `json:"seen"`
	// Learning holds, per airport, when the engine first had data for it.
	Learning map[string]time.Time `json:"learning"`
}

// NewState returns an empty state.
func NewState() State {
	return State{
		Tracks:   map[string]Track{},
		Seen:     map[string]map[string]time.Time{},
		Learning: map[string]time.Time{},
	}
}

// clone copies the maps the engine writes to.
func (s State) clone() State {
	c := NewState()
	maps.Copy(c.Tracks, s.Tracks)
	maps.Copy(c.Learning, s.Learning)
	for airport, types := range s.Seen {
		c.Seen[airport] = maps.Clone(types)
	}
	return c
}

// Key identifies a track.
func Key(hex, airport string) string { return hex + "@" + airport }

// MarkNotified records that the notification of kind is settled, by delivery
// or by rejection. It also starts the cooldown.
func (s *State) MarkNotified(key string, kind Kind, now time.Time, cooldown time.Duration) {
	t, ok := s.Tracks[key]
	if !ok {
		return
	}
	switch kind {
	case KindApproach:
		t.ApproachNotified = true
	case KindLanded:
		t.LandedNotified = true
	}
	if until := now.Add(cooldown); until.After(t.CooldownUntil) {
		t.CooldownUntil = until
	}
	s.Tracks[key] = t
}

// Input is what one airport's surroundings look like in this cycle.
type Input struct {
	Airport string // ICAO
	// Covered is false when no source delivered current data. The engine then
	// does not conclude anything from aircraft that are missing.
	Covered  bool
	Aircraft []aircraft.Aircraft
}

// Kind is the kind of a notification.
type Kind string

const (
	KindApproach Kind = "approach"
	KindLanded   Kind = "landed"
)

// Notification is a push that is due. It stays due, cycle after cycle, until
// MarkNotified settles it.
type Notification struct {
	Kind      Kind
	Key       string
	Airport   config.Airport
	Aircraft  aircraft.Aircraft
	Rule      string
	RuleValue string
	DistNM    float64
	AGLFt     int
	Presumed  bool // landed because the signal was lost, not because it was seen on the ground
}

// EventKind is a state change worth a log line.
type EventKind string

const (
	EventApproach EventKind = "approach"
	EventLanded   EventKind = "landed"
	EventGoAround EventKind = "go_around"
	EventDeparted EventKind = "departed"
	EventLost     EventKind = "lost"
)

// Decision says what the engine did about an event.
type Decision string

const (
	DecisionNotifyApproach Decision = "notify_approach"
	DecisionNotifyLanded   Decision = "notify_landed"
	DecisionNone           Decision = "none"
)

// Reason explains a DecisionNone.
type Reason string

const (
	ReasonCooldown            Reason = "cooldown"
	ReasonAlreadyNotified     Reason = "already_notified"
	ReasonApproachNotNotified Reason = "approach_not_notified"
	ReasonLearning            Reason = "learning"
)

// Event describes a state change.
type Event struct {
	Kind      EventKind
	Airport   config.Airport
	Aircraft  aircraft.Aircraft
	Rule      string
	RuleValue string
	DistNM    float64
	AGLFt     int
	Presumed  bool
	Decision  Decision
	Reason    Reason
}

// Evaluate advances the state by one cycle.
func Evaluate(prev State, cfg *config.Config, inputs []Input, now time.Time) (State, []Event, []Notification) {
	next := prev.clone()

	var events []Event
	lostAfter := max(minLostAfter, 3*cfg.PollInterval)

	for _, in := range inputs {
		ap, ok := cfg.Airport(in.Airport)
		if !ok {
			continue
		}

		if in.Covered && next.Learning[ap.ICAO].IsZero() {
			next.Learning[ap.ICAO] = now
		}
		learning := now.Sub(next.Learning[ap.ICAO]) < cfg.Novelty.LearningPeriod

		seen := map[string]bool{}
		for _, ac := range in.Aircraft {
			if !ac.HasPos {
				continue
			}
			dist := airports.DistanceNM(ap.Lat, ap.Lon, ac.Lat, ac.Lon)
			if dist > ap.RadiusNM {
				continue
			}
			key := Key(ac.Hex, ap.ICAO)
			t, tracked := next.Tracks[key]

			rule, value, matched := cfg.Watch.Match(ac)
			switch {
			case matched:
			case tracked && t.Phase != PhaseNone:
				// Keep following a visit that is under way, even if the
				// config or the seen types changed since it began. Between
				// visits the aircraft has to qualify again.
				rule, value, matched = t.Rule, t.RuleValue, true
			case cfg.Novelty.Enabled && ac.Type != "" && next.Seen[ap.ICAO][ac.Type].IsZero():
				rule, value, matched = config.RuleNovelty, ac.Type, true
			}
			if !matched {
				continue
			}
			if !tracked {
				t = Track{Hex: ac.Hex, Airport: ap.ICAO, FirstSeen: now}
			}
			seen[key] = true
			if !tracked || t.Phase == PhaseNone {
				t.Rule, t.RuleValue = rule, value
			}

			var evs []Event
			t, evs = step(t, ac, ap, dist, now, cfg)
			for i := range evs {
				if evs[i].Kind != EventApproach {
					continue
				}
				if cfg.Novelty.Enabled && ac.Type != "" {
					if next.Seen[ap.ICAO] == nil {
						next.Seen[ap.ICAO] = map[string]time.Time{}
					}
					if next.Seen[ap.ICAO][ac.Type].IsZero() {
						next.Seen[ap.ICAO][ac.Type] = now
					}
				}
				if t.Rule == config.RuleNovelty && learning {
					t.Silent = true
					evs[i].Decision, evs[i].Reason = DecisionNone, ReasonLearning
				}
			}
			next.Tracks[key] = t
			events = append(events, evs...)
		}

		if !in.Covered {
			continue
		}
		for _, key := range slices.Sorted(maps.Keys(next.Tracks)) {
			t := next.Tracks[key]
			if t.Airport != ap.ICAO || seen[key] {
				continue
			}
			var evs []Event
			t, evs = unseen(t, ap, now, lostAfter)
			next.Tracks[key] = t
			events = append(events, evs...)
		}
	}

	var due []Notification
	for _, key := range slices.Sorted(maps.Keys(next.Tracks)) {
		t := next.Tracks[key]
		ap, ok := cfg.Airport(t.Airport)
		if !ok {
			continue
		}
		switch {
		case t.Silent:
		case t.Phase == PhaseApproaching && !t.ApproachNotified && !now.Before(t.CooldownUntil):
			due = append(due, notification(KindApproach, key, t, ap))
		case t.Phase == PhaseLanded && !t.LandedNotified && (t.ApproachNotified || !now.Before(t.CooldownUntil)):
			due = append(due, notification(KindLanded, key, t, ap))
		}

		if t.Phase != PhaseApproaching && now.Sub(t.LastSeen) > dropAfter && !now.Before(t.CooldownUntil) {
			delete(next.Tracks, key)
		}
	}
	return next, events, due
}

func notification(kind Kind, key string, t Track, ap config.Airport) Notification {
	return Notification{
		Kind: kind, Key: key, Airport: ap, Aircraft: t.Last,
		Rule: t.Rule, RuleValue: t.RuleValue,
		DistNM: t.LastDistNM, AGLFt: t.LastAGLFt, Presumed: t.Presumed,
	}
}

func event(kind EventKind, t Track, ap config.Airport) Event {
	return Event{
		Kind: kind, Airport: ap, Aircraft: t.Last, Rule: t.Rule, RuleValue: t.RuleValue,
		DistNM: t.LastDistNM, AGLFt: t.LastAGLFt, Presumed: t.Presumed,
	}
}

// step applies one observation of an aircraft that is inside the geofence.
func step(t Track, ac aircraft.Aircraft, ap config.Airport, dist float64, now time.Time, cfg *config.Config) (Track, []Event) {
	d := cfg.Detect
	agl := ac.AltFt - ap.ElevFt

	// Trends compare with the previous observation, which only counts if it
	// is recent: an aircraft that comes back after a go-around has no trend.
	prev := t.HasLast && now.Sub(t.LastSeen) <= trendMaxAge
	descending := (ac.HasVRate && ac.VRateFPM <= -d.MinDescentFPM) ||
		(!ac.HasVRate && prev && ac.HasAlt && agl <= t.LastAGLFt-50)
	closing := !prev || dist <= t.LastDistNM+closingTolerance
	approachCriteria := !ac.OnGround && ac.HasAlt && agl <= d.ApproachMaxAltFt && descending && closing

	t.Last = ac
	t.LastSeen = now
	t.LastDistNM = dist
	if ac.HasAlt {
		t.LastAGLFt = agl
	}
	t.HasLast = true

	var events []Event
	switch t.Phase {
	case PhaseNone:
		if !approachCriteria {
			t.Confirm = 0
			break
		}
		t.Confirm++
		if t.Confirm < confirmCycles {
			break
		}
		t.Phase, t.Confirm, t.Climb, t.Presumed = PhaseApproaching, 0, 0, false
		e := event(EventApproach, t, ap)
		switch {
		case t.ApproachNotified:
			e.Decision, e.Reason = DecisionNone, ReasonAlreadyNotified
		case now.Before(t.CooldownUntil):
			e.Decision, e.Reason = DecisionNone, ReasonCooldown
		default:
			e.Decision = DecisionNotifyApproach
		}
		events = append(events, e)

	case PhaseApproaching:
		switch {
		case ac.OnGround && dist <= d.LandingRadiusNM:
			t.Phase, t.Presumed = PhaseLanded, false
			events = append(events, landedEvent(t, ap, now))
		case ac.HasVRate && ac.VRateFPM >= goAroundVRate && ac.HasAlt && agl > goAroundAGL:
			t.Climb++
			if t.Climb >= goAroundCycles {
				t.Phase, t.Confirm, t.Climb = PhaseNone, 0, 0
				events = append(events, event(EventGoAround, t, ap))
			}
		default:
			t.Climb = 0
		}

	case PhaseLanded:
		if !ac.OnGround && ac.HasAlt && agl > departedAGL {
			t.Phase, t.Confirm, t.Climb, t.Presumed = PhaseNone, 0, 0, false
			t.ApproachNotified, t.LandedNotified, t.Silent = false, false, false
			events = append(events, event(EventDeparted, t, ap))
		}
	}
	return t, events
}

// unseen applies a cycle in which an aircraft with a track was not in the
// geofence, although the sources delivered current data.
func unseen(t Track, ap config.Airport, now time.Time, lostAfter time.Duration) (Track, []Event) {
	switch t.Phase {
	case PhaseNone:
		t.Confirm = 0
	case PhaseApproaching:
		if now.Sub(t.LastSeen) < lostAfter {
			break
		}
		if t.HasLast && t.LastAGLFt <= lostBelowAGL && t.LastDistNM <= min(lostWithinNM, ap.RadiusNM) {
			t.Phase, t.Presumed = PhaseLanded, true
			return t, []Event{landedEvent(t, ap, now)}
		}
		t.Phase, t.Confirm, t.Climb = PhaseNone, 0, 0
		return t, []Event{event(EventLost, t, ap)}
	}
	return t, nil
}

func landedEvent(t Track, ap config.Airport, now time.Time) Event {
	e := event(EventLanded, t, ap)
	switch {
	case t.Silent:
		e.Decision, e.Reason = DecisionNone, ReasonLearning
	case t.LandedNotified:
		e.Decision, e.Reason = DecisionNone, ReasonAlreadyNotified
	case t.ApproachNotified || !now.Before(t.CooldownUntil):
		e.Decision = DecisionNotifyLanded
	default:
		e.Decision, e.Reason = DecisionNone, ReasonApproachNotNotified
	}
	return e
}

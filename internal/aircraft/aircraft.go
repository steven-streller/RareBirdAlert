// Package aircraft holds the common model of an aircraft as reported by the
// data sources, and the merge of what several sources know about the same
// aircraft.
package aircraft

import (
	"slices"
	"sort"
	"strings"
	"time"
)

// Aircraft is one aircraft in the units the rest of the application uses:
// feet, knots and feet per minute.
type Aircraft struct {
	Hex      string `json:"hex"`                // ICAO 24-bit address, lower case
	Callsign string `json:"callsign,omitempty"` //
	Reg      string `json:"reg,omitempty"`      // registration
	Type     string `json:"type,omitempty"`     // ICAO type designator, e.g. A3ST
	Desc     string `json:"desc,omitempty"`     // long type name
	Operator string `json:"operator,omitempty"`

	Lat           float64 `json:"lat"`
	Lon           float64 `json:"lon"`
	HasPos        bool    `json:"has_pos"`
	AltFt         int     `json:"alt_ft"` // barometric
	HasAlt        bool    `json:"has_alt"`
	OnGround      bool    `json:"on_ground"`
	GroundSpeedKt float64 `json:"gs_kt"`
	VRateFPM      int     `json:"vrate_fpm"`
	HasVRate      bool    `json:"has_vrate"`

	Military    bool `json:"military,omitempty"`
	Interesting bool `json:"interesting,omitempty"`

	SeenAt  time.Time `json:"seen_at"` // when the position was last updated
	Sources []string  `json:"sources"` // sources that reported the aircraft
}

// Group is what one source delivered for one area.
type Group struct {
	Source   string
	Aircraft []Aircraft
}

// Merge combines the groups into one list, sorted by hex.
//
// Groups are given in priority order. The identity of an aircraft
// (registration, type, operator, ...) comes from the first group that has it.
// Position, altitude and rates come from whichever source updated them last.
func Merge(groups []Group) []Aircraft {
	merged := map[string]*Aircraft{}
	for _, g := range groups {
		for _, in := range g.Aircraft {
			if in.Hex == "" {
				continue
			}
			m, ok := merged[in.Hex]
			if !ok {
				c := in
				c.Sources = []string{g.Source}
				merged[in.Hex] = &c
				continue
			}
			if !slices.Contains(m.Sources, g.Source) {
				m.Sources = append(m.Sources, g.Source)
			}
			fill(&m.Callsign, in.Callsign)
			fill(&m.Reg, in.Reg)
			fill(&m.Type, in.Type)
			fill(&m.Desc, in.Desc)
			fill(&m.Operator, in.Operator)
			m.Military = m.Military || in.Military
			m.Interesting = m.Interesting || in.Interesting
			if in.SeenAt.After(m.SeenAt) {
				m.SeenAt = in.SeenAt
				m.OnGround = in.OnGround
				if in.HasPos {
					m.Lat, m.Lon, m.HasPos = in.Lat, in.Lon, true
				}
				if in.HasAlt {
					m.AltFt, m.HasAlt = in.AltFt, true
				}
				if in.HasVRate {
					m.VRateFPM, m.HasVRate = in.VRateFPM, true
				}
				if in.GroundSpeedKt > 0 {
					m.GroundSpeedKt = in.GroundSpeedKt
				}
			} else if !m.HasPos && in.HasPos {
				m.Lat, m.Lon, m.HasPos = in.Lat, in.Lon, true
			}
		}
	}
	out := make([]Aircraft, 0, len(merged))
	for _, m := range merged {
		out = append(out, *m)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Hex < out[j].Hex })
	return out
}

func fill(dst *string, v string) {
	if *dst == "" {
		*dst = strings.TrimSpace(v)
	}
}

// Fresh returns the aircraft whose position is not older than maxAge.
func Fresh(list []Aircraft, now time.Time, maxAge time.Duration) []Aircraft {
	out := make([]Aircraft, 0, len(list))
	for _, a := range list {
		if now.Sub(a.SeenAt) <= maxAge {
			out = append(out, a)
		}
	}
	return out
}

// NormalizeHex returns the lower-case form of an ICAO 24-bit address, or ""
// if s is not one. Addresses with a leading "~" are non-ICAO TIS-B targets.
func NormalizeHex(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	if len(s) != 6 {
		return ""
	}
	for _, c := range s {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return ""
		}
	}
	return s
}

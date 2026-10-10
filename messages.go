package main

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/config"
	"rarebirdalert/internal/engine"
	"rarebirdalert/internal/photos"
	"rarebirdalert/internal/pushover"
)

// Lifetimes of the pushes: an approach that is half an hour old is no use any
// more, a landing stays interesting a little longer.
const (
	approachTTL = 30 * time.Minute
	landedTTL   = 3 * time.Hour
)

// ruleNames are the rule names in the words of the push.
var ruleNames = map[string]string{
	config.RuleHex:          "ICAO24",
	config.RuleRegistration: "Kennzeichen",
	config.RuleType:         "Typ",
	config.RuleCallsign:     "Rufzeichen",
	config.RuleOperator:     "Betreiber",
	config.RuleMilitary:     "Militär",
	config.RuleInteresting:  "als interessant markiert",
	config.RuleNovelty:      "neuer Typ an diesem Flughafen",
}

// aircraftMessage builds the push for an approach or a landing. photo may be
// nil.
//
//	Title: "A3ST im Anflug auf Bremen" / "A3ST in Bremen gelandet"
//	Body:  type name, registration and callsign, operator, position, reason, sources
func aircraftMessage(n engine.Notification, cfg *config.Config, photo *photos.Photo) pushover.Message {
	ac := n.Aircraft
	what := firstNonEmpty(ac.Type, ac.Reg, ac.Callsign, strings.ToUpper(ac.Hex))
	airport := n.Airport.ShortName()

	title := what + " im Anflug auf " + airport
	rule, ttl := cfg.NotifyApproach, approachTTL
	if n.Rule == config.RuleNovelty {
		// A new type is a nice find, not a reason to be woken up.
		rule = cfg.NotifyNovelty
	}
	if n.Kind == engine.KindLanded {
		if n.Rule != config.RuleNovelty {
			rule = cfg.NotifyLanded
		}
		ttl = landedTTL
		title = what + " in " + airport + " gelandet"
		if n.Presumed {
			title = what + " in " + airport + " vermutlich gelandet"
		}
	}

	lines := []string{
		joinNonEmpty(" · ", ac.Desc, ac.Type),
		joinNonEmpty(" · ", labelled("Kennzeichen", ac.Reg), labelled("Rufzeichen", ac.Callsign)),
		labelled("Betreiber", ac.Operator),
	}
	if n.Kind == engine.KindApproach {
		lines = append(lines, fmt.Sprintf("%s NM entfernt · %s ft · %s",
			germanFloat(n.DistNM), thousands(ac.AltFt), rate(ac)))
	} else if n.Presumed {
		lines = append(lines, "Signal knapp über dem Boden verloren")
	}
	lines = append(lines,
		joinNonEmpty(": ", "Treffer", joinNonEmpty(" ", ruleNames[n.Rule], n.RuleValue)),
		"Quellen: "+strings.Join(ac.Sources, ", "),
	)

	msg := pushover.Message{
		Title:    title,
		Priority: rule.Priority,
		Sound:    rule.Sound,
		TTL:      ttl,
		URL:      mapURL(ac.Hex),
		URLTitle: "Live-Karte",
	}
	if photo != nil {
		// The terms of the photo API: name the photographer next to the image
		// and link the photo's page.
		lines = append(lines, joinNonEmpty(" ", "Foto:", photo.Photographer, "/ Planespotters.net"))
		msg.URL = photo.Link
		msg.URLTitle = "Foto auf Planespotters.net"
	}
	msg.Body = joinNonEmpty("\n", lines...)
	return msg
}

// mapURL opens the aircraft on adsb.lol's map.
func mapURL(hex string) string { return "https://adsb.lol/?icao=" + hex }

func testMessage() pushover.Message {
	return pushover.Message{
		Title:    "RareBirdAlert Test",
		Body:     "Die Pushover-Zugangsdaten funktionieren.\nSo sehen Benachrichtigungen von RareBirdAlert aus.",
		URL:      "https://adsb.lol/",
		URLTitle: "Live-Karte",
		Priority: -1,
	}
}

func configErrorMessage(err error, activeConfigKept bool) pushover.Message {
	consequence := "Die bisherige Konfiguration bleibt aktiv."
	if !activeConfigKept {
		consequence = "Es ist keine gültige Konfiguration aktiv, RareBirdAlert meldet solange nichts."
	}
	return pushover.Message{
		Title: "RareBirdAlert: config.yaml fehlerhaft",
		Body:  err.Error() + "\n" + consequence,
	}
}

func outageMessage(since time.Time, err error, loc *time.Location) pushover.Message {
	return pushover.Message{
		Title: "RareBirdAlert: Datenquellen nicht erreichbar",
		Body: fmt.Sprintf("Seit %s Uhr liefert keine Quelle Daten (%s). Anflüge werden solange nicht erkannt.",
			since.In(loc).Format("15:04"), err),
	}
}

func recoveryMessage(since, until time.Time, loc *time.Location) pushover.Message {
	return pushover.Message{
		Title: "RareBirdAlert: Datenquellen wieder erreichbar",
		Body: fmt.Sprintf("Ausfall von %s bis %s Uhr (%s). Anflüge in dieser Zeit wurden möglicherweise nicht gemeldet.",
			since.In(loc).Format("15:04"), until.In(loc).Format("15:04"), germanDuration(until.Sub(since))),
	}
}

func labelled(label, value string) string {
	if strings.TrimSpace(value) == "" {
		return ""
	}
	return label + " " + value
}

// rate describes the vertical rate, or says nothing if it is unknown.
func rate(ac aircraft.Aircraft) string {
	switch {
	case !ac.HasVRate:
		return "Sinkrate unbekannt"
	case ac.VRateFPM <= -100:
		return "sinkt " + thousands(-ac.VRateFPM) + " ft/min"
	case ac.VRateFPM >= 100:
		return "steigt " + thousands(ac.VRateFPM) + " ft/min"
	default:
		return "Höhe konstant"
	}
}

func germanDuration(d time.Duration) string {
	minutes := int(d.Round(time.Minute) / time.Minute)
	if minutes < 60 {
		return fmt.Sprintf("%d min", minutes)
	}
	return fmt.Sprintf("%d h %02d min", minutes/60, minutes%60)
}

// germanFloat formats with one decimal and a comma.
func germanFloat(f float64) string {
	return strings.Replace(strconv.FormatFloat(f, 'f', 1, 64), ".", ",", 1)
}

// thousands formats an integer with a dot as the thousands separator.
func thousands(n int) string {
	s := strconv.Itoa(n)
	neg := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(s, "-")
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "." + s[i:]
	}
	if neg {
		s = "-" + s
	}
	return s
}

func firstNonEmpty(parts ...string) string {
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			return p
		}
	}
	return ""
}

func joinNonEmpty(sep string, parts ...string) string {
	kept := parts[:0:0]
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, sep)
}

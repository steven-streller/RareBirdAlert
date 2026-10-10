package main

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"os"
	"strings"
	"text/tabwriter"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/airports"
	"rarebirdalert/internal/config"
	"rarebirdalert/internal/photos"
	"rarebirdalert/internal/pushover"
	"rarebirdalert/internal/source"
)

// commandConfig loads the config for a one-off command.
func commandConfig(set settings) (*config.Config, error) {
	data, err := os.ReadFile(set.ConfigPath)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, fmt.Errorf("%s nicht gefunden, siehe config/config.example.yaml", set.ConfigPath)
	}
	if err != nil {
		return nil, err
	}
	cfg, err := config.Parse(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", set.ConfigPath, err)
	}
	return cfg, nil
}

func fail(set settings, err error) int {
	fmt.Fprintln(set.Stderr, "rarebirdalert:", err)
	return 1
}

// cmdNow polls every enabled source once per airport and prints the aircraft
// inside the geofences, with the rule that matches.
func cmdNow(set settings) int {
	cfg, err := commandConfig(set)
	if err != nil {
		return fail(set, err)
	}
	ua := userAgent(set.Contact)
	sources := map[string]source.Source{
		source.NameADSBLol:   source.NewADSBv2(source.NameADSBLol, set.ADSBLolURL, ua),
		source.NameAirplanes: source.NewADSBv2(source.NameAirplanes, set.AirplanesURL, ua),
		source.NameOpenSky:   source.NewOpenSky(set.OpenSkyURL, set.OpenSkyTokenURL, set.OpenSkyID, set.OpenSkySecret, ua),
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	now := time.Now()
	fmt.Fprintf(set.Stdout, "Stand %s Uhr (%s), Quellen: %s, %d Einträge in der Watchlist\n",
		now.In(cfg.Location).Format("02.01.2006 15:04"), cfg.Location, strings.Join(cfg.Sources, ", "), cfg.Watch.Count())

	exit := 0
	for _, ap := range cfg.Airports {
		fmt.Fprintf(set.Stdout, "\n%s %s, Radius %g NM\n", ap.ICAO, ap.Name, ap.RadiusNM)

		var groups []aircraft.Group
		for i, name := range cfg.Sources {
			if i > 0 {
				time.Sleep(requestGap)
			}
			res, err := sources[name].Fetch(ctx, source.Area{Lat: ap.Lat, Lon: ap.Lon, RadiusNM: ap.RadiusNM + areaMarginNM})
			if err != nil {
				fmt.Fprintf(set.Stdout, "  %s: FEHLER %s\n", name, err)
				exit = 1
				continue
			}
			credits := ""
			if res.Credits >= 0 {
				credits = fmt.Sprintf(", %d Credits übrig", res.Credits)
			}
			fmt.Fprintf(set.Stdout, "  %s: %d Flugzeuge%s\n", name, len(res.Aircraft), credits)
			groups = append(groups, aircraft.Group{Source: name, Aircraft: res.Aircraft})
		}

		w := tabwriter.NewWriter(set.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "\n  HEX\tKENNZ.\tTYP\tRUFZEICHEN\tHÖHE ft\tRATE fpm\tENTF. NM\tQUELLEN\tTREFFER")
		shown := 0
		for _, ac := range aircraft.Merge(groups) {
			if !ac.HasPos {
				continue
			}
			dist := airports.DistanceNM(ap.Lat, ap.Lon, ac.Lat, ac.Lon)
			if dist > ap.RadiusNM {
				continue
			}
			shown++
			alt := "–"
			switch {
			case ac.OnGround:
				alt = "Boden"
			case ac.HasAlt:
				alt = fmt.Sprint(ac.AltFt)
			}
			vr := "–"
			if ac.HasVRate {
				vr = fmt.Sprint(ac.VRateFPM)
			}
			match := "–"
			if rule, value, ok := cfg.Watch.Match(ac); ok {
				match = ruleNames[rule] + " " + value
			}
			fmt.Fprintf(w, "  %s\t%s\t%s\t%s\t%s\t%s\t%.1f\t%s\t%s\n", ac.Hex, orDash(ac.Reg), orDash(ac.Type),
				orDash(ac.Callsign), alt, vr, dist, strings.Join(ac.Sources, "+"), match)
		}
		if shown == 0 {
			fmt.Fprintln(set.Stdout, "  keine Flugzeuge im Geofence")
			continue
		}
		_ = w.Flush()
	}
	return exit
}

// cmdAirports lists the configured airports, or looks up the given ICAO codes
// in the built-in table.
func cmdAirports(set settings, args []string) int {
	type row struct {
		icao, name string
		lat, lon   float64
		elev       int
		radius     string
	}
	var rows []row

	if len(args) == 0 {
		cfg, err := commandConfig(set)
		if err != nil {
			return fail(set, err)
		}
		for _, ap := range cfg.Airports {
			rows = append(rows, row{ap.ICAO, ap.Name, ap.Lat, ap.Lon, ap.ElevFt, fmt.Sprintf("%g NM", ap.RadiusNM)})
		}
	} else {
		for _, code := range args {
			ap, ok := airports.Lookup(code)
			if !ok {
				return fail(set, fmt.Errorf("%q ist nicht in der eingebauten Liste deutscher Flughäfen (%d Einträge)", code, airports.Count()))
			}
			rows = append(rows, row{ap.ICAO, ap.Name, ap.Lat, ap.Lon, ap.ElevFt, "–"})
		}
	}

	w := tabwriter.NewWriter(set.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "ICAO\tNAME\tBREITE\tLÄNGE\tHÖHE ft\tRADIUS")
	for _, r := range rows {
		fmt.Fprintf(w, "%s\t%s\t%.4f\t%.4f\t%d\t%s\n", r.icao, r.name, r.lat, r.lon, r.elev, r.radius)
	}
	_ = w.Flush()
	return 0
}

// cmdTest sends a test push, which verifies the Pushover credentials. With
// --hex it also looks up and attaches the photo of that aircraft.
func cmdTest(set settings, args []string) int {
	hex := ""
	for i := 0; i < len(args); i++ {
		switch {
		case args[i] == "--hex" && i+1 < len(args):
			i++
			hex = aircraft.NormalizeHex(args[i])
		case strings.HasPrefix(args[i], "--hex="):
			hex = aircraft.NormalizeHex(strings.TrimPrefix(args[i], "--hex="))
		default:
			return fail(set, fmt.Errorf("unbekannte Angabe %q, erlaubt ist --hex HEX", args[i]))
		}
		if hex == "" {
			return fail(set, errors.New("--hex erwartet einen ICAO24-Hexcode mit sechs Hexziffern, etwa 3c6444"))
		}
	}
	if set.Token == "" || set.User == "" {
		return fail(set, errors.New("PUSHOVER_TOKEN und PUSHOVER_USER müssen gesetzt sein"))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	ua := userAgent(set.Contact)

	msg := testMessage()
	if hex != "" {
		if set.Contact == "" && set.PlanespottersURL == "" {
			return fail(set, errors.New("RAREBIRDALERT_CONTACT muss gesetzt sein: Planespotters.net lehnt Anfragen ohne Kontakt im User-Agent ab"))
		}
		client := photos.New(set.PlanespottersURL, ua)
		photo, result, err := client.Lookup(ctx, hex, "")
		switch {
		case err != nil:
			return fail(set, err)
		case result == photos.ResultNotFound:
			fmt.Fprintf(set.Stdout, "Kein Foto für %s bei Planespotters.net, der Test-Push geht ohne Foto raus.\n", hex)
		default:
			data, contentType, err := client.Image(ctx, photo)
			if err != nil {
				return fail(set, err)
			}
			msg.Attachment, msg.AttachmentType = data, contentType
			msg.Body += "\nFoto: " + photo.Photographer + " / Planespotters.net"
			msg.URL, msg.URLTitle = photo.Link, "Foto auf Planespotters.net"
		}
	}

	receipt, err := pushover.New(set.Token, set.User, set.Device, set.PushoverURL, ua).Send(ctx, msg)
	if err != nil {
		return fail(set, err)
	}
	fmt.Fprintf(set.Stdout, "Test-Push gesendet (Pushover-Request %s).\n", receipt.Request)
	return 0
}

// cmdHealth asks the running daemon for its health. It exists because the
// image has neither a shell nor curl for a container health check.
func cmdHealth(set settings) int {
	if set.HTTPAddr == "" {
		return fail(set, errors.New("RAREBIRDALERT_HTTP_ADDR ist leer, der HTTP-Server ist abgeschaltet"))
	}
	host, port, err := net.SplitHostPort(set.HTTPAddr)
	if err != nil {
		return fail(set, fmt.Errorf("RAREBIRDALERT_HTTP_ADDR: %w", err))
	}
	if host == "" || host == "0.0.0.0" || host == "::" {
		host = "127.0.0.1"
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + net.JoinHostPort(host, port) + "/healthz")
	if err != nil {
		return fail(set, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fail(set, fmt.Errorf("/healthz antwortet mit %s", resp.Status))
	}
	fmt.Fprintln(set.Stdout, "ok")
	return 0
}

func orDash(s string) string {
	if s == "" {
		return "–"
	}
	return s
}

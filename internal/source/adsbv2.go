package source

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"rarebirdalert/internal/aircraft"
)

// Base URLs of the two services that speak the ADSBexchange v2 format.
const (
	ADSBLolURL   = "https://api.adsb.lol"
	AirplanesURL = "https://api.airplanes.live"
)

// ADSBv2 reads the "point" endpoint of an ADSBexchange-v2 compatible API:
// GET {base}/v2/point/{lat}/{lon}/{radius in NM}. adsb.lol and airplanes.live
// both offer it.
type ADSBv2 struct {
	name      string
	base      string
	userAgent string
	http      *http.Client
	now       func() time.Time
}

// NewADSBv2 returns a client. An empty base selects the public endpoint of
// the named service.
func NewADSBv2(name, base, userAgent string) *ADSBv2 {
	if base == "" {
		base = ADSBLolURL
		if name == NameAirplanes {
			base = AirplanesURL
		}
	}
	// A new connection for every request. Both services answer a connection
	// that is used again with 429 after a few requests, even at one request
	// every few seconds; fresh connections at the same pace pass. The polls
	// are far enough apart for the extra handshake not to matter.
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.DisableKeepAlives = true
	client := &http.Client{Timeout: 15 * time.Second, Transport: tr}
	return &ADSBv2{name: name, base: strings.TrimRight(base, "/"), userAgent: userAgent, http: client, now: time.Now}
}

// Name implements Source.
func (c *ADSBv2) Name() string { return c.name }

// maxRadiusNM is the largest radius both services accept.
const maxRadiusNM = 250

type adsbResponse struct {
	AC  *[]adsbAircraft `json:"ac"`
	Msg string          `json:"msg"`
	Now int64           `json:"now"` // Unix milliseconds
}

type adsbAircraft struct {
	Hex      string          `json:"hex"`
	Flight   string          `json:"flight"`
	Reg      string          `json:"r"`
	Type     string          `json:"t"`
	Desc     string          `json:"desc"`
	OwnOp    string          `json:"ownOp"`
	AltBaro  json.RawMessage `json:"alt_baro"` // number in feet, or the string "ground"
	GS       *float64        `json:"gs"`
	BaroRate *float64        `json:"baro_rate"`
	GeomRate *float64        `json:"geom_rate"`
	Lat      *float64        `json:"lat"`
	Lon      *float64        `json:"lon"`
	SeenPos  *float64        `json:"seen_pos"`
	Seen     *float64        `json:"seen"`
	DBFlags  int             `json:"dbFlags"`
}

// Fetch implements Source.
func (c *ADSBv2) Fetch(ctx context.Context, area Area) (Result, error) {
	radius := min(max(area.RadiusNM, 1), maxRadiusNM)
	url := fmt.Sprintf("%s/v2/point/%.4f/%.4f/%.0f", c.base, area.Lat, area.Lon, radius)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return Result{}, &Error{Kind: KindNetwork, Err: err}
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)

	resp, err := c.http.Do(req)
	if err != nil {
		return Result{}, transportError(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		// The body of a refusal explains what to do, so keep it in the error.
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 2<<10))
		e := statusError(resp)
		if msg := strings.TrimSpace(string(raw)); msg != "" {
			e.Err = fmt.Errorf("HTTP %d: %s", resp.StatusCode, msg)
		}
		return Result{}, e
	}

	var body adsbResponse
	if err := json.NewDecoder(io.LimitReader(resp.Body, maxResponseBytes)).Decode(&body); err != nil {
		return Result{}, &Error{Kind: KindInvalidResponse, Status: resp.StatusCode, Err: fmt.Errorf("decode response: %w", err)}
	}
	if body.AC == nil {
		return Result{}, &Error{Kind: KindInvalidResponse, Status: resp.StatusCode, Err: fmt.Errorf("response has no aircraft list (msg %q)", body.Msg)}
	}

	base := c.now()
	if body.Now > 0 {
		base = time.UnixMilli(body.Now)
	}
	out := make([]aircraft.Aircraft, 0, len(*body.AC))
	for _, in := range *body.AC {
		if a, ok := in.convert(c.name, base); ok {
			out = append(out, a)
		}
	}
	return Result{Aircraft: out, Credits: -1}, nil
}

func (in adsbAircraft) convert(source string, base time.Time) (aircraft.Aircraft, bool) {
	hex := aircraft.NormalizeHex(in.Hex)
	if hex == "" {
		return aircraft.Aircraft{}, false
	}
	a := aircraft.Aircraft{
		Hex:         hex,
		Callsign:    strings.ToUpper(strings.TrimSpace(in.Flight)),
		Reg:         strings.ToUpper(strings.TrimSpace(in.Reg)),
		Type:        strings.ToUpper(strings.TrimSpace(in.Type)),
		Desc:        strings.TrimSpace(in.Desc),
		Operator:    strings.TrimSpace(in.OwnOp),
		Military:    in.DBFlags&1 != 0,
		Interesting: in.DBFlags&2 != 0,
		Sources:     []string{source},
	}
	if in.Lat != nil && in.Lon != nil {
		a.Lat, a.Lon, a.HasPos = *in.Lat, *in.Lon, true
	}
	if in.GS != nil {
		a.GroundSpeedKt = *in.GS
	}

	alt := strings.TrimSpace(string(in.AltBaro))
	switch {
	case alt == `"ground"`:
		a.OnGround = true
	case alt != "" && alt != "null":
		var ft float64
		if err := json.Unmarshal(in.AltBaro, &ft); err == nil {
			a.AltFt, a.HasAlt = int(ft), true
		}
	}

	switch {
	case in.BaroRate != nil:
		a.VRateFPM, a.HasVRate = int(*in.BaroRate), true
	case in.GeomRate != nil:
		a.VRateFPM, a.HasVRate = int(*in.GeomRate), true
	}

	age := 0.0
	switch {
	case a.HasPos && in.SeenPos != nil:
		age = *in.SeenPos
	case in.Seen != nil:
		age = *in.Seen
	}
	a.SeenAt = base.Add(-time.Duration(age * float64(time.Second)))
	return a, true
}

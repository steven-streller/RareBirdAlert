package source

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"rarebirdalert/internal/aircraft"
)

// Endpoints of the OpenSky Network.
const (
	OpenSkyURL      = "https://opensky-network.org/api"
	OpenSkyTokenURL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)

// OpenSky credits per day, by account kind. The auto interval of the daemon
// is derived from them.
const (
	OpenSkyAnonymousCredits = 400
	OpenSkyStandardCredits  = 4000
)

const (
	metresToFeet     = 3.28084
	msToKnots        = 1.94384
	msToFeetPerMin   = 196.85
	defaultRetryWait = 10 * time.Minute
)

// OpenSky reads GET /states/all with a bounding box. Without credentials the
// API is used anonymously, with a much smaller daily quota.
//
// The state vectors carry no aircraft type or registration; those come from
// the other sources.
type OpenSky struct {
	base      string
	tokenURL  string
	clientID  string
	secret    string
	userAgent string
	http      *http.Client
	now       func() time.Time

	mu           sync.Mutex
	token        string
	tokenExpires time.Time
	blockedUntil time.Time
}

// NewOpenSky returns a client. Empty endpoints select the public ones; empty
// credentials select anonymous access.
func NewOpenSky(base, tokenURL, clientID, secret, userAgent string) *OpenSky {
	if base == "" {
		base = OpenSkyURL
	}
	if tokenURL == "" {
		tokenURL = OpenSkyTokenURL
	}
	return &OpenSky{
		base: strings.TrimRight(base, "/"), tokenURL: tokenURL,
		clientID: clientID, secret: secret, userAgent: userAgent,
		http: newHTTPClient(), now: time.Now,
	}
}

// Name implements Source.
func (c *OpenSky) Name() string { return NameOpenSky }

// Authenticated reports whether the client has credentials.
func (c *OpenSky) Authenticated() bool { return c.clientID != "" && c.secret != "" }

// DailyCredits is the quota of the account kind this client uses.
func (c *OpenSky) DailyCredits() int {
	if c.Authenticated() {
		return OpenSkyStandardCredits
	}
	return OpenSkyAnonymousCredits
}

// Fetch implements Source.
func (c *OpenSky) Fetch(ctx context.Context, area Area) (Result, error) {
	c.mu.Lock()
	blocked := c.blockedUntil
	c.mu.Unlock()
	if wait := blocked.Sub(c.now()); wait > 0 {
		return Result{}, &Error{Kind: KindRateLimited, Status: http.StatusTooManyRequests, RetryAfter: wait,
			Err: fmt.Errorf("quota exhausted, not asking again for %s", wait.Round(time.Second))}
	}

	dLat := area.RadiusNM / 60
	dLon := dLat / math.Max(0.1, math.Cos(area.Lat*math.Pi/180))
	q := url.Values{}
	q.Set("lamin", fmt.Sprintf("%.4f", area.Lat-dLat))
	q.Set("lamax", fmt.Sprintf("%.4f", area.Lat+dLat))
	q.Set("lomin", fmt.Sprintf("%.4f", area.Lon-dLon))
	q.Set("lomax", fmt.Sprintf("%.4f", area.Lon+dLon))
	endpoint := c.base + "/states/all?" + q.Encode()

	res, err := c.get(ctx, endpoint, true)
	if err != nil {
		return Result{}, err
	}
	return res, nil
}

func (c *OpenSky) get(ctx context.Context, endpoint string, retryOnAuth bool) (Result, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return Result{}, &Error{Kind: KindNetwork, Err: err}
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)
	if c.Authenticated() {
		token, err := c.bearer(ctx)
		if err != nil {
			return Result{}, err
		}
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return Result{}, transportError(err)
	}
	defer resp.Body.Close()

	credits := -1
	if v := resp.Header.Get("X-Rate-Limit-Remaining"); v != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			credits = n
		}
	}

	switch {
	case resp.StatusCode == http.StatusUnauthorized && c.Authenticated() && retryOnAuth:
		// The token expired earlier than announced; fetch a new one once.
		c.mu.Lock()
		c.token, c.tokenExpires = "", time.Time{}
		c.mu.Unlock()
		return c.get(ctx, endpoint, false)
	case resp.StatusCode == http.StatusTooManyRequests:
		wait := defaultRetryWait
		if secs, err := strconv.Atoi(strings.TrimSpace(resp.Header.Get("X-Rate-Limit-Retry-After-Seconds"))); err == nil && secs > 0 {
			wait = time.Duration(secs) * time.Second
		}
		c.mu.Lock()
		c.blockedUntil = c.now().Add(wait)
		c.mu.Unlock()
		e := statusError(resp)
		e.RetryAfter = wait
		return Result{Credits: 0}, e
	case resp.StatusCode != http.StatusOK:
		return Result{}, statusError(resp)
	}

	var body struct {
		Time   int64   `json:"time"`
		States [][]any `json:"states"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, maxResponseBytes)).Decode(&body); err != nil {
		return Result{}, &Error{Kind: KindInvalidResponse, Status: resp.StatusCode, Err: fmt.Errorf("decode response: %w", err)}
	}
	if body.Time == 0 {
		return Result{}, &Error{Kind: KindInvalidResponse, Status: resp.StatusCode, Err: errors.New("response has no time")}
	}

	out := make([]aircraft.Aircraft, 0, len(body.States))
	for _, s := range body.States {
		if a, ok := convertState(s, time.Unix(body.Time, 0)); ok {
			out = append(out, a)
		}
	}
	return Result{Aircraft: out, Credits: credits}, nil
}

// bearer returns a valid access token, fetching one with the client
// credentials flow if needed.
func (c *OpenSky) bearer(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.token != "" && c.now().Before(c.tokenExpires.Add(-time.Minute)) {
		return c.token, nil
	}

	form := url.Values{}
	form.Set("grant_type", "client_credentials")
	form.Set("client_id", c.clientID)
	form.Set("client_secret", c.secret)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.tokenURL, strings.NewReader(form.Encode()))
	if err != nil {
		return "", &Error{Kind: KindNetwork, Err: err}
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("User-Agent", c.userAgent)
	resp, err := c.http.Do(req)
	if err != nil {
		e := transportError(err)
		e.Err = fmt.Errorf("opensky token: %w", err)
		return "", e
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		e := statusError(resp)
		e.Err = fmt.Errorf("opensky token: HTTP %d (client id or secret wrong?)", resp.StatusCode)
		return "", e
	}
	var tok struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(&tok); err != nil || tok.AccessToken == "" {
		return "", &Error{Kind: KindInvalidResponse, Status: resp.StatusCode, Err: errors.New("opensky token: response has no access_token")}
	}
	if tok.ExpiresIn <= 0 {
		tok.ExpiresIn = 1800
	}
	c.token = tok.AccessToken
	c.tokenExpires = c.now().Add(time.Duration(tok.ExpiresIn) * time.Second)
	return c.token, nil
}

// convertState turns one state vector into an Aircraft. Index meaning: 0
// icao24, 1 callsign, 3 time_position, 4 last_contact, 5 longitude, 6
// latitude, 7 baro_altitude (m), 8 on_ground, 9 velocity (m/s), 11
// vertical_rate (m/s).
func convertState(s []any, fetched time.Time) (aircraft.Aircraft, bool) {
	if len(s) < 12 {
		return aircraft.Aircraft{}, false
	}
	hexStr, _ := s[0].(string)
	hex := aircraft.NormalizeHex(hexStr)
	if hex == "" {
		return aircraft.Aircraft{}, false
	}
	a := aircraft.Aircraft{Hex: hex, Sources: []string{NameOpenSky}, SeenAt: fetched}
	if cs, ok := s[1].(string); ok {
		a.Callsign = strings.ToUpper(strings.TrimSpace(cs))
	}
	lon, okLon := number(s[5])
	lat, okLat := number(s[6])
	if okLon && okLat {
		a.Lat, a.Lon, a.HasPos = lat, lon, true
	}
	if alt, ok := number(s[7]); ok {
		a.AltFt, a.HasAlt = int(math.Round(alt*metresToFeet)), true
	}
	a.OnGround, _ = s[8].(bool)
	if v, ok := number(s[9]); ok {
		a.GroundSpeedKt = v * msToKnots
	}
	if v, ok := number(s[11]); ok {
		a.VRateFPM, a.HasVRate = int(math.Round(v*msToFeetPerMin)), true
	}
	// time_position is when the position was last reported; fall back to
	// last_contact, then to the time of the response.
	for _, i := range []int{3, 4} {
		if t, ok := number(s[i]); ok && t > 0 {
			a.SeenAt = time.Unix(int64(t), 0)
			break
		}
	}
	return a, true
}

func number(v any) (float64, bool) {
	f, ok := v.(float64)
	return f, ok
}

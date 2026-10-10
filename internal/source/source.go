// Package source holds the clients of the aircraft position APIs. Each client
// turns the answer of its API into the common aircraft model.
package source

import (
	"context"
	"errors"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"rarebirdalert/internal/aircraft"
)

// Source names, as used in the config file, in logs and as metric labels.
const (
	NameADSBLol      = "adsb.lol"
	NameAirplanes    = "airplanes.live"
	NameOpenSky      = "opensky"
	maxResponseBytes = 8 << 20
)

// Names lists the known sources.
var Names = []string{NameADSBLol, NameAirplanes, NameOpenSky}

// Area is a circle on the map.
type Area struct {
	Lat, Lon float64
	RadiusNM float64
}

// Result is the answer of one successful poll.
type Result struct {
	Aircraft []aircraft.Aircraft
	// Credits is what the API says is left of the quota, -1 if it does not say.
	Credits int
}

// Source is a client of one position API.
type Source interface {
	Name() string
	Fetch(ctx context.Context, area Area) (Result, error)
}

// Error kinds, the "result" label of rarebirdalert_source_polls_total.
const (
	KindTimeout         = "timeout"
	KindNetwork         = "network"
	KindHTTPStatus      = "http_status"
	KindRateLimited     = "rate_limited"
	KindInvalidResponse = "invalid_response"
)

// ErrorKinds lists every kind an Error can have.
var ErrorKinds = []string{KindTimeout, KindNetwork, KindHTTPStatus, KindRateLimited, KindInvalidResponse}

// Error is a failed poll.
type Error struct {
	Kind       string
	Status     int           // HTTP status, 0 if there was no response
	RetryAfter time.Duration // set with KindRateLimited if the API said how long
	Err        error
}

func (e *Error) Error() string {
	if e.Err != nil {
		return e.Err.Error()
	}
	return e.Kind
}

func (e *Error) Unwrap() error { return e.Err }

// ErrorKind returns the kind of err, or KindNetwork for unknown errors.
func ErrorKind(err error) string {
	var se *Error
	if errors.As(err, &se) {
		return se.Kind
	}
	return KindNetwork
}

// RetryAfter returns how long the API asked to wait, 0 if it did not say.
func RetryAfter(err error) time.Duration {
	var se *Error
	if errors.As(err, &se) {
		return se.RetryAfter
	}
	return 0
}

// ErrorStatus returns the HTTP status behind err, 0 if there is none.
func ErrorStatus(err error) int {
	var se *Error
	if errors.As(err, &se) {
		return se.Status
	}
	return 0
}

// transportError classifies an error of http.Client.Do.
func transportError(err error) *Error {
	var ne net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &ne) && ne.Timeout()) {
		return &Error{Kind: KindTimeout, Err: err}
	}
	return &Error{Kind: KindNetwork, Err: err}
}

// statusError classifies a response that is not 200.
func statusError(resp *http.Response) *Error {
	if resp.StatusCode == http.StatusTooManyRequests {
		e := &Error{Kind: KindRateLimited, Status: resp.StatusCode, Err: errors.New("HTTP 429, rate limit reached")}
		if secs, err := strconv.Atoi(strings.TrimSpace(resp.Header.Get("Retry-After"))); err == nil && secs > 0 {
			e.RetryAfter = time.Duration(secs) * time.Second
		}
		return e
	}
	return &Error{Kind: KindHTTPStatus, Status: resp.StatusCode, Err: errors.New("HTTP " + resp.Status)}
}

func newHTTPClient() *http.Client {
	return &http.Client{Timeout: 15 * time.Second}
}

package source

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestADSBv2ParsesRealResponse(t *testing.T) {
	var path, ua string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path, ua = r.URL.Path, r.Header.Get("User-Agent")
		_, _ = w.Write(fixture(t, "adsbv2_point.json"))
	}))
	defer srv.Close()

	c := NewADSBv2(NameADSBLol, srv.URL, "RareBirdAlert/test (+me@example.org)")
	res, err := c.Fetch(context.Background(), Area{Lat: 53.0468, Lon: 8.7893, RadiusNM: 12})
	if err != nil {
		t.Fatal(err)
	}
	if path != "/v2/point/53.0468/8.7893/12" {
		t.Errorf("path = %s", path)
	}
	if !strings.Contains(ua, "me@example.org") {
		t.Errorf("User-Agent = %q", ua)
	}
	if len(res.Aircraft) != 7 || res.Credits != -1 {
		t.Fatalf("got %d aircraft, credits %d", len(res.Aircraft), res.Credits)
	}

	var cs *int
	for i, a := range res.Aircraft {
		if a.Hex == "484c53" {
			cs = &i
		}
	}
	if cs == nil {
		t.Fatal("KLM66J missing")
	}
	a := res.Aircraft[*cs]
	if a.Callsign != "KLM66J" || a.Reg != "PH-EZO" || a.Type != "E190" {
		t.Errorf("identity = %+v", a)
	}
	if !a.HasAlt || a.AltFt != 17650 || !a.HasVRate || a.VRateFPM != 3008 || !a.HasPos {
		t.Errorf("motion = %+v", a)
	}
	if a.Sources[0] != NameADSBLol {
		t.Errorf("sources = %v", a.Sources)
	}
}

func TestADSBv2GroundFlagsAndTISB(t *testing.T) {
	body := `{"ac":[
		{"hex":"3c6444","flight":"BGA1   ","t":"A3ST","alt_baro":"ground","gs":12.5,"lat":53.04,"lon":8.78,"seen_pos":1.5,"dbFlags":3},
		{"hex":"~abc123","alt_baro":100,"lat":1,"lon":1},
		{"hex":"zz","alt_baro":100}
	],"msg":"No error","now":1791614886001}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
	defer srv.Close()

	res, err := NewADSBv2(NameAirplanes, srv.URL, "ua").Fetch(context.Background(), Area{RadiusNM: 5})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Aircraft) != 1 {
		t.Fatalf("TIS-B and malformed addresses must be dropped, got %d", len(res.Aircraft))
	}
	a := res.Aircraft[0]
	if !a.OnGround || a.HasAlt || !a.Military || !a.Interesting || a.GroundSpeedKt != 12.5 {
		t.Errorf("aircraft = %+v", a)
	}
	want := time.UnixMilli(1791614886001).Add(-1500 * time.Millisecond)
	if !a.SeenAt.Equal(want) {
		t.Errorf("SeenAt = %v, want %v", a.SeenAt, want)
	}
}

func TestADSBv2Errors(t *testing.T) {
	cases := []struct {
		name    string
		handler http.HandlerFunc
		kind    string
		status  int
		text    string
	}{
		{"forbidden with explanation", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(403)
			_, _ = w.Write([]byte(`{"error":"Please contact us"}`))
		}, KindHTTPStatus, 403, "Please contact us"},
		{"rate limited", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(429) }, KindRateLimited, 429, ""},
		{"not json", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("<html>")) }, KindInvalidResponse, 200, ""},
		{"no aircraft list", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`{"msg":"boom"}`)) }, KindInvalidResponse, 200, "boom"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(tc.handler)
			defer srv.Close()
			_, err := NewADSBv2(NameADSBLol, srv.URL, "ua").Fetch(context.Background(), Area{RadiusNM: 5})
			if err == nil {
				t.Fatal("want an error")
			}
			if ErrorKind(err) != tc.kind || ErrorStatus(err) != tc.status {
				t.Errorf("kind %q status %d, want %q %d (%v)", ErrorKind(err), ErrorStatus(err), tc.kind, tc.status, err)
			}
			if tc.text != "" && !strings.Contains(err.Error(), tc.text) {
				t.Errorf("error %q lacks %q", err, tc.text)
			}
		})
	}
}

func TestADSBv2NetworkAndTimeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close()
	_, err := NewADSBv2(NameADSBLol, url, "ua").Fetch(context.Background(), Area{RadiusNM: 5})
	if ErrorKind(err) != KindNetwork {
		t.Errorf("closed server: kind %q (%v)", ErrorKind(err), err)
	}

	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { time.Sleep(300 * time.Millisecond) }))
	defer slow.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	_, err = NewADSBv2(NameADSBLol, slow.URL, "ua").Fetch(ctx, Area{RadiusNM: 5})
	if ErrorKind(err) != KindTimeout {
		t.Errorf("slow server: kind %q (%v)", ErrorKind(err), err)
	}
}

func TestOpenSkyAnonymousParsesRealResponse(t *testing.T) {
	var query string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		query = r.URL.RawQuery
		if r.Header.Get("Authorization") != "" {
			t.Error("anonymous requests carry no Authorization header")
		}
		w.Header().Set("X-Rate-Limit-Remaining", "399")
		_, _ = w.Write(fixture(t, "opensky_states.json"))
	}))
	defer srv.Close()

	c := NewOpenSky(srv.URL, "", "", "", "ua")
	res, err := c.Fetch(context.Background(), Area{Lat: 53.0468, Lon: 8.7893, RadiusNM: 12})
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"lamin=", "lamax=", "lomin=", "lomax="} {
		if !strings.Contains(query, key) {
			t.Errorf("query %q lacks %s", query, key)
		}
	}
	if res.Credits != 399 || len(res.Aircraft) != 1 {
		t.Fatalf("credits %d, aircraft %d", res.Credits, len(res.Aircraft))
	}
	a := res.Aircraft[0]
	if a.Hex != "502d5f" || a.Callsign != "BTI7HB" || a.Reg != "" || a.Type != "" {
		t.Errorf("identity = %+v", a)
	}
	// 10363.2 m = 34000 ft, 200.2 m/s = 389 kn, 0 m/s
	if a.AltFt != 34000 || a.OnGround || !a.HasVRate || a.VRateFPM != 0 {
		t.Errorf("motion = %+v", a)
	}
	if a.GroundSpeedKt < 388 || a.GroundSpeedKt > 390 {
		t.Errorf("speed = %v kn", a.GroundSpeedKt)
	}
	if a.SeenAt.Unix() != 1791614882 {
		t.Errorf("SeenAt = %v", a.SeenAt)
	}
}

func TestOpenSkyTokenFlowAndRateLimit(t *testing.T) {
	var tokens, states atomic.Int32
	mux := http.NewServeMux()
	mux.HandleFunc("/token", func(w http.ResponseWriter, r *http.Request) {
		tokens.Add(1)
		_ = r.ParseForm()
		if r.Form.Get("grant_type") != "client_credentials" || r.Form.Get("client_id") != "id" || r.Form.Get("client_secret") != "secret" {
			w.WriteHeader(401)
			return
		}
		_, _ = w.Write([]byte(`{"access_token":"tok","expires_in":1800}`))
	})
	mux.HandleFunc("/states/all", func(w http.ResponseWriter, r *http.Request) {
		n := states.Add(1)
		if r.Header.Get("Authorization") != "Bearer tok" {
			w.WriteHeader(401)
			return
		}
		if n >= 3 {
			w.Header().Set("X-Rate-Limit-Retry-After-Seconds", "120")
			w.WriteHeader(429)
			return
		}
		_, _ = w.Write([]byte(`{"time":1791614883,"states":null}`))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewOpenSky(srv.URL, srv.URL+"/token", "id", "secret", "ua")
	for i := 0; i < 2; i++ {
		res, err := c.Fetch(context.Background(), Area{Lat: 53, Lon: 8, RadiusNM: 10})
		if err != nil || len(res.Aircraft) != 0 {
			t.Fatalf("fetch %d: %v, %d aircraft", i, err, len(res.Aircraft))
		}
	}
	if tokens.Load() != 1 {
		t.Errorf("the token must be reused, fetched %d times", tokens.Load())
	}

	_, err := c.Fetch(context.Background(), Area{Lat: 53, Lon: 8, RadiusNM: 10})
	var se *Error
	if ErrorKind(err) != KindRateLimited {
		t.Fatalf("want rate_limited, got %v", err)
	}
	se, _ = err.(*Error)
	if se == nil || se.RetryAfter != 120*time.Second {
		t.Errorf("retry after = %+v", se)
	}
	before := states.Load()
	if _, err := c.Fetch(context.Background(), Area{Lat: 53, Lon: 8, RadiusNM: 10}); ErrorKind(err) != KindRateLimited {
		t.Errorf("blocked fetch: %v", err)
	}
	if states.Load() != before {
		t.Error("a blocked client must not ask again before the wait is over")
	}
}

func TestOpenSkyBadCredentials(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(401) }))
	defer srv.Close()
	c := NewOpenSky(srv.URL, srv.URL+"/token", "id", "wrong", "ua")
	_, err := c.Fetch(context.Background(), Area{Lat: 53, Lon: 8, RadiusNM: 10})
	if err == nil || ErrorStatus(err) != 401 || !strings.Contains(err.Error(), "token") {
		t.Fatalf("err = %v", err)
	}
}

package photos

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const found = `{"photos":[{"id":"1","thumbnail":{"src":"https://t.example/small.jpg"},
"thumbnail_large":{"src":"https://t.example/large.jpg"},"link":"https://www.planespotters.net/photo/1","photographer":"Max Muster"}]}`

func server(t *testing.T, hits *atomic.Int32, handler func(r *http.Request) (int, string)) *Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		status, body := handler(r)
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return New(srv.URL, "RareBirdAlert/test (+me@example.org)")
}

func TestLookupFoundAndCached(t *testing.T) {
	var hits atomic.Int32
	var path, ua string
	c := server(t, &hits, func(r *http.Request) (int, string) {
		path, ua = r.URL.Path, r.Header.Get("User-Agent")
		return 200, found
	})

	p, result, err := c.Lookup(context.Background(), "3C6444", "F-GSTA")
	if err != nil || result != ResultFound {
		t.Fatalf("Lookup = %v, %q, %v", p, result, err)
	}
	if p.ImageURL != "https://t.example/large.jpg" || p.Photographer != "Max Muster" || !strings.HasSuffix(p.Link, "/photo/1") {
		t.Errorf("photo = %+v", p)
	}
	if path != "/pub/photos/hex/3C6444" || !strings.Contains(ua, "me@example.org") {
		t.Errorf("request: %s, %q", path, ua)
	}

	_, result, _ = c.Lookup(context.Background(), "3c6444", "")
	if result != ResultCached || hits.Load() != 1 {
		t.Errorf("second lookup: %q after %d hits", result, hits.Load())
	}
}

func TestLookupFallsBackToRegistration(t *testing.T) {
	var hits atomic.Int32
	c := server(t, &hits, func(r *http.Request) (int, string) {
		if strings.Contains(r.URL.Path, "/hex/") {
			return 200, `{"photos":[]}`
		}
		return 200, found
	})
	_, result, err := c.Lookup(context.Background(), "3c6444", "F-GSTA")
	if err != nil || result != ResultFound || hits.Load() != 2 {
		t.Fatalf("result %q, hits %d, err %v", result, hits.Load(), err)
	}
}

func TestLookupNotFoundIsCachedErrorsAreShortLived(t *testing.T) {
	var hits atomic.Int32
	c := server(t, &hits, func(r *http.Request) (int, string) { return 200, `{"photos":[]}` })
	for i := 0; i < 3; i++ {
		if _, result, err := c.Lookup(context.Background(), "000001", ""); err != nil || result != ResultNotFound {
			t.Fatalf("lookup %d: %q %v", i, result, err)
		}
	}
	if hits.Load() != 1 {
		t.Errorf("negative answers must be cached, got %d hits", hits.Load())
	}

	var errHits atomic.Int32
	ec := server(t, &errHits, func(r *http.Request) (int, string) {
		return 403, `{"error":"User-Agent must include a contact"}`
	})
	_, result, err := ec.Lookup(context.Background(), "000002", "")
	if result != ResultError || err == nil || !strings.Contains(err.Error(), "contact") {
		t.Fatalf("403: %q %v", result, err)
	}
	// An error is remembered only briefly.
	now := time.Now()
	ec.now = func() time.Time { return now.Add(errorTTL + time.Second) }
	ec.Lookup(context.Background(), "000002", "")
	if errHits.Load() != 2 {
		t.Errorf("errors must be retried after a short while, got %d hits", errHits.Load())
	}
}

func TestImage(t *testing.T) {
	img := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/ok.jpg":
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte("jpegdata"))
		case "/html":
			w.Header().Set("Content-Type", "text/html")
			_, _ = w.Write([]byte("<html>"))
		default:
			w.WriteHeader(404)
		}
	}))
	defer img.Close()

	c := New("", "ua")
	c.http = img.Client()
	data, ct, err := c.Image(context.Background(), Photo{ImageURL: img.URL + "/ok.jpg"})
	if err != nil || string(data) != "jpegdata" || ct != "image/jpeg" {
		t.Fatalf("Image = %q, %q, %v", data, ct, err)
	}
	if _, _, err := c.Image(context.Background(), Photo{ImageURL: img.URL + "/html"}); err == nil {
		t.Error("non-image content must be refused")
	}
	if _, _, err := c.Image(context.Background(), Photo{ImageURL: img.URL + "/missing"}); err == nil {
		t.Error("404 must be an error")
	}
	if _, _, err := c.Image(context.Background(), Photo{ImageURL: "http://insecure.example/a.jpg"}); err == nil {
		t.Error("plain http must be refused")
	}
}

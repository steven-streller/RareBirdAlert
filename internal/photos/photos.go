// Package photos looks up aircraft photos at Planespotters.net.
//
// The photo API needs no key, but it refuses requests whose User-Agent does
// not contain a contact URL or e-mail address. Its terms require the
// photographer's name next to the photo and a plain link to the photo's page.
package photos

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// DefaultBase is the public API.
const DefaultBase = "https://api.planespotters.net"

// Lookup results, the "result" label of rarebirdalert_photo_lookups_total.
const (
	ResultFound    = "found"
	ResultNotFound = "not_found"
	ResultError    = "error"
	ResultCached   = "cached"
)

// Results lists every lookup result.
var Results = []string{ResultFound, ResultNotFound, ResultError, ResultCached}

// MaxImageBytes is Pushover's limit for an attachment.
const MaxImageBytes = 5 << 20

// How long answers are remembered.
const (
	foundTTL    = 7 * 24 * time.Hour
	notFoundTTL = 12 * time.Hour
	errorTTL    = 2 * time.Minute
)

// Photo is a photo of an aircraft.
type Photo struct {
	ImageURL     string // large thumbnail
	Link         string // the photo's page, to be linked
	Photographer string
}

// Client talks to the photo API and remembers answers.
type Client struct {
	base      string
	userAgent string
	http      *http.Client
	now       func() time.Time

	mu    sync.Mutex
	cache map[string]entry
}

type entry struct {
	photo   Photo
	found   bool
	expires time.Time
}

// New returns a client. An empty base selects the public API.
func New(base, userAgent string) *Client {
	if base == "" {
		base = DefaultBase
	}
	return &Client{
		base:      strings.TrimRight(base, "/"),
		userAgent: userAgent,
		http:      &http.Client{Timeout: 10 * time.Second},
		now:       time.Now,
		cache:     map[string]entry{},
	}
}

type apiResponse struct {
	Photos []struct {
		Thumbnail      struct{ Src string } `json:"thumbnail"`
		ThumbnailLarge struct{ Src string } `json:"thumbnail_large"`
		Link           string               `json:"link"`
		Photographer   string               `json:"photographer"`
	} `json:"photos"`
	Error string `json:"error"`
}

// Lookup finds a photo of the aircraft with the given ICAO24 hex, falling
// back to the registration. The result is one of the Result constants.
func (c *Client) Lookup(ctx context.Context, hex, reg string) (Photo, string, error) {
	key := strings.ToLower(hex)
	if e, ok := c.cached(key); ok {
		if e.found {
			return e.photo, ResultCached, nil
		}
		return Photo{}, ResultNotFound, nil
	}

	photo, found, err := c.fetch(ctx, "hex", hex)
	if err == nil && !found && reg != "" {
		photo, found, err = c.fetch(ctx, "reg", reg)
	}
	switch {
	case err != nil:
		c.store(key, entry{expires: c.now().Add(errorTTL)})
		return Photo{}, ResultError, err
	case !found:
		c.store(key, entry{expires: c.now().Add(notFoundTTL)})
		return Photo{}, ResultNotFound, nil
	}
	c.store(key, entry{photo: photo, found: true, expires: c.now().Add(foundTTL)})
	return photo, ResultFound, nil
}

func (c *Client) cached(key string) (entry, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.cache[key]
	if !ok || c.now().After(e.expires) {
		delete(c.cache, key)
		return entry{}, false
	}
	return e, true
}

func (c *Client) store(key string, e entry) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.cache[key] = e
}

func (c *Client) fetch(ctx context.Context, by, value string) (Photo, bool, error) {
	endpoint := fmt.Sprintf("%s/pub/photos/%s/%s", c.base, by, url.PathEscape(value))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return Photo{}, false, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)

	resp, err := c.http.Do(req)
	if err != nil {
		return Photo{}, false, err
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode == http.StatusNotFound {
		return Photo{}, false, nil
	}
	if resp.StatusCode != http.StatusOK {
		var body apiResponse
		_ = json.Unmarshal(raw, &body)
		if body.Error != "" {
			return Photo{}, false, fmt.Errorf("planespotters: HTTP %d: %s", resp.StatusCode, body.Error)
		}
		return Photo{}, false, fmt.Errorf("planespotters: HTTP %d", resp.StatusCode)
	}
	var body apiResponse
	if err := json.Unmarshal(raw, &body); err != nil {
		return Photo{}, false, fmt.Errorf("planespotters: decode response: %w", err)
	}
	for _, p := range body.Photos {
		img := p.ThumbnailLarge.Src
		if img == "" {
			img = p.Thumbnail.Src
		}
		if img == "" || p.Link == "" {
			continue
		}
		return Photo{ImageURL: img, Link: p.Link, Photographer: strings.TrimSpace(p.Photographer)}, true, nil
	}
	return Photo{}, false, nil
}

// Image downloads the image of a photo.
func (c *Client) Image(ctx context.Context, p Photo) (data []byte, contentType string, err error) {
	// Plain http is only accepted when the API endpoint itself was configured
	// as plain http, which is what a test server is.
	u, err := url.Parse(p.ImageURL)
	secure := u != nil && (u.Scheme == "https" || (u.Scheme == "http" && strings.HasPrefix(c.base, "http://")))
	if err != nil || !secure || u.Host == "" {
		return nil, "", fmt.Errorf("planespotters: unusable image URL %q", p.ImageURL)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.ImageURL, nil)
	if err != nil {
		return nil, "", err
	}
	req.Header.Set("User-Agent", c.userAgent)
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, "", fmt.Errorf("planespotters image: HTTP %d", resp.StatusCode)
	}
	contentType = resp.Header.Get("Content-Type")
	if !strings.HasPrefix(contentType, "image/") {
		return nil, "", fmt.Errorf("planespotters image: unexpected content type %q", contentType)
	}
	data, err = io.ReadAll(io.LimitReader(resp.Body, MaxImageBytes+1))
	if err != nil {
		return nil, "", err
	}
	if len(data) > MaxImageBytes {
		return nil, "", errors.New("planespotters image: larger than 5 MB")
	}
	return data, contentType, nil
}

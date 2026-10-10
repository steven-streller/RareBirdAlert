// Package pushover sends notifications through the Pushover message API
// (https://pushover.net/api).
package pushover

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// DefaultEndpoint is the message endpoint of the Pushover API.
const DefaultEndpoint = "https://api.pushover.net/1/messages.json"

// Limits of the API, in characters.
const (
	maxTitle    = 250
	maxMessage  = 1024
	maxURL      = 512
	maxURLTitle = 100
)

// Message is one push notification.
type Message struct {
	Title    string
	Body     string
	URL      string
	URLTitle string
	Priority int           // -2..1; emergency priority (2) is not supported
	Sound    string        // empty: the user's default sound
	TTL      time.Duration // 0: the message does not expire

	// Attachment is an image of at most 5 MB.
	Attachment     []byte
	AttachmentType string // e.g. image/jpeg
}

// Receipt is Pushover's acknowledgement of an accepted message.
type Receipt struct {
	Request string // id of the request, to quote when contacting Pushover's support
}

// Error is a failed send. Permanent failures must not be retried: Pushover
// blocks clients that keep repeating rejected requests.
type Error struct {
	Permanent bool
	Status    int      // HTTP status, 0 if no response was received
	Messages  []string // error texts from the API
	Err       error    // transport error, if any
}

func (e *Error) Error() string {
	switch {
	case e.Err != nil:
		return "pushover: " + e.Err.Error()
	case len(e.Messages) > 0:
		return fmt.Sprintf("pushover: HTTP %d: %s", e.Status, strings.Join(e.Messages, "; "))
	default:
		return fmt.Sprintf("pushover: HTTP %d", e.Status)
	}
}

func (e *Error) Unwrap() error { return e.Err }

// MentionsAttachment reports whether the API's error texts blame the
// attachment, as opposed to the credentials or the message.
func (e *Error) MentionsAttachment() bool {
	for _, m := range e.Messages {
		if strings.Contains(strings.ToLower(m), "attachment") {
			return true
		}
	}
	return false
}

// Client sends messages for one application token to one user or group key.
type Client struct {
	token     string
	user      string
	device    string
	endpoint  string
	userAgent string
	http      *http.Client
}

// New returns a client. device may be empty to reach all of the user's
// devices; an empty endpoint selects DefaultEndpoint.
func New(token, user, device, endpoint, userAgent string) *Client {
	if endpoint == "" {
		endpoint = DefaultEndpoint
	}
	return &Client{
		token:     token,
		user:      user,
		device:    device,
		endpoint:  endpoint,
		userAgent: userAgent,
		http:      &http.Client{Timeout: 30 * time.Second},
	}
}

// Send delivers m. A nil error means Pushover accepted and queued the message.
// A message with an attachment is sent as multipart/form-data.
func (c *Client) Send(ctx context.Context, m Message) (Receipt, error) {
	fields := [][2]string{
		{"token", c.token},
		{"user", c.user},
		{"message", truncate(m.Body, maxMessage)},
	}
	if m.Title != "" {
		fields = append(fields, [2]string{"title", truncate(m.Title, maxTitle)})
	}
	if c.device != "" {
		fields = append(fields, [2]string{"device", c.device})
	}
	if m.URL != "" && utf8.RuneCountInString(m.URL) <= maxURL {
		fields = append(fields, [2]string{"url", m.URL})
		if m.URLTitle != "" {
			fields = append(fields, [2]string{"url_title", truncate(m.URLTitle, maxURLTitle)})
		}
	}
	if m.Priority != 0 {
		fields = append(fields, [2]string{"priority", strconv.Itoa(m.Priority)})
	}
	if m.Sound != "" {
		fields = append(fields, [2]string{"sound", m.Sound})
	}
	if ttl := int64(m.TTL / time.Second); ttl > 0 {
		fields = append(fields, [2]string{"ttl", strconv.FormatInt(ttl, 10)})
	}

	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	for _, f := range fields {
		if err := mw.WriteField(f[0], f[1]); err != nil {
			return Receipt{}, &Error{Permanent: true, Err: err}
		}
	}
	if len(m.Attachment) > 0 {
		contentType := m.AttachmentType
		if contentType == "" {
			contentType = "application/octet-stream"
		}
		h := textproto.MIMEHeader{}
		h.Set("Content-Disposition", `form-data; name="attachment"; filename="photo"`)
		h.Set("Content-Type", contentType)
		part, err := mw.CreatePart(h)
		if err == nil {
			_, err = part.Write(m.Attachment)
		}
		if err != nil {
			return Receipt{}, &Error{Permanent: true, Err: err}
		}
	}
	if err := mw.Close(); err != nil {
		return Receipt{}, &Error{Permanent: true, Err: err}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint, &body)
	if err != nil {
		return Receipt{}, &Error{Permanent: true, Err: err}
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	if c.userAgent != "" {
		req.Header.Set("User-Agent", c.userAgent)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return Receipt{}, &Error{Err: err}
	}
	defer resp.Body.Close()

	var reply struct {
		Status  int      `json:"status"`
		Request string   `json:"request"`
		Errors  []string `json:"errors"`
	}
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	_ = json.Unmarshal(raw, &reply)

	switch {
	case resp.StatusCode == http.StatusOK && reply.Status == 1:
		return Receipt{Request: reply.Request}, nil
	case resp.StatusCode >= 400 && resp.StatusCode < 500, resp.StatusCode == http.StatusOK:
		// Invalid input, wrong credentials or an exhausted quota (429):
		// repeating the same request cannot succeed.
		return Receipt{Request: reply.Request}, &Error{Permanent: true, Status: resp.StatusCode, Messages: reply.Errors}
	default:
		return Receipt{Request: reply.Request}, &Error{Status: resp.StatusCode, Messages: reply.Errors}
	}
}

// truncate shortens s to at most max characters, ending in an ellipsis.
func truncate(s string, max int) string {
	if utf8.RuneCountInString(s) <= max {
		return s
	}
	runes := []rune(s)
	return string(runes[:max-1]) + "…"
}

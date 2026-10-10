package pushover

import (
	"context"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type received struct {
	fields     map[string]string
	attachment []byte
	attachType string
}

func capture(t *testing.T, status int, body string) (*httptest.Server, *received) {
	t.Helper()
	got := &received{fields: map[string]string{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mt, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || mt != "multipart/form-data" {
			t.Errorf("content type %q", r.Header.Get("Content-Type"))
		}
		mr := multipart.NewReader(r.Body, params["boundary"])
		for {
			p, err := mr.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				t.Fatal(err)
			}
			data, _ := io.ReadAll(p)
			if p.FormName() == "attachment" {
				got.attachment, got.attachType = data, p.Header.Get("Content-Type")
				continue
			}
			got.fields[p.FormName()] = string(data)
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv, got
}

func TestSendWithAttachment(t *testing.T) {
	srv, got := capture(t, 200, `{"status":1,"request":"abc"}`)
	c := New("tok", "usr", "phone", srv.URL, "ua")
	receipt, err := c.Send(context.Background(), Message{
		Title: "A3ST im Anflug auf Bremen", Body: "text", URL: "https://example.org/p", URLTitle: "Foto",
		Priority: -1, Sound: "siren", TTL: 30 * time.Minute,
		Attachment: []byte("\xff\xd8jpeg"), AttachmentType: "image/jpeg",
	})
	if err != nil || receipt.Request != "abc" {
		t.Fatalf("Send = %v, %v", receipt, err)
	}
	want := map[string]string{
		"token": "tok", "user": "usr", "device": "phone", "message": "text", "title": "A3ST im Anflug auf Bremen",
		"url": "https://example.org/p", "url_title": "Foto", "priority": "-1", "sound": "siren", "ttl": "1800",
	}
	for k, v := range want {
		if got.fields[k] != v {
			t.Errorf("field %s = %q, want %q", k, got.fields[k], v)
		}
	}
	if string(got.attachment) != "\xff\xd8jpeg" || got.attachType != "image/jpeg" {
		t.Errorf("attachment = %q (%s)", got.attachment, got.attachType)
	}
}

func TestSendWithoutAttachmentOmitsOptionalFields(t *testing.T) {
	srv, got := capture(t, 200, `{"status":1,"request":"x"}`)
	if _, err := New("t", "u", "", srv.URL, "").Send(context.Background(), Message{Body: "hi"}); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"title", "device", "url", "priority", "sound", "ttl"} {
		if _, ok := got.fields[k]; ok {
			t.Errorf("field %s must be omitted", k)
		}
	}
	if got.attachment != nil {
		t.Error("no attachment expected")
	}
}

func TestSendErrorClassification(t *testing.T) {
	cases := []struct {
		name      string
		status    int
		body      string
		permanent bool
	}{
		{"invalid token", 400, `{"status":0,"errors":["application token is invalid"]}`, true},
		{"quota", 429, `{"status":0,"errors":["quota"]}`, true},
		{"server error", 503, ``, false},
		{"200 but status 0", 200, `{"status":0,"errors":["odd"]}`, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, _ := capture(t, tc.status, tc.body)
			_, err := New("t", "u", "", srv.URL, "").Send(context.Background(), Message{Body: "hi"})
			perr, ok := err.(*Error)
			if !ok || perr.Permanent != tc.permanent || perr.Status != tc.status {
				t.Fatalf("err = %#v", err)
			}
		})
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close()
	_, err := New("t", "u", "", url, "").Send(context.Background(), Message{Body: "hi"})
	if perr, ok := err.(*Error); !ok || perr.Permanent || perr.Status != 0 {
		t.Fatalf("transport error = %#v", err)
	}
}

func TestTruncate(t *testing.T) {
	long := strings.Repeat("ä", 2000)
	srv, got := capture(t, 200, `{"status":1}`)
	if _, err := New("t", "u", "", srv.URL, "").Send(context.Background(), Message{Body: long, Title: long}); err != nil {
		t.Fatal(err)
	}
	if n := len([]rune(got.fields["message"])); n != maxMessage {
		t.Errorf("message has %d characters", n)
	}
	if n := len([]rune(got.fields["title"])); n != maxTitle {
		t.Errorf("title has %d characters", n)
	}
}

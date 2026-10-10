package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"math"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/config"
	"rarebirdalert/internal/engine"
	"rarebirdalert/internal/obs"
)

const rigConfig = `
airports:
  - icao: EDDW
sources: [adsb.lol]
watch:
  types: [A3ST]
`

// step is where the Beluga is in one cycle of a scripted approach.
type step struct {
	distNM float64
	altFt  int
	vrate  int
	ground bool
}

var approachScript = []step{
	{8, 2500, -700, false},
	{7, 2200, -700, false},
	{5, 1500, -700, false},
	{1, 0, 0, true},
	{0.5, 0, 0, true},
}

type push struct {
	fields     map[string]string
	attachment []byte
}

type rig struct {
	t      *testing.T
	now    time.Time
	script []step
	index  int
	app    *app
	logs   *bytes.Buffer
	set    settings

	mu         sync.Mutex
	pushes     []push
	pushStatus func(attempt int) int
	// rejectAttachment makes Pushover refuse messages with an attachment.
	rejectAttachment bool
	attempts         int
	photoHits        int

	adsbCalls  int
	adsbStatus func(call int) int
}

func (r *rig) adsb(w http.ResponseWriter, req *http.Request) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.adsbCalls++
	if r.adsbStatus != nil {
		if status := r.adsbStatus(r.adsbCalls); status != 200 {
			w.WriteHeader(status)
			_, _ = w.Write([]byte("<html>429 Too Many Requests</html>"))
			return
		}
	}
	s := r.script[r.index]
	lat := 53.0468
	lon := 8.7893 - s.distNM/(60*math.Cos(lat*math.Pi/180))
	alt := fmt.Sprint(s.altFt)
	if s.ground {
		alt = `"ground"`
	}
	fmt.Fprintf(w, `{"ac":[
		{"hex":"3c6444","flight":"BGA123  ","r":"F-GSTA","t":"A3ST","desc":"AIRBUS A-300B4-608ST Beluga","alt_baro":%s,"baro_rate":%d,"gs":150,"lat":%.5f,"lon":%.5f,"seen_pos":0.5},
		{"hex":"4ac9c4","flight":"BLX251  ","r":"SE-RND","t":"B38M","alt_baro":2000,"baro_rate":-700,"lat":%.5f,"lon":%.5f,"seen_pos":0.2}
	],"msg":"No error","now":%d}`, alt, s.vrate, lat, lon, lat, lon, r.now.UnixMilli())
}

func (r *rig) pushover(w http.ResponseWriter, req *http.Request) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.attempts++
	_, params, _ := mime.ParseMediaType(req.Header.Get("Content-Type"))
	mr := multipart.NewReader(req.Body, params["boundary"])
	p := push{fields: map[string]string{}}
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			r.t.Error(err)
			break
		}
		data, _ := io.ReadAll(part)
		if part.FormName() == "attachment" {
			p.attachment = data
		} else {
			p.fields[part.FormName()] = string(data)
		}
	}
	if r.rejectAttachment && p.attachment != nil {
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"status":0,"errors":["attachment is not a valid image"]}`))
		return
	}
	if r.pushStatus != nil {
		if status := r.pushStatus(r.attempts); status != 200 {
			w.WriteHeader(status)
			return
		}
	}
	r.pushes = append(r.pushes, p)
	_, _ = w.Write([]byte(`{"status":1,"request":"req-1"}`))
}

type rigOption func(*rig)

func newRig(t *testing.T, opts ...rigOption) *rig {
	t.Helper()
	r := &rig{t: t, now: time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC), script: approachScript, logs: &bytes.Buffer{}}
	for _, o := range opts {
		o(r)
	}

	adsb := httptest.NewServer(http.HandlerFunc(r.adsb))
	pushSrv := httptest.NewServer(http.HandlerFunc(r.pushover))
	var photoSrv *httptest.Server
	photoSrv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		switch {
		case strings.HasPrefix(req.URL.Path, "/pub/photos/hex/"):
			r.mu.Lock()
			r.photoHits++
			r.mu.Unlock()
			fmt.Fprintf(w, `{"photos":[{"thumbnail_large":{"src":"%s/img.jpg"},"link":"https://www.planespotters.net/photo/42","photographer":"Max Muster"}]}`, photoSrv.URL)
		case req.URL.Path == "/img.jpg":
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte("JPEGDATA"))
		default:
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(func() { adsb.Close(); pushSrv.Close(); photoSrv.Close() })

	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(cfgPath, []byte(rigConfig), 0o644); err != nil {
		t.Fatal(err)
	}
	r.set = settings{
		Token: "tok", User: "usr", Contact: "me@example.org",
		ConfigPath: cfgPath, StatePath: filepath.Join(dir, "state.json"),
		ADSBLolURL: adsb.URL, PushoverURL: pushSrv.URL, PlanespottersURL: photoSrv.URL,
		Stdout: io.Discard, Stderr: io.Discard,
	}
	return r
}

// start creates the app, as on a process start.
func (r *rig) start() {
	r.t.Helper()
	logger, err := obs.NewLogger(r.logs, "json", "debug")
	if err != nil {
		r.t.Fatal(err)
	}
	r.app = newApp(r.set, logger, obs.NewMetrics("test"), func() time.Time { return r.now })
	r.app.restoreState()
}

// cycle runs one cycle in which the aircraft is at script position i.
func (r *rig) cycle(i int) {
	r.t.Helper()
	r.mu.Lock()
	r.index = i
	r.now = r.now.Add(15 * time.Second)
	r.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	r.app.cycle(ctx)
	if _, err := r.app.store.Save(r.app.state, r.now); err != nil {
		r.t.Fatal(err)
	}
}

func (r *rig) metrics() string {
	var buf bytes.Buffer
	r.app.met.WritePrometheus(&buf)
	return buf.String()
}

func (r *rig) wantMetric(line string) {
	r.t.Helper()
	if !strings.Contains(r.metrics(), line+"\n") {
		r.t.Errorf("metric %q missing or different in:\n%s", line, grep(r.metrics(), "rarebirdalert_"))
	}
}

func grep(text, prefix string) string {
	var out []string
	for _, l := range strings.Split(text, "\n") {
		if strings.HasPrefix(l, prefix) {
			out = append(out, l)
		}
	}
	return strings.Join(out, "\n")
}

func TestScriptedApproachSendsApproachAndLandingPush(t *testing.T) {
	r := newRig(t)
	r.start()
	for i := range approachScript {
		r.cycle(i)
	}

	if len(r.pushes) != 2 {
		t.Fatalf("want 2 pushes, got %d: %+v", len(r.pushes), r.pushes)
	}
	approach, landed := r.pushes[0], r.pushes[1]

	if approach.fields["title"] != "A3ST im Anflug auf Bremen" {
		t.Errorf("approach title = %q", approach.fields["title"])
	}
	for _, want := range []string{"AIRBUS A-300B4-608ST Beluga", "Kennzeichen F-GSTA", "Rufzeichen BGA123", "Treffer: Typ A3ST",
		"Quellen: adsb.lol", "Foto: Max Muster / Planespotters.net", "sinkt 700 ft/min"} {
		if !strings.Contains(approach.fields["message"], want) {
			t.Errorf("approach message lacks %q:\n%s", want, approach.fields["message"])
		}
	}
	if string(approach.attachment) != "JPEGDATA" {
		t.Errorf("approach attachment = %q", approach.attachment)
	}
	if approach.fields["url"] != "https://www.planespotters.net/photo/42" || approach.fields["url_title"] != "Foto auf Planespotters.net" {
		t.Errorf("approach link = %q %q", approach.fields["url"], approach.fields["url_title"])
	}
	if approach.fields["ttl"] != "1800" {
		t.Errorf("approach ttl = %q", approach.fields["ttl"])
	}

	if landed.fields["title"] != "A3ST in Bremen gelandet" || landed.fields["priority"] != "-1" {
		t.Errorf("landed push = %v", landed.fields)
	}
	if r.photoHits != 1 {
		t.Errorf("the photo must be looked up once and then come from the cache, got %d lookups", r.photoHits)
	}

	r.wantMetric(`rarebirdalert_notifications_total{kind="approach",result="sent",airport="EDDW"} 1`)
	r.wantMetric(`rarebirdalert_notifications_total{kind="landed",result="sent",airport="EDDW"} 1`)
	r.wantMetric(`rarebirdalert_approaches_total{airport="EDDW",rule="type"} 1`)
	r.wantMetric(`rarebirdalert_landings_total{airport="EDDW",detection="observed"} 1`)
	r.wantMetric(`rarebirdalert_photo_lookups_total{result="found"} 1`)
	r.wantMetric(`rarebirdalert_photo_lookups_total{result="cached"} 1`)
	r.wantMetric(`rarebirdalert_source_polls_total{source="adsb.lol",result="ok"} 5`)
	r.wantMetric(`rarebirdalert_airport_watched_aircraft{airport="EDDW"} 1`)
	r.wantMetric(`rarebirdalert_airport_aircraft{airport="EDDW"} 2`)
	r.wantMetric(`rarebirdalert_config_airports 1`)

	logs := r.logs.String()
	for _, event := range []string{`"event":"config.loaded"`, `"event":"aircraft.approach"`, `"decision":"notify_approach"`,
		`"event":"aircraft.landed"`, `"event":"notification.sent"`, `"event":"photo.found"`} {
		if !strings.Contains(logs, event) {
			t.Errorf("log lacks %s", event)
		}
	}
	if strings.Contains(logs, "tok") {
		t.Error("the Pushover token must never be logged")
	}
}

func TestRestartDuringApproachDoesNotRepeatThePush(t *testing.T) {
	r := newRig(t)
	r.start()
	r.cycle(0)
	r.cycle(1)
	r.cycle(2)
	if len(r.pushes) != 1 {
		t.Fatalf("want the approach push, got %d", len(r.pushes))
	}

	// New process, same state file.
	r.start()
	r.cycle(2)
	r.cycle(3)
	if len(r.pushes) != 2 || r.pushes[1].fields["title"] != "A3ST in Bremen gelandet" {
		t.Fatalf("after the restart want only the landing push, got %d pushes: %+v", len(r.pushes), r.pushes)
	}
}

func TestTransientPushFailureIsRetriedOnTheNextCycle(t *testing.T) {
	r := newRig(t)
	r.pushStatus = func(attempt int) int {
		if attempt == 1 {
			return 503
		}
		return 200
	}
	r.start()
	r.cycle(0)
	r.cycle(1)
	if len(r.pushes) != 0 {
		t.Fatalf("first attempt fails, got %d pushes", len(r.pushes))
	}
	r.cycle(2)
	if len(r.pushes) != 1 || !strings.Contains(r.pushes[0].fields["title"], "im Anflug") {
		t.Fatalf("retry must deliver the approach push, got %+v", r.pushes)
	}
	r.wantMetric(`rarebirdalert_notifications_total{kind="approach",result="failed",airport="EDDW"} 1`)
	r.wantMetric(`rarebirdalert_notifications_total{kind="approach",result="sent",airport="EDDW"} 1`)
}

func TestRejectedPushIsNotRepeated(t *testing.T) {
	r := newRig(t)
	r.pushStatus = func(int) int { return 400 }
	r.start()
	for i := range approachScript {
		r.cycle(i)
	}
	// One attempt for the approach, one for the landing, none after that:
	// Pushover blocks clients that repeat rejected requests.
	if r.attempts != 2 {
		t.Errorf("want 2 attempts, got %d", r.attempts)
	}
	r.wantMetric(`rarebirdalert_notifications_total{kind="approach",result="rejected",airport="EDDW"} 1`)
	r.wantMetric(`rarebirdalert_notifications_total{kind="landed",result="rejected",airport="EDDW"} 1`)
}

func TestRejectedPhotoCostsOnlyThePhoto(t *testing.T) {
	r := newRig(t)
	r.rejectAttachment = true
	r.start()
	for i := range approachScript {
		r.cycle(i)
	}
	if len(r.pushes) != 2 {
		t.Fatalf("want both pushes without photo, got %d", len(r.pushes))
	}
	for _, p := range r.pushes {
		if p.attachment != nil {
			t.Error("the retry must not carry the photo")
		}
	}
	r.wantMetric(`rarebirdalert_notifications_total{kind="approach",result="sent",airport="EDDW"} 1`)
}

func TestDryRunDecidesButDoesNotPush(t *testing.T) {
	r := newRig(t)
	r.set.DryRun = true
	r.set.Token, r.set.User = "", ""
	r.start()
	for i := range approachScript {
		r.cycle(i)
	}
	if r.attempts != 0 {
		t.Fatalf("dry run sent %d pushes", r.attempts)
	}
	r.wantMetric(`rarebirdalert_notifications_total{kind="approach",result="dry_run",airport="EDDW"} 1`)
	if !strings.Contains(r.logs.String(), `"event":"notification.dry_run"`) {
		t.Error("dry run must log what it would have sent")
	}
}

func TestWithoutContactThereIsNoPhoto(t *testing.T) {
	r := newRig(t)
	r.set.Contact, r.set.PlanespottersURL = "", ""
	r.start()
	for i := range approachScript {
		r.cycle(i)
	}
	if len(r.pushes) != 2 {
		t.Fatalf("want 2 pushes, got %d", len(r.pushes))
	}
	for _, p := range r.pushes {
		if p.attachment != nil || strings.Contains(p.fields["message"], "Planespotters") {
			t.Errorf("no photo expected: %+v", p)
		}
		if !strings.HasPrefix(p.fields["url"], "https://adsb.lol/?icao=3c6444") {
			t.Errorf("fallback link = %q", p.fields["url"])
		}
	}
}

func TestSourceOutageIsReportedOnceAndRecovery(t *testing.T) {
	r := newRig(t)
	r.start()
	r.cycle(0)

	// Shut the source down by pointing the client at a closed server.
	dead := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	deadURL := dead.URL
	dead.Close()
	r.set.ADSBLolURL = deadURL
	good := r.app
	r.start()
	r.app.state = good.state

	for i := 0; i < 5; i++ {
		r.cycle(0)
	}
	if len(r.pushes) != 0 {
		t.Fatalf("no outage push before the threshold, got %d", len(r.pushes))
	}
	r.now = r.now.Add(20 * time.Minute)
	r.cycle(0)
	r.cycle(0)
	if len(r.pushes) != 1 || !strings.Contains(r.pushes[0].fields["title"], "nicht erreichbar") {
		t.Fatalf("want one outage push, got %+v", r.pushes)
	}
	r.wantMetric(`rarebirdalert_notifications_total{kind="outage",result="sent"} 1`)
	if !strings.Contains(r.logs.String(), `"event":"source.poll_failed"`) ||
		!strings.Contains(r.logs.String(), `"event":"upstream.outage"`) {
		t.Error("outage must be logged")
	}
}

func TestInvalidConfigKeepsLastGoodAndAlerts(t *testing.T) {
	r := newRig(t)
	r.start()
	r.cycle(3) // on the ground, nothing to report
	if err := os.WriteFile(r.set.ConfigPath, []byte("airports: [{icao: EDDW}]\nwatchh: 1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	r.cycle(3)
	r.wantMetric(`rarebirdalert_config_last_reload_successful 0`)
	r.wantMetric(`rarebirdalert_config_airports 1`)
	if len(r.pushes) != 1 || !strings.Contains(r.pushes[0].fields["title"], "config.yaml fehlerhaft") {
		t.Fatalf("want a config error push, got %+v", r.pushes)
	}
	if !strings.Contains(r.pushes[0].fields["message"], "bleibt aktiv") ||
		!strings.Contains(r.pushes[0].fields["message"], "watchh") {
		t.Errorf("message = %q", r.pushes[0].fields["message"])
	}
}

func TestHelpAndUnknownCommand(t *testing.T) {
	var out, errOut bytes.Buffer
	set := settings{Stdout: &out, Stderr: &errOut}
	if code := run([]string{"help"}, set); code != 0 || !strings.Contains(out.String(), "Aufruf: rarebirdalert") {
		t.Errorf("help: %d %q", code, out.String())
	}
	if code := run([]string{"bogus"}, set); code != 2 || !strings.Contains(errOut.String(), "bogus") {
		t.Errorf("unknown: %d %q", code, errOut.String())
	}
	out.Reset()
	if code := run([]string{"version"}, set); code != 0 || strings.TrimSpace(out.String()) != version {
		t.Errorf("version: %d %q", code, out.String())
	}
}

func TestAirportsCommandLooksUpCodes(t *testing.T) {
	var out, errOut bytes.Buffer
	set := settings{Stdout: &out, Stderr: &errOut}
	if code := run([]string{"airports", "EDDW", "EDDV"}, set); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut.String())
	}
	if !strings.Contains(out.String(), "Bremen Airport") || !strings.Contains(out.String(), "Hannover Airport") {
		t.Errorf("output = %s", out.String())
	}
	if code := run([]string{"airports", "KJFK"}, set); code != 1 {
		t.Errorf("unknown airport: exit %d", code)
	}
}

func TestRateLimitedSourceIsHeldBackAndResumes(t *testing.T) {
	r := newRig(t)
	r.adsbStatus = func(call int) int {
		if call == 1 {
			return 429
		}
		return 200
	}
	r.start()

	r.cycle(0) // refused
	if r.adsbCalls != 1 {
		t.Fatalf("calls = %d", r.adsbCalls)
	}
	r.cycle(0) // 15 s later: still inside the 30 s back-off
	if r.adsbCalls != 1 {
		t.Fatalf("a rate-limited source must not be asked again inside the back-off, calls = %d", r.adsbCalls)
	}
	r.cycle(0) // 30 s after the refusal
	if r.adsbCalls != 2 {
		t.Fatalf("the source must be asked again once the back-off is over, calls = %d", r.adsbCalls)
	}

	r.wantMetric(`rarebirdalert_source_polls_total{source="adsb.lol",result="rate_limited"} 1`)
	r.wantMetric(`rarebirdalert_source_polls_total{source="adsb.lol",result="ok"} 1`)
	logs := r.logs.String()
	if !strings.Contains(logs, `"error_kind":"rate_limited"`) || !strings.Contains(logs, `"retry_in_seconds":30`) {
		t.Errorf("the refusal and its back-off must be logged:\n%s", logs)
	}
	if !strings.Contains(logs, `"event":"source.recovered"`) {
		t.Error("recovery must be logged")
	}
}

func TestNoveltyAndMilitaryMessages(t *testing.T) {
	cfg, err := config.Parse([]byte(`
airports: [{icao: EDDW}]
watch: {military: true}
novelty: {enabled: true}
notify:
  approach: {priority: 1}
  novelty: {priority: -2}
`))
	if err != nil {
		t.Fatal(err)
	}
	ap, _ := cfg.Airport("EDDW")
	base := engine.Notification{Kind: engine.KindApproach, Airport: ap, DistNM: 6.2, AGLFt: 1800,
		Aircraft: aircraft.Aircraft{Hex: "ae080b", Type: "C17", Sources: []string{"adsb.lol"}}}

	mil := base
	mil.Rule, mil.RuleValue = config.RuleMilitary, "C17"
	msg := aircraftMessage(mil, cfg, nil)
	if msg.Priority != 1 || !strings.Contains(msg.Body, "Treffer: Militär C17") {
		t.Errorf("military = priority %d, body:\n%s", msg.Priority, msg.Body)
	}

	nov := base
	nov.Rule, nov.RuleValue = config.RuleNovelty, "C17"
	msg = aircraftMessage(nov, cfg, nil)
	if msg.Priority != -2 || !strings.Contains(msg.Body, "Treffer: neuer Typ an diesem Flughafen C17") {
		t.Errorf("novelty = priority %d, body:\n%s", msg.Priority, msg.Body)
	}
	nov.Kind = engine.KindLanded
	if msg = aircraftMessage(nov, cfg, nil); msg.Priority != -2 {
		t.Errorf("the landing of a novelty keeps the novelty priority, got %d", msg.Priority)
	}
}

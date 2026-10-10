package obs

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"sort"
	"strings"
	"testing"
	"time"
)

var update = flag.Bool("update", false, "rewrite testdata/metric_families.golden")

func exercise(m *Metrics) string {
	m.WatchSources([]string{"adsb.lol", "opensky"}, []string{"timeout", "http_status"})
	m.WatchAirports([]string{"EDDW"}, []string{"type", "registration"})
	m.WatchPhotos([]string{"found", "not_found"})
	m.ObservePoll("adsb.lol", ResultOK, 120*time.Millisecond)
	m.ObservePoll("opensky", "timeout", 5*time.Second)
	m.SetOpenSkyCredits(321)
	m.CountApproach("EDDW", "type")
	m.CountLanding("EDDW", true)
	m.CountNotification(KindApproach, ResultSent, "EDDW")
	m.CountNotification(KindOutage, ResultFailed, "")
	m.CountPhoto("found")
	m.ConfigLoaded(2, 7)
	m.Publish(
		[]AirportGauge{{Airport: "EDDW", Aircraft: 5, Watched: 1, Approaching: 1}},
		[]SourceGauge{{Source: "adsb.lol", Airport: "EDDW", Aircraft: 6}},
		time.Minute)
	var buf bytes.Buffer
	m.WritePrometheus(&buf)
	return buf.String()
}

// families extracts "name type" from the # TYPE lines, for our own metrics.
func families(text string) []string {
	var out []string
	for _, line := range strings.Split(text, "\n") {
		f := strings.Fields(line)
		if len(f) == 4 && f[0] == "#" && f[1] == "TYPE" && strings.HasPrefix(f[2], "rarebirdalert_") {
			out = append(out, f[2]+" "+f[3])
		}
	}
	sort.Strings(out)
	return out
}

func TestMetricCatalogMatchesOutput(t *testing.T) {
	text := exercise(NewMetrics("1.2.3"))
	got := strings.Join(families(text), "\n") + "\n"

	if *update {
		if err := os.WriteFile("testdata/metric_families.golden", []byte(got), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile("testdata/metric_families.golden")
	if err != nil {
		t.Fatal(err)
	}
	if got != string(want) {
		t.Errorf("exported metric families differ from the golden file (run with -update to accept):\n--- got\n%s--- want\n%s", got, want)
	}

	// The catalog and the output must describe the same families, with help.
	fromCatalog := map[string]string{}
	for _, info := range Catalog {
		fromCatalog[info.Name] = info.Type
		if !strings.Contains(text, "# HELP "+info.Name+" "+info.Help) {
			t.Errorf("no help text for %s", info.Name)
		}
	}
	for _, fam := range families(text) {
		name, typ, _ := strings.Cut(fam, " ")
		if fromCatalog[name] != typ {
			t.Errorf("%s is exported as %s but the catalog says %q", name, typ, fromCatalog[name])
		}
		delete(fromCatalog, name)
	}
	for name := range fromCatalog {
		t.Errorf("catalog metric %s is not exported", name)
	}
}

func TestSeriesAreVisibleBeforeTheFirstEvent(t *testing.T) {
	m := NewMetrics("dev")
	m.WatchSources([]string{"adsb.lol"}, []string{"timeout"})
	m.WatchAirports([]string{"EDDW"}, []string{"type"})
	var buf bytes.Buffer
	m.WritePrometheus(&buf)
	text := buf.String()
	for _, want := range []string{
		`rarebirdalert_source_polls_total{source="adsb.lol",result="ok"} 0`,
		`rarebirdalert_source_polls_total{source="adsb.lol",result="timeout"} 0`,
		`rarebirdalert_notifications_total{kind="approach",result="sent",airport="EDDW"} 0`,
		`rarebirdalert_notifications_total{kind="outage",result="failed"} 0`,
		`rarebirdalert_approaches_total{airport="EDDW",rule="type"} 0`,
		`rarebirdalert_landings_total{airport="EDDW",detection="presumed"} 0`,
		`rarebirdalert_build_info{version="dev"`,
	} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q", want)
		}
	}
}

func TestGaugesEndWhenPublishingStops(t *testing.T) {
	m := NewMetrics("dev")
	now := time.Now()
	m.now = func() time.Time { return now }
	m.Publish([]AirportGauge{{Airport: "EDDW", Aircraft: 3}}, []SourceGauge{{Source: "adsb.lol", Airport: "EDDW", Aircraft: 3}}, time.Minute)

	var buf bytes.Buffer
	m.WritePrometheus(&buf)
	if !strings.Contains(buf.String(), `rarebirdalert_airport_aircraft{airport="EDDW"} 3`) ||
		!strings.Contains(buf.String(), `rarebirdalert_source_aircraft{source="adsb.lol",airport="EDDW"} 3`) {
		t.Fatalf("gauges missing:\n%s", buf.String())
	}

	now = now.Add(2 * time.Minute)
	buf.Reset()
	m.WritePrometheus(&buf)
	if strings.Contains(buf.String(), "rarebirdalert_airport_aircraft") || strings.Contains(buf.String(), "rarebirdalert_source_aircraft") {
		t.Error("expired gauges must not be exported")
	}
}

func TestLogFormat(t *testing.T) {
	var buf bytes.Buffer
	l, err := NewLogger(&buf, "json", "info")
	if err != nil {
		t.Fatal(err)
	}
	Log(l, 0, EventAppStarted, "started")
	var line map[string]any
	if err := json.Unmarshal(buf.Bytes(), &line); err != nil {
		t.Fatalf("not JSON: %q", buf.String())
	}
	if line["event"] != "app.started" || line["level"] != "info" || line["msg"] != "started" {
		t.Errorf("line = %v", line)
	}
	if ts, _ := line["time"].(string); !strings.HasSuffix(ts, "Z") {
		t.Errorf("time must be UTC: %q", ts)
	}

	if _, err := NewLogger(&buf, "xml", "info"); err == nil {
		t.Error("unknown format must be refused")
	}
	if _, err := NewLogger(&buf, "json", "loud"); err == nil {
		t.Error("unknown level must be refused")
	}
}

func TestEventNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, e := range Events {
		if seen[e] {
			t.Errorf("duplicate event %s", e)
		}
		seen[e] = true
	}
}

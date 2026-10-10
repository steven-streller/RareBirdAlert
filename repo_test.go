package main

import (
	"encoding/json"
	"os"
	"regexp"
	"slices"
	"strings"
	"testing"

	"rarebirdalert/internal/config"
	"rarebirdalert/internal/obs"
)

func readRepoFile(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(name)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

var (
	metricName = regexp.MustCompile(`rarebirdalert_[a-z0-9_]+`)
	eventName  = regexp.MustCompile("`((?:app|config|state|cycle|source|upstream|aircraft|notification|photo|http|library)\\.[a-z_]+)`")
	logEvents  = regexp.MustCompile(`event(?:=~?)"([^"]+)"`)
)

// knownMetric accepts the catalog names and the series suffixes of histograms.
func knownMetric(name string) bool {
	for _, info := range obs.Catalog {
		if name == info.Name {
			return true
		}
		if info.Type == "histogram" {
			for _, suffix := range []string{"_bucket", "_sum", "_count"} {
				if name == info.Name+suffix {
					return true
				}
			}
		}
	}
	return false
}

func TestDashboardUsesOnlyKnownMetricsAndEvents(t *testing.T) {
	raw := readRepoFile(t, "grafana/rarebirdalert-dashboard.json")
	var dash struct {
		UID    string `json:"uid"`
		Panels []struct {
			Title   string `json:"title"`
			Type    string `json:"type"`
			Targets []struct {
				Expr string `json:"expr"`
			} `json:"targets"`
		} `json:"panels"`
		Annotations struct {
			List []struct {
				Expr string `json:"expr"`
			} `json:"list"`
		} `json:"annotations"`
	}
	if err := json.Unmarshal([]byte(raw), &dash); err != nil {
		t.Fatal(err)
	}
	if dash.UID != "rarebirdalert" {
		t.Errorf("uid = %q", dash.UID)
	}

	var exprs []string
	for _, p := range dash.Panels {
		for _, q := range p.Targets {
			exprs = append(exprs, q.Expr)
			if strings.Contains(q.Expr, "rarebirdalert_") && !strings.Contains(q.Expr, `job="$job"`) {
				t.Errorf("panel %q: %q does not filter by job", p.Title, q.Expr)
			}
		}
		if p.Type != "row" && p.Title == "" {
			t.Error("panel without a title")
		}
	}
	for _, a := range dash.Annotations.List {
		exprs = append(exprs, a.Expr)
	}

	used := 0
	for _, e := range exprs {
		for _, name := range metricName.FindAllString(e, -1) {
			used++
			if !knownMetric(name) {
				t.Errorf("dashboard uses unknown metric %q in %q", name, e)
			}
		}
		for _, m := range logEvents.FindAllStringSubmatch(e, -1) {
			for _, ev := range strings.Split(m[1], "|") {
				if !slices.Contains(obs.Events, ev) {
					t.Errorf("dashboard queries unknown event %q", ev)
				}
			}
		}
	}
	if used < 10 {
		t.Errorf("only %d metric references found, the dashboard looks empty", used)
	}
}

func TestReadmeDocumentsExactlyTheMetricsAndEvents(t *testing.T) {
	readme := readRepoFile(t, "README.md")

	for _, name := range metricName.FindAllString(readme, -1) {
		if !knownMetric(name) {
			t.Errorf("README names unknown metric %q", name)
		}
	}
	for _, info := range obs.Catalog {
		if !strings.Contains(readme, "`"+info.Name+"`") {
			t.Errorf("README does not document metric %s", info.Name)
		}
	}

	documented := map[string]bool{}
	for _, m := range eventName.FindAllStringSubmatch(readme, -1) {
		if m[1] == "config.yaml" { // the file, not an event
			continue
		}
		documented[m[1]] = true
		if !slices.Contains(obs.Events, m[1]) {
			t.Errorf("README names unknown event %q", m[1])
		}
	}
	for _, ev := range obs.Events {
		if !documented[ev] {
			t.Errorf("README does not document event %s", ev)
		}
	}
}

func TestReadmeAndExampleListTheEnvironmentVariables(t *testing.T) {
	known := []string{"PUSHOVER_TOKEN", "PUSHOVER_USER", "PUSHOVER_DEVICE", "RAREBIRDALERT_CONTACT",
		"OPENSKY_CLIENT_ID", "OPENSKY_CLIENT_SECRET", "RAREBIRDALERT_CONFIG", "RAREBIRDALERT_STATE",
		"RAREBIRDALERT_HTTP_ADDR", "RAREBIRDALERT_LOG_LEVEL", "RAREBIRDALERT_LOG_FORMAT", "RAREBIRDALERT_DRY_RUN"}
	readme := readRepoFile(t, "README.md")
	for _, name := range known {
		if !strings.Contains(readme, "`"+name+"`") {
			t.Errorf("README does not document %s", name)
		}
	}

	// Everything that looks like one of ours in the README must be real.
	for _, name := range regexp.MustCompile("`((?:RAREBIRDALERT|PUSHOVER|OPENSKY)_[A-Z_]+)`").FindAllStringSubmatch(readme, -1) {
		if !slices.Contains(known, name[1]) {
			t.Errorf("README names unknown variable %s", name[1])
		}
	}

	env := readRepoFile(t, ".env.example")
	for _, name := range []string{"PUSHOVER_TOKEN", "PUSHOVER_USER", "RAREBIRDALERT_CONTACT"} {
		if !strings.Contains(env, name+"=") {
			t.Errorf(".env.example lacks %s", name)
		}
	}
}

func TestExampleConfigIsValid(t *testing.T) {
	cfg, err := config.Parse([]byte(readRepoFile(t, "config/config.example.yaml")))
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.Airports) != 2 || cfg.Watch.Count() == 0 {
		t.Errorf("example config: %d airports, %d watch entries", len(cfg.Airports), cfg.Watch.Count())
	}
}

func TestComposeMatchesTheImage(t *testing.T) {
	compose := readRepoFile(t, "compose.yaml")
	for _, want := range []string{"env_file: .env", "./config:/config:ro", `["CMD", "/rarebirdalert", "health"]`,
		"read_only: true", "cap_drop:", "no-new-privileges:true", "127.0.0.1:8080:8080"} {
		if !strings.Contains(compose, want) {
			t.Errorf("compose.yaml lacks %q", want)
		}
	}
	docker := readRepoFile(t, "Dockerfile")
	for _, want := range []string{"FROM scratch", "USER 65534:65534", `ENTRYPOINT ["/rarebirdalert"]`,
		"timetzdata", "nethttpomithttp2", "CGO_ENABLED=0", "@sha256:"} {
		if !strings.Contains(docker, want) {
			t.Errorf("Dockerfile lacks %q", want)
		}
	}
}

// The image has a size budget; the cheapest way to blow it is a new
// dependency. This test makes adding one a deliberate act.
func TestOnlyTheExpectedDirectDependencies(t *testing.T) {
	var direct []string
	for _, line := range strings.Split(readRepoFile(t, "go.mod"), "\n") {
		line = strings.TrimSpace(line)
		fields := strings.Fields(line)
		if len(fields) < 2 || strings.HasPrefix(line, "module") || strings.HasPrefix(line, "go ") ||
			strings.HasPrefix(line, "require") || strings.HasPrefix(line, ")") || strings.HasPrefix(line, "//") {
			continue
		}
		if !strings.Contains(line, "// indirect") {
			direct = append(direct, fields[0])
		}
	}
	slices.Sort(direct)
	want := []string{"github.com/VictoriaMetrics/metrics", "go.yaml.in/yaml/v3"}
	if !slices.Equal(direct, want) {
		t.Errorf("direct dependencies = %v, want %v", direct, want)
	}
}

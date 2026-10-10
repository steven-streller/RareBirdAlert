package obs

import (
	"bytes"
	"fmt"
	"io"
	"runtime"
	"strings"
	"sync/atomic"
	"time"

	"github.com/VictoriaMetrics/metrics"
)

// Metric names.
const (
	MetricSourcePolls       = "rarebirdalert_source_polls_total"
	MetricSourcePollTime    = "rarebirdalert_source_poll_duration_seconds"
	MetricSourceLastSuccess = "rarebirdalert_source_last_success_timestamp_seconds"
	MetricSourceAircraft    = "rarebirdalert_source_aircraft"
	MetricOpenSkyCredits    = "rarebirdalert_opensky_credits_remaining"
	MetricAirportAircraft   = "rarebirdalert_airport_aircraft"
	MetricAirportApproach   = "rarebirdalert_airport_approaching"
	MetricAirportWatched    = "rarebirdalert_airport_watched_aircraft"
	MetricApproaches        = "rarebirdalert_approaches_total"
	MetricLandings          = "rarebirdalert_landings_total"
	MetricNotifications     = "rarebirdalert_notifications_total"
	MetricPhotoLookups      = "rarebirdalert_photo_lookups_total"
	MetricConfigReloadOK    = "rarebirdalert_config_last_reload_successful"
	MetricConfigReloadTime  = "rarebirdalert_config_last_reload_success_timestamp_seconds"
	MetricConfigAirports    = "rarebirdalert_config_airports"
	MetricConfigWatch       = "rarebirdalert_config_watch_entries"
	MetricBuildInfo         = "rarebirdalert_build_info"
)

// MetricInfo describes one metric family.
type MetricInfo struct {
	Name string
	Type string
	Help string
}

// Catalog lists every metric family the application exports itself. The Go
// runtime and process metrics of the library come on top.
var Catalog = []MetricInfo{
	{MetricSourcePolls, "counter", "Polls of the position sources by source and result."},
	{MetricSourcePollTime, "histogram", "Duration of one poll of a position source in seconds."},
	{MetricSourceLastSuccess, "gauge", "Unix time of the last successful poll of a source."},
	{MetricSourceAircraft, "gauge", "Aircraft a source reported in the last poll around an airport."},
	{MetricOpenSkyCredits, "gauge", "API credits OpenSky says are left for today."},
	{MetricAirportAircraft, "gauge", "Aircraft inside the geofence of an airport, all sources merged."},
	{MetricAirportApproach, "gauge", "Watched aircraft currently on approach to an airport."},
	{MetricAirportWatched, "gauge", "Aircraft inside the geofence of an airport that match the watchlist."},
	{MetricApproaches, "counter", "Approaches of watched aircraft by airport and matching rule, whether or not a push was sent."},
	{MetricLandings, "counter", "Landings of watched aircraft by airport and detection (observed on the ground, presumed after losing the signal)."},
	{MetricNotifications, "counter", "Push notification attempts by kind and result (sent, failed = will be retried, rejected = given up, dry_run)."},
	{MetricPhotoLookups, "counter", "Photo lookups at Planespotters.net by result."},
	{MetricConfigReloadOK, "gauge", "1 if the current content of the config file is valid, 0 if it was rejected."},
	{MetricConfigReloadTime, "gauge", "Unix time of the last successful load of the config file."},
	{MetricConfigAirports, "gauge", "Number of airports in the active config."},
	{MetricConfigWatch, "gauge", "Number of watchlist entries in the active config."},
	{MetricBuildInfo, "gauge", "Build information, constant 1."},
}

// Label values.
const (
	KindApproach    = "approach"
	KindLanded      = "landed"
	KindOutage      = "outage"
	KindRecovery    = "recovery"
	KindConfigError = "config_error"

	ResultSent     = "sent"
	ResultFailed   = "failed"
	ResultRejected = "rejected"
	ResultDryRun   = "dry_run"

	DetectionObserved = "observed"
	DetectionPresumed = "presumed"
)

// ResultOK is the result label of a successful poll. Failed polls use the
// error kinds of package source.
const ResultOK = "ok"

var (
	airportKinds        = []string{KindApproach, KindLanded}
	systemKinds         = []string{KindOutage, KindRecovery, KindConfigError}
	notificationResults = []string{ResultSent, ResultFailed, ResultRejected, ResultDryRun}
	pollBuckets         = []float64{0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15}
)

// AirportGauge is the view of one airport that is exported as gauges.
type AirportGauge struct {
	Airport     string
	Aircraft    int
	Watched     int
	Approaching int
}

// SourceGauge is what one source reported around one airport.
type SourceGauge struct {
	Source   string
	Airport  string
	Aircraft int
}

type view struct {
	airports   []AirportGauge
	sources    []SourceGauge
	validUntil time.Time
}

// Metrics is the set of metrics of one application instance.
type Metrics struct {
	set  *metrics.Set
	help map[string]string
	now  func() time.Time

	configOK       *metrics.Gauge
	configLoadedAt *metrics.Gauge
	configAirports *metrics.Gauge
	configWatch    *metrics.Gauge
	credits        *metrics.Gauge

	view atomic.Pointer[view]
}

// NewMetrics creates the metrics.
func NewMetrics(version string) *Metrics {
	// The library only writes "# TYPE" lines when asked to. They are needed
	// for counters and histograms to be recognised as such.
	metrics.ExposeMetadata(true)

	m := &Metrics{set: metrics.NewSet(), help: map[string]string{}, now: time.Now}
	for _, info := range Catalog {
		m.help[info.Name] = info.Help
	}

	m.configOK = m.set.NewGauge(MetricConfigReloadOK, nil)
	m.configLoadedAt = m.set.NewGauge(MetricConfigReloadTime, nil)
	m.configAirports = m.set.NewGauge(MetricConfigAirports, nil)
	m.configWatch = m.set.NewGauge(MetricConfigWatch, nil)
	m.set.NewGauge(fmt.Sprintf(`%s{version=%s,goversion=%s}`, MetricBuildInfo, quote(version), quote(runtime.Version())), nil).Set(1)

	for _, kind := range systemKinds {
		for _, result := range notificationResults {
			m.set.GetOrCreateCounter(notificationCounter(kind, result, ""))
		}
	}
	m.set.RegisterMetricsWriter(m.writeView)
	return m
}

// WatchSources creates the per-source series at 0, so that rate() and
// increase() see every event from the first one on.
func (m *Metrics) WatchSources(sources, errorKinds []string) {
	for _, s := range sources {
		for _, result := range append([]string{ResultOK}, errorKinds...) {
			m.set.GetOrCreateCounter(pollCounter(s, result))
		}
		m.set.GetOrCreatePrometheusHistogramExt(pollDuration(s), pollBuckets)
	}
}

// WatchAirports creates the per-airport counters at 0.
func (m *Metrics) WatchAirports(icaos, rules []string) {
	for _, a := range icaos {
		for _, kind := range airportKinds {
			for _, result := range notificationResults {
				m.set.GetOrCreateCounter(notificationCounter(kind, result, a))
			}
		}
		for _, rule := range rules {
			m.set.GetOrCreateCounter(approachCounter(a, rule))
		}
		for _, d := range []string{DetectionObserved, DetectionPresumed} {
			m.set.GetOrCreateCounter(landingCounter(a, d))
		}
	}
}

// WatchPhotos creates the photo counters at 0.
func (m *Metrics) WatchPhotos(results []string) {
	for _, r := range results {
		m.set.GetOrCreateCounter(photoCounter(r))
	}
}

// ObservePoll records one poll of a source.
func (m *Metrics) ObservePoll(source, result string, duration time.Duration) {
	m.set.GetOrCreateCounter(pollCounter(source, result)).Inc()
	m.set.GetOrCreatePrometheusHistogramExt(pollDuration(source), pollBuckets).Update(duration.Seconds())
	if result == ResultOK {
		m.set.GetOrCreateGauge(lastSuccessGauge(source), nil).Set(unixSeconds(m.now()))
	}
}

// SetOpenSkyCredits records the credits OpenSky reports as left.
func (m *Metrics) SetOpenSkyCredits(n int) {
	if m.credits == nil {
		m.credits = m.set.NewGauge(MetricOpenSkyCredits, nil)
	}
	m.credits.Set(float64(n))
}

// CountApproach records a detected approach of a watched aircraft.
func (m *Metrics) CountApproach(airport, rule string) {
	m.set.GetOrCreateCounter(approachCounter(airport, rule)).Inc()
}

// CountLanding records a detected landing of a watched aircraft.
func (m *Metrics) CountLanding(airport string, presumed bool) {
	d := DetectionObserved
	if presumed {
		d = DetectionPresumed
	}
	m.set.GetOrCreateCounter(landingCounter(airport, d)).Inc()
}

// CountNotification records one attempt to send a push. airport is empty for
// notifications that are not about an airport.
func (m *Metrics) CountNotification(kind, result, airport string) {
	m.set.GetOrCreateCounter(notificationCounter(kind, result, airport)).Inc()
}

// CountPhoto records one photo lookup.
func (m *Metrics) CountPhoto(result string) {
	m.set.GetOrCreateCounter(photoCounter(result)).Inc()
}

// ConfigLoaded records that the config file was read and found valid.
func (m *Metrics) ConfigLoaded(airports, watch int) {
	m.configOK.Set(1)
	m.configLoadedAt.Set(unixSeconds(m.now()))
	m.configAirports.Set(float64(airports))
	m.configWatch.Set(float64(watch))
}

// ConfigRejected records that the current content of the config file is
// invalid. The counts keep describing the config that is still active.
func (m *Metrics) ConfigRejected() { m.configOK.Set(0) }

// Publish replaces the airport and source gauges. They are exported for the
// given duration only: if polling stops, the series end instead of freezing at
// their last value.
func (m *Metrics) Publish(airports []AirportGauge, sources []SourceGauge, validFor time.Duration) {
	m.view.Store(&view{airports: airports, sources: sources, validUntil: m.now().Add(validFor)})
}

// WritePrometheus writes all metrics in the Prometheus text format.
func (m *Metrics) WritePrometheus(w io.Writer) {
	var buf bytes.Buffer
	m.set.WritePrometheus(&buf)
	// The library writes "# HELP name" without a text. Fill in ours.
	for line := range bytes.Lines(buf.Bytes()) {
		if name, ok := bytes.CutPrefix(bytes.TrimRight(line, "\n"), []byte("# HELP ")); ok {
			if help := m.help[string(name)]; help != "" {
				fmt.Fprintf(w, "# HELP %s %s\n", name, help)
				continue
			}
		}
		_, _ = w.Write(line)
	}
	metrics.WriteProcessMetrics(w)
}

func (m *Metrics) writeView(w io.Writer) {
	v := m.view.Load()
	if v == nil || m.now().After(v.validUntil) {
		return
	}
	families := []struct {
		name  string
		value func(AirportGauge) int
	}{
		{MetricAirportAircraft, func(g AirportGauge) int { return g.Aircraft }},
		{MetricAirportApproach, func(g AirportGauge) int { return g.Approaching }},
		{MetricAirportWatched, func(g AirportGauge) int { return g.Watched }},
	}
	if len(v.airports) > 0 {
		for _, f := range families {
			metrics.WriteMetadataIfNeeded(w, f.name, "gauge")
			for _, g := range v.airports {
				fmt.Fprintf(w, "%s{airport=%s} %d\n", f.name, quote(g.Airport), f.value(g))
			}
		}
	}
	if len(v.sources) > 0 {
		metrics.WriteMetadataIfNeeded(w, MetricSourceAircraft, "gauge")
		for _, g := range v.sources {
			fmt.Fprintf(w, "%s{source=%s,airport=%s} %d\n", MetricSourceAircraft, quote(g.Source), quote(g.Airport), g.Aircraft)
		}
	}
}

func pollCounter(source, result string) string {
	return fmt.Sprintf(`%s{source=%s,result=%s}`, MetricSourcePolls, quote(source), quote(result))
}

func pollDuration(source string) string {
	return fmt.Sprintf(`%s{source=%s}`, MetricSourcePollTime, quote(source))
}

func lastSuccessGauge(source string) string {
	return fmt.Sprintf(`%s{source=%s}`, MetricSourceLastSuccess, quote(source))
}

func approachCounter(airport, rule string) string {
	return fmt.Sprintf(`%s{airport=%s,rule=%s}`, MetricApproaches, quote(airport), quote(rule))
}

func landingCounter(airport, detection string) string {
	return fmt.Sprintf(`%s{airport=%s,detection=%s}`, MetricLandings, quote(airport), quote(detection))
}

func photoCounter(result string) string {
	return fmt.Sprintf(`%s{result=%s}`, MetricPhotoLookups, quote(result))
}

func notificationCounter(kind, result, airport string) string {
	if airport == "" {
		return fmt.Sprintf(`%s{kind=%s,result=%s}`, MetricNotifications, quote(kind), quote(result))
	}
	return fmt.Sprintf(`%s{kind=%s,result=%s,airport=%s}`, MetricNotifications, quote(kind), quote(result), quote(airport))
}

var labelEscaper = strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`)

// quote formats a label value as the exposition format requires.
func quote(v string) string {
	return `"` + labelEscaper.Replace(v) + `"`
}

func unixSeconds(t time.Time) float64 {
	return float64(t.UnixMilli()) / 1000
}

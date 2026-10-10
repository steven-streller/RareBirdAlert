package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"runtime/debug"
	"strings"
	"sync"
	"syscall"
	"time"

	"rarebirdalert/internal/aircraft"
	"rarebirdalert/internal/airports"
	"rarebirdalert/internal/config"
	"rarebirdalert/internal/engine"
	"rarebirdalert/internal/obs"
	"rarebirdalert/internal/photos"
	"rarebirdalert/internal/pushover"
	"rarebirdalert/internal/source"
)

// Timing of the sources.
const (
	// airplanes.live allows one request per second.
	requestGap = 1100 * time.Millisecond
	// areaMarginNM widens the query beyond the geofence, so that aircraft
	// just outside it are seen coming.
	areaMarginNM = 2.0
	// maxAgeADSB is how old an aircraft position from the ADS-B sources may be.
	maxAgeADSB = 60 * time.Second
	// openSkyBudgetShare is the part of the daily OpenSky credits the auto
	// interval plans to use.
	openSkyBudgetShare = 0.75
)

// feed is the last successful answer of one source for one airport.
type feed struct {
	at       time.Time
	aircraft []aircraft.Aircraft
}

type health struct {
	failures int
	since    time.Time

	// backoff and blockedUntil hold a source back after it answered 429.
	// Asking again sooner only prolongs the throttling.
	backoff      time.Duration
	blockedUntil time.Time
}

// Back-off after a rate limit: doubles from the first to the last value.
const (
	minBackoff = 30 * time.Second
	maxBackoff = 10 * time.Minute
)

// app is the daemon: it polls the sources, lets the engine decide and turns
// the engine's answers into pushes, log lines and metrics.
type app struct {
	set    settings
	log    *slog.Logger
	met    *obs.Metrics
	push   *pushover.Client
	photos *photos.Client // nil: no contact configured, no photos
	loader *config.Loader
	store  *engine.Store
	clock  func() time.Time
	beat   obs.Heartbeat

	sources map[string]source.Source
	opensky *source.OpenSky

	cfg   *config.Config // nil until a valid config has been loaded
	state engine.State

	feeds    map[string]map[string]feed // source -> airport -> last answer
	lastPoll map[string]time.Time
	health   map[string]*health

	lastAnyOK     time.Time
	outageError   error
	outageAlerted bool

	// pending holds notifications about RareBirdAlert itself that still have
	// to be delivered, by kind. A newer message of a kind replaces the older.
	pending map[string]pushover.Message

	saveFailing bool
}

// systemKinds is the order in which pending notifications are sent.
var systemKinds = []string{obs.KindConfigError, obs.KindOutage, obs.KindRecovery}

func newApp(set settings, logger *slog.Logger, met *obs.Metrics, clock func() time.Time) *app {
	ua := userAgent(set.Contact)
	a := &app{
		set:    set,
		log:    logger,
		met:    met,
		push:   pushover.New(set.Token, set.User, set.Device, set.PushoverURL, ua),
		loader: config.NewLoader(set.ConfigPath),
		store:  engine.NewStore(set.StatePath),
		clock:  clock,
		state:  engine.NewState(),

		opensky:   source.NewOpenSky(set.OpenSkyURL, set.OpenSkyTokenURL, set.OpenSkyID, set.OpenSkySecret, ua),
		feeds:     map[string]map[string]feed{},
		lastPoll:  map[string]time.Time{},
		health:    map[string]*health{},
		pending:   map[string]pushover.Message{},
		lastAnyOK: clock(),
	}
	a.sources = map[string]source.Source{
		source.NameADSBLol:   source.NewADSBv2(source.NameADSBLol, set.ADSBLolURL, ua),
		source.NameAirplanes: source.NewADSBv2(source.NameAirplanes, set.AirplanesURL, ua),
		source.NameOpenSky:   a.opensky,
	}
	if set.Contact != "" || set.PlanespottersURL != "" {
		a.photos = photos.New(set.PlanespottersURL, ua)
	}
	met.WatchSources(source.Names, source.ErrorKinds)
	met.WatchPhotos(photos.Results)
	return a
}

// minFailedLifetime is how long a daemon that is about to exit with an error
// stays alive, counted from its start. Not every restart policy backs off:
// podman restarts at once, which turns a broken setup into several starts per
// second and floods the logs and the APIs.
var minFailedLifetime = 30 * time.Second

// runDaemon is the default command. It returns the process exit code.
func runDaemon(set settings) int {
	started := time.Now()
	logger, err := obs.NewLogger(set.Stdout, set.LogFormat, set.LogLevel)
	if err != nil {
		fmt.Fprintln(set.Stderr, "rarebirdalert:", err)
		holdBeforeExit(set, nil, started)
		return 2
	}
	code := daemon(set, logger)
	if code != 0 {
		holdBeforeExit(set, logger, started)
	}
	return code
}

// holdBeforeExit waits until the process has lived for minFailedLifetime. It
// returns at once on SIGINT or SIGTERM, and when a person is watching: in a
// terminal a delayed exit only gets in the way.
func holdBeforeExit(set settings, logger *slog.Logger, started time.Time) {
	remaining := minFailedLifetime - time.Since(started)
	if remaining <= 0 || isTerminal(set.Stdout) {
		return
	}
	if logger != nil {
		obs.Log(logger, slog.LevelInfo, obs.EventAppExitDelayed, "delaying the exit so that a restart policy cannot loop tightly",
			slog.Int64("delay_seconds", int64(remaining.Round(time.Second)/time.Second)))
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	select {
	case <-ctx.Done():
	case <-time.After(remaining):
	}
}

func isTerminal(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	info, err := f.Stat()
	return err == nil && info.Mode()&os.ModeCharDevice != 0
}

// daemon runs until it is told to stop or cannot go on.
func daemon(set settings, logger *slog.Logger) (code int) {
	slog.SetDefault(logger)
	obs.CaptureStdLog(logger)

	// A panic would otherwise print a multi-line trace that is not JSON.
	defer func() {
		if r := recover(); r != nil {
			obs.Log(logger, slog.LevelError, obs.EventAppPanic, "panic",
				slog.String("error", fmt.Sprint(r)), slog.String("stack", string(debug.Stack())))
			code = 2
		}
	}()

	if !set.DryRun && (set.Token == "" || set.User == "") {
		obs.Log(logger, slog.LevelError, obs.EventAppStopping, "PUSHOVER_TOKEN and PUSHOVER_USER must be set",
			slog.String("reason", "missing_credentials"))
		return 1
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	a := newApp(set, logger, obs.NewMetrics(version), time.Now)
	a.beat.Beat(time.Now(), 2*time.Minute)
	obs.Log(logger, slog.LevelInfo, obs.EventAppStarted, "RareBirdAlert started",
		slog.String("version", version),
		slog.String("config_path", set.ConfigPath),
		slog.String("state_path", set.StatePath),
		slog.String("http_addr", set.HTTPAddr),
		slog.Bool("dry_run", set.DryRun),
		slog.Bool("photos", a.photos != nil),
		slog.Bool("opensky_authenticated", a.opensky.Authenticated()))
	if a.photos == nil {
		obs.Log(logger, slog.LevelWarn, obs.EventAppWarning,
			"RAREBIRDALERT_CONTACT is not set: Planespotters.net refuses requests without a contact, so pushes carry no photo",
			slog.String("reason", "no_contact"))
	}
	if (set.OpenSkyID == "") != (set.OpenSkySecret == "") {
		obs.Log(logger, slog.LevelWarn, obs.EventAppWarning,
			"only one of OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET is set, OpenSky is used anonymously",
			slog.String("reason", "opensky_credentials_incomplete"))
	}

	var srv *http.Server
	if set.HTTPAddr != "" {
		listener, err := net.Listen("tcp", set.HTTPAddr)
		if err != nil {
			obs.Log(logger, slog.LevelError, obs.EventAppStopping, "cannot listen for HTTP",
				slog.String("reason", "listen_failed"), slog.String("http_addr", set.HTTPAddr), slog.String("error", err.Error()))
			return 1
		}
		srv = obs.NewServer(set.HTTPAddr, a.met, func() bool { return a.beat.Healthy(time.Now()) },
			obs.StdLogger(logger, obs.EventHTTPError))
		go func() {
			if err := srv.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
				obs.Log(logger, slog.LevelError, obs.EventHTTPError, "HTTP server stopped", slog.String("error", err.Error()))
			}
		}()
		obs.Log(logger, slog.LevelInfo, obs.EventHTTPListening, "serving /metrics and /healthz",
			slog.String("http_addr", listener.Addr().String()))
	}

	a.restoreState()
	a.loop(ctx)

	obs.Log(logger, slog.LevelInfo, obs.EventAppStopping, "RareBirdAlert stopping", slog.String("reason", "signal"))
	if err := a.store.Flush(a.state, a.clock()); err != nil && !a.saveFailing {
		obs.Log(logger, slog.LevelWarn, obs.EventStateSaveFailed, "cannot write state file, a restart may repeat notifications",
			slog.String("error", err.Error()))
	}
	if srv != nil {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}
	return 0
}

// loop runs poll cycles until ctx is cancelled.
func (a *app) loop(ctx context.Context) {
	for {
		a.cycle(ctx)
		select {
		case <-ctx.Done():
			return
		case <-time.After(a.pollInterval()):
		}
	}
}

func (a *app) pollInterval() time.Duration {
	if a.cfg == nil {
		return config.DefaultPollInterval
	}
	return a.cfg.PollInterval
}

// cycle is one iteration: reload the config if it changed, poll, decide,
// notify, persist.
func (a *app) cycle(ctx context.Context) {
	started := time.Now()
	a.reloadConfig()
	if a.cfg != nil {
		a.poll(ctx)
	}
	a.sendPending(ctx)
	// A cycle can take a while when sources are slow; the loop still counts
	// as alive for a few intervals after it.
	a.beat.Beat(time.Now(), max(3*a.pollInterval(), 2*time.Minute)+time.Since(started))
}

func (a *app) restoreState() {
	state, restored, discarded, err := a.store.Load(a.clock())
	a.state = state
	switch {
	case err != nil:
		obs.Log(a.log, slog.LevelWarn, obs.EventStateLoadFailed, "state file unusable, starting without memory",
			slog.String("error", err.Error()))
	case restored+discarded > 0:
		obs.Log(a.log, slog.LevelInfo, obs.EventStateRestored, "state restored",
			slog.Int("tracks", restored), slog.Int("discarded", discarded))
	}
}

func (a *app) reloadConfig() {
	cfg, hash, changed, err := a.loader.Poll()
	if !changed {
		return
	}
	if err != nil {
		a.met.ConfigRejected()
		obs.Log(a.log, slog.LevelError, obs.EventConfigInvalid, "config file rejected",
			slog.String("config_path", a.loader.Path()),
			slog.String("config_hash", hash),
			slog.String("error", err.Error()),
			slog.Bool("active_config_kept", a.cfg != nil))
		a.pending[obs.KindConfigError] = configErrorMessage(err, a.cfg != nil)
		return
	}

	reload := a.cfg != nil
	a.cfg = cfg
	delete(a.pending, obs.KindConfigError)
	icaos := make([]string, 0, len(cfg.Airports))
	for _, ap := range cfg.Airports {
		icaos = append(icaos, ap.ICAO)
	}
	a.met.ConfigLoaded(len(cfg.Airports), cfg.Watch.Count())
	a.met.WatchAirports(icaos, config.Rules)
	obs.Log(a.log, slog.LevelInfo, obs.EventConfigLoaded, "config loaded",
		slog.String("config_hash", cfg.Hash),
		slog.String("airports", strings.Join(icaos, ",")),
		slog.String("sources", strings.Join(cfg.Sources, ",")),
		slog.Int("watch_entries", cfg.Watch.Count()),
		slog.String("poll_interval", cfg.PollInterval.String()),
		slog.String("opensky_interval", a.openSkyInterval().String()),
		slog.String("timezone", cfg.Location.String()),
		slog.Bool("reload", reload))

	if cfg.HasSource(source.NameAirplanes) && a.set.Contact == "" && a.set.AirplanesURL == "" {
		obs.Log(a.log, slog.LevelWarn, obs.EventAppWarning,
			"airplanes.live is enabled without RAREBIRDALERT_CONTACT: the service answers 403 to anonymous clients",
			slog.String("reason", "airplanes_without_contact"))
	}
	if cfg.HasSource(source.NameOpenSky) && cfg.OpenSkyInterval > 0 {
		budget := float64(a.opensky.DailyCredits()) * openSkyBudgetShare
		perDay := float64(24*time.Hour) / float64(cfg.OpenSkyInterval) * float64(len(cfg.Airports))
		if perDay > float64(a.opensky.DailyCredits()) {
			obs.Log(a.log, slog.LevelWarn, obs.EventAppWarning,
				"opensky_interval needs more credits per day than the account has",
				slog.String("reason", "opensky_budget"),
				slog.Int("calls_per_day", int(perDay)),
				slog.Int("daily_credits", a.opensky.DailyCredits()),
				slog.Int("recommended_budget", int(budget)))
		}
	}
}

// openSkyInterval is the pause between OpenSky polls: the configured one, or
// the one that spends a share of the daily credits evenly over the airports.
func (a *app) openSkyInterval() time.Duration {
	if a.cfg == nil {
		return 5 * time.Minute
	}
	if a.cfg.OpenSkyInterval > 0 {
		return a.cfg.OpenSkyInterval
	}
	calls := float64(a.opensky.DailyCredits()) * openSkyBudgetShare / float64(max(1, len(a.cfg.Airports)))
	return max(time.Duration(float64(24*time.Hour)/calls), config.MinOpenSkyInterval)
}

func (a *app) interval(name string) time.Duration {
	if name == source.NameOpenSky {
		return a.openSkyInterval()
	}
	return a.cfg.PollInterval
}

// maxAge is how old data of a source may be to still count as current.
func (a *app) maxAge(name string) time.Duration {
	if name == source.NameOpenSky {
		return min(max(a.openSkyInterval()*5/2, time.Minute), 10*time.Minute)
	}
	return max(maxAgeADSB, 3*a.cfg.PollInterval)
}

// fetched is the outcome of one request.
type fetched struct {
	source  string
	airport config.Airport
	res     source.Result
	err     error
	took    time.Duration
}

// poll asks every due source about every airport, merges what came back and
// lets the engine decide.
func (a *app) poll(ctx context.Context) {
	now := a.clock()

	var due []string
	for _, name := range a.cfg.Sources {
		if h := a.health[name]; h != nil && now.Before(h.blockedUntil) {
			continue
		}
		if last, ok := a.lastPoll[name]; ok && now.Sub(last) < a.interval(name)-time.Second {
			continue
		}
		due = append(due, name)
	}

	var (
		mu      sync.Mutex
		results []fetched
		wg      sync.WaitGroup
	)
	for _, name := range due {
		a.lastPoll[name] = now
		src := a.sources[name]
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i, ap := range a.cfg.Airports {
				if i > 0 {
					select {
					case <-ctx.Done():
						return
					case <-time.After(requestGap):
					}
				}
				started := time.Now()
				res, err := src.Fetch(ctx, source.Area{Lat: ap.Lat, Lon: ap.Lon, RadiusNM: ap.RadiusNM + areaMarginNM})
				mu.Lock()
				results = append(results, fetched{source: src.Name(), airport: ap, res: res, err: err, took: time.Since(started)})
				mu.Unlock()
				if source.ErrorKind(err) == source.KindRateLimited {
					return // the other airports would be refused as well
				}
			}
		}()
	}
	wg.Wait()
	if ctx.Err() != nil {
		return // shutting down, not an outage
	}

	now = a.clock()
	var lastErr error
	anyOK := false
	for _, r := range results {
		if r.err != nil {
			a.met.ObservePoll(r.source, source.ErrorKind(r.err), r.took)
			a.pollFailed(r, now)
			lastErr = r.err
			if r.res.Credits >= 0 && r.source == source.NameOpenSky {
				a.met.SetOpenSkyCredits(r.res.Credits)
			}
			continue
		}
		anyOK = true
		a.met.ObservePoll(r.source, obs.ResultOK, r.took)
		if r.res.Credits >= 0 && r.source == source.NameOpenSky {
			a.met.SetOpenSkyCredits(r.res.Credits)
		}
		a.pollSucceeded(r, now)
	}
	if anyOK {
		a.upstreamOK(now)
	} else if lastErr != nil {
		a.upstreamFailing(now, lastErr)
	}

	inputs, gauges, srcGauges := a.assemble(now)
	next, events, notifications := engine.Evaluate(a.state, a.cfg, inputs, now)
	a.state = next
	for _, e := range events {
		a.logEvent(e)
	}
	for _, n := range notifications {
		a.notify(ctx, n, now)
	}

	for i, g := range gauges {
		for _, t := range a.state.Tracks {
			if t.Airport == g.Airport && t.Phase == engine.PhaseApproaching {
				gauges[i].Approaching++
			}
		}
	}
	a.met.Publish(gauges, srcGauges, 3*a.cfg.PollInterval+30*time.Second)
	a.saveState(now)

	obs.Log(a.log, slog.LevelDebug, obs.EventCycleCompleted, "cycle completed",
		slog.Int("sources_polled", len(due)), slog.Int("tracks", len(a.state.Tracks)))
}

// assemble merges the latest data of the sources per airport.
func (a *app) assemble(now time.Time) ([]engine.Input, []obs.AirportGauge, []obs.SourceGauge) {
	var (
		inputs   []engine.Input
		gauges   []obs.AirportGauge
		srcGauge []obs.SourceGauge
	)
	for _, ap := range a.cfg.Airports {
		var groups []aircraft.Group
		for _, name := range a.cfg.Sources {
			f, ok := a.feeds[name][ap.ICAO]
			if !ok || now.Sub(f.at) > a.maxAge(name) {
				continue
			}
			groups = append(groups, aircraft.Group{Source: name, Aircraft: aircraft.Fresh(f.aircraft, now, a.maxAge(name))})
			srcGauge = append(srcGauge, obs.SourceGauge{Source: name, Airport: ap.ICAO, Aircraft: len(f.aircraft)})
		}
		merged := aircraft.Merge(groups)

		g := obs.AirportGauge{Airport: ap.ICAO}
		for _, ac := range merged {
			if !ac.HasPos || airports.DistanceNM(ap.Lat, ap.Lon, ac.Lat, ac.Lon) > ap.RadiusNM {
				continue
			}
			g.Aircraft++
			if _, _, ok := a.cfg.Watch.Match(ac); ok {
				g.Watched++
			}
		}
		gauges = append(gauges, g)
		inputs = append(inputs, engine.Input{Airport: ap.ICAO, Covered: len(groups) > 0, Aircraft: merged})
	}
	return inputs, gauges, srcGauge
}

func (a *app) pollFailed(r fetched, now time.Time) {
	h := a.health[r.source]
	if h == nil {
		h = &health{}
		a.health[r.source] = h
	}
	if h.failures == 0 {
		h.since = now
	}
	h.failures++

	attrs := []slog.Attr{
		slog.String("source", r.source),
		slog.String("airport", r.airport.ICAO),
		slog.String("error", r.err.Error()),
		slog.String("error_kind", source.ErrorKind(r.err)),
		slog.Int("consecutive_failures", h.failures),
		slog.Int64("duration_ms", r.took.Milliseconds()),
	}
	if status := source.ErrorStatus(r.err); status != 0 {
		attrs = append(attrs, slog.Int("http_status", status))
	}
	if source.ErrorKind(r.err) == source.KindRateLimited {
		h.backoff = min(max(2*h.backoff, minBackoff), maxBackoff)
		wait := max(h.backoff, source.RetryAfter(r.err))
		h.blockedUntil = now.Add(wait)
		attrs = append(attrs, slog.Int64("retry_in_seconds", int64(wait/time.Second)))
	}
	obs.Log(a.log, slog.LevelWarn, obs.EventSourcePollFailed, "poll of a source failed", attrs...)
}

func (a *app) pollSucceeded(r fetched, now time.Time) {
	byAirport := a.feeds[r.source]
	if byAirport == nil {
		byAirport = map[string]feed{}
		a.feeds[r.source] = byAirport
	}
	byAirport[r.airport.ICAO] = feed{at: now, aircraft: r.res.Aircraft}

	if h := a.health[r.source]; h != nil && h.failures > 0 {
		obs.Log(a.log, slog.LevelInfo, obs.EventSourceRecovered, "source reachable again",
			slog.String("source", r.source),
			slog.Time("since", h.since),
			slog.Int64("outage_seconds", int64(now.Sub(h.since)/time.Second)),
			slog.Int("failed_polls", h.failures))
		h.failures = 0
	}
	if h := a.health[r.source]; h != nil {
		h.backoff, h.blockedUntil = 0, time.Time{}
	}
	obs.Log(a.log, slog.LevelDebug, obs.EventSourcePolled, "source polled",
		slog.String("source", r.source), slog.String("airport", r.airport.ICAO),
		slog.Int("aircraft", len(r.res.Aircraft)), slog.Int64("duration_ms", r.took.Milliseconds()))
}

// upstreamFailing reports an outage once no source has delivered anything for
// OutageAlertAfter. A single failing source is only a warning.
func (a *app) upstreamFailing(now time.Time, err error) {
	a.outageError = err
	threshold := a.cfg.OutageAlertAfter
	if threshold == 0 {
		threshold = config.DefaultOutageAlertAfter
	}
	if a.outageAlerted || now.Sub(a.lastAnyOK) < threshold {
		return
	}
	a.outageAlerted = true
	obs.Log(a.log, slog.LevelError, obs.EventUpstreamOutage, "no source delivers data",
		slog.Time("since", a.lastAnyOK), slog.String("error", err.Error()))
	if a.cfg.OutageAlertAfter > 0 {
		a.pending[obs.KindOutage] = outageMessage(a.lastAnyOK, err, a.cfg.Location)
	}
}

func (a *app) upstreamOK(now time.Time) {
	if a.outageAlerted {
		obs.Log(a.log, slog.LevelInfo, obs.EventUpstreamRecovered, "sources deliver data again",
			slog.Time("since", a.lastAnyOK),
			slog.Int64("outage_seconds", int64(now.Sub(a.lastAnyOK)/time.Second)))
		if a.cfg.OutageAlertAfter > 0 {
			// If the outage push never got through, the recovery tells the
			// whole story on its own.
			delete(a.pending, obs.KindOutage)
			a.pending[obs.KindRecovery] = recoveryMessage(a.lastAnyOK, now, a.cfg.Location)
		}
	}
	a.lastAnyOK, a.outageAlerted, a.outageError = now, false, nil
}

// notify sends the push for an approach or a landing. A delivered or rejected
// push settles the notification; after a transient failure it stays due and
// the next cycle tries again.
func (a *app) notify(ctx context.Context, n engine.Notification, now time.Time) {
	attrs := []slog.Attr{
		slog.String("kind", string(n.Kind)),
		slog.String("airport", n.Airport.ICAO),
		slog.String("hex", n.Aircraft.Hex),
		slog.String("registration", n.Aircraft.Reg),
		slog.String("type", n.Aircraft.Type),
		slog.String("callsign", n.Aircraft.Callsign),
		slog.String("rule", n.Rule),
		slog.Bool("presumed", n.Presumed),
	}

	msg := aircraftMessage(n, a.cfg, nil)
	if photo, ok := a.photoFor(ctx, n); ok {
		with := aircraftMessage(n, a.cfg, &photo.Photo)
		with.Attachment, with.AttachmentType = photo.data, photo.contentType
		msg = with
	}

	result := a.send(ctx, string(n.Kind), n.Airport.ICAO, msg, attrs)
	if result != obs.ResultFailed {
		a.state.MarkNotified(n.Key, n.Kind, now, a.cfg.Cooldown)
	}
}

type loadedPhoto struct {
	photos.Photo
	data        []byte
	contentType string
}

// photoFor fetches the photo for the notification. The push goes out without
// one when anything about it fails.
func (a *app) photoFor(ctx context.Context, n engine.Notification) (loadedPhoto, bool) {
	if a.photos == nil {
		return loadedPhoto{}, false
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()

	attrs := []slog.Attr{slog.String("hex", n.Aircraft.Hex), slog.String("registration", n.Aircraft.Reg)}
	photo, result, err := a.photos.Lookup(ctx, n.Aircraft.Hex, n.Aircraft.Reg)
	a.met.CountPhoto(result)
	switch result {
	case photos.ResultError:
		obs.Log(a.log, slog.LevelWarn, obs.EventPhotoFailed, "photo lookup failed, sending without photo",
			append(attrs, slog.String("error", err.Error()))...)
		return loadedPhoto{}, false
	case photos.ResultNotFound:
		obs.Log(a.log, slog.LevelInfo, obs.EventPhotoMissing, "no photo for the aircraft", attrs...)
		return loadedPhoto{}, false
	}

	data, contentType, err := a.photos.Image(ctx, photo)
	if err != nil {
		obs.Log(a.log, slog.LevelWarn, obs.EventPhotoFailed, "photo download failed, sending without photo",
			append(attrs, slog.String("error", err.Error()))...)
		return loadedPhoto{}, false
	}
	obs.Log(a.log, slog.LevelInfo, obs.EventPhotoFound, "photo found",
		append(attrs, slog.String("photographer", photo.Photographer), slog.String("result", result))...)
	return loadedPhoto{Photo: photo, data: data, contentType: contentType}, true
}

// sendPending delivers notifications about RareBirdAlert itself.
func (a *app) sendPending(ctx context.Context) {
	for _, kind := range systemKinds {
		msg, ok := a.pending[kind]
		if !ok {
			continue
		}
		if a.send(ctx, kind, "", msg, []slog.Attr{slog.String("kind", kind)}) != obs.ResultFailed {
			delete(a.pending, kind)
		}
	}
}

// send delivers one push and reports the outcome as a log line and a metric.
func (a *app) send(ctx context.Context, kind, airport string, msg pushover.Message, attrs []slog.Attr) (result string) {
	if a.set.DryRun {
		obs.Log(a.log, slog.LevelInfo, obs.EventNotificationDryRun, "dry run, push not sent",
			append(attrs, slog.String("title", msg.Title), slog.String("body", msg.Body),
				slog.Bool("photo", len(msg.Attachment) > 0))...)
		a.met.CountNotification(kind, obs.ResultDryRun, airport)
		return obs.ResultDryRun
	}

	started := time.Now()
	receipt, err := a.push.Send(ctx, msg)
	var perr *pushover.Error
	if errors.As(err, &perr) && perr.Permanent && len(msg.Attachment) > 0 && perr.MentionsAttachment() {
		// A rejected image must not cost the notification. Only the image is
		// retried without: repeating a request that Pushover rejected for
		// other reasons, such as wrong credentials, would only get the
		// client blocked.
		obs.Log(a.log, slog.LevelWarn, obs.EventNotificationRejected, "push with photo rejected, retrying without photo",
			append(attrs, slog.String("error", err.Error()))...)
		msg.Attachment, msg.AttachmentType = nil, ""
		receipt, err = a.push.Send(ctx, msg)
		perr = nil
	}
	attrs = append(attrs, slog.Int64("duration_ms", time.Since(started).Milliseconds()),
		slog.Bool("photo", len(msg.Attachment) > 0))

	switch {
	case err == nil:
		result = obs.ResultSent
		obs.Log(a.log, slog.LevelInfo, obs.EventNotificationSent, "push sent",
			append(attrs, slog.String("title", msg.Title), slog.String("pushover_request", receipt.Request))...)
	case errors.As(err, &perr) && perr.Permanent:
		result = obs.ResultRejected
		obs.Log(a.log, slog.LevelError, obs.EventNotificationRejected, "push rejected, giving up",
			append(attrs, slog.String("error", err.Error()), slog.Int("http_status", perr.Status))...)
	default:
		result = obs.ResultFailed
		if perr != nil && perr.Status != 0 {
			attrs = append(attrs, slog.Int("http_status", perr.Status))
		}
		obs.Log(a.log, slog.LevelWarn, obs.EventNotificationFailed, "push failed, will retry",
			append(attrs, slog.String("error", err.Error()))...)
	}
	a.met.CountNotification(kind, result, airport)
	return result
}

func (a *app) saveState(now time.Time) {
	if _, err := a.store.Save(a.state, now); err != nil {
		// Report once, not every cycle.
		if !a.saveFailing {
			obs.Log(a.log, slog.LevelWarn, obs.EventStateSaveFailed, "cannot write state file, a restart may repeat notifications",
				slog.String("error", err.Error()))
		}
		a.saveFailing = true
		return
	}
	a.saveFailing = false
}

// logEvent writes the engine's account of a state change, including the
// decision and its reason. These lines answer "why was there (no) push?".
func (a *app) logEvent(e engine.Event) {
	attrs := []slog.Attr{
		slog.String("airport", e.Airport.ICAO),
		slog.String("hex", e.Aircraft.Hex),
		slog.String("registration", e.Aircraft.Reg),
		slog.String("type", e.Aircraft.Type),
		slog.String("callsign", e.Aircraft.Callsign),
		slog.String("rule", e.Rule),
		slog.String("rule_value", e.RuleValue),
		slog.Float64("distance_nm", round1(e.DistNM)),
		slog.Int("altitude_agl_ft", e.AGLFt),
		slog.String("sources", strings.Join(e.Aircraft.Sources, ",")),
	}
	decision := []slog.Attr{slog.String("decision", string(e.Decision))}
	if e.Reason != "" {
		decision = append(decision, slog.String("reason", string(e.Reason)))
	}

	switch e.Kind {
	case engine.EventApproach:
		a.met.CountApproach(e.Airport.ICAO, e.Rule)
		obs.Log(a.log, slog.LevelInfo, obs.EventAircraftApproach, "watched aircraft on approach", append(attrs, decision...)...)
	case engine.EventLanded:
		a.met.CountLanding(e.Airport.ICAO, e.Presumed)
		attrs = append(attrs, slog.Bool("presumed", e.Presumed))
		obs.Log(a.log, slog.LevelInfo, obs.EventAircraftLanded, "watched aircraft landed", append(attrs, decision...)...)
	case engine.EventGoAround:
		obs.Log(a.log, slog.LevelInfo, obs.EventAircraftGoAround, "approach broken off", attrs...)
	case engine.EventDeparted:
		obs.Log(a.log, slog.LevelInfo, obs.EventAircraftDeparted, "watched aircraft took off", attrs...)
	case engine.EventLost:
		obs.Log(a.log, slog.LevelInfo, obs.EventAircraftLost, "signal lost during approach, not close enough to count as landed", attrs...)
	}
}

func round1(f float64) float64 { return float64(int(f*10+0.5)) / 10 }

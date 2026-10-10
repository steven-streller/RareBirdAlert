package engine

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

// heartbeat is how often the state is rewritten while only the "last seen"
// timestamps changed. It bounds how stale those can be on disk.
const heartbeat = 5 * time.Minute

// Limits applied to what is read from disk.
const (
	staleApproach = 15 * time.Minute // an approach not seen for this long is over
	staleTrack    = 12 * time.Hour
)

const stateVersion = 1

type stateFile struct {
	Version  int                             `json:"version"`
	SavedAt  time.Time                       `json:"saved_at"`
	Tracks   map[string]Track                `json:"tracks"`
	Seen     map[string]map[string]time.Time `json:"seen,omitempty"`
	Learning map[string]time.Time            `json:"learning,omitempty"`
}

// Store persists the engine state, so that a restart does not repeat
// notifications. An empty path disables persistence.
type Store struct {
	path      string
	signature string
	savedAt   time.Time
}

// NewStore returns a store for the file at path.
func NewStore(path string) *Store { return &Store{path: path} }

// Load reads the state file. A missing file is an empty state. Tracks that
// cannot describe anything current any more are dropped or reset.
func (s *Store) Load(now time.Time) (st State, restored, discarded int, err error) {
	st = NewState()
	if s.path == "" {
		return st, 0, 0, nil
	}
	data, err := os.ReadFile(s.path)
	if errors.Is(err, fs.ErrNotExist) {
		return st, 0, 0, nil
	}
	if err != nil {
		return st, 0, 0, err
	}
	var f stateFile
	if err := json.Unmarshal(data, &f); err != nil {
		return st, 0, 0, fmt.Errorf("decode %s: %w", s.path, err)
	}
	if f.Version != stateVersion {
		return st, 0, 0, fmt.Errorf("%s has unsupported version %d", s.path, f.Version)
	}
	for key, t := range f.Tracks {
		if now.Sub(t.LastSeen) > staleTrack && !now.Before(t.CooldownUntil) {
			discarded++
			continue
		}
		if t.Phase == PhaseApproaching && now.Sub(t.LastSeen) > staleApproach {
			t.Phase, t.Confirm, t.Climb = PhaseNone, 0, 0
		}
		st.Tracks[key] = t
		restored++
	}
	for airport, types := range f.Seen {
		st.Seen[airport] = maps.Clone(types)
	}
	maps.Copy(st.Learning, f.Learning)
	s.signature = signature(st)
	s.savedAt = f.SavedAt
	return st, restored, discarded, nil
}

// Save writes st if something that matters changed, or if the heartbeat is
// due and there is anything to keep fresh.
func (s *Store) Save(st State, now time.Time) (written bool, err error) {
	if s.path == "" {
		return false, nil
	}
	sig := signature(st)
	if sig == s.signature && !s.savedAt.IsZero() &&
		(len(st.Tracks) == 0 || now.Sub(s.savedAt) < heartbeat) {
		return false, nil
	}
	if err := s.Flush(st, now); err != nil {
		return false, err
	}
	return true, nil
}

// Flush writes st unconditionally. A clean shutdown uses it, so that the
// timestamps on disk are current when the next process starts.
func (s *Store) Flush(st State, now time.Time) error {
	if s.path == "" {
		return nil
	}
	data, err := json.MarshalIndent(stateFile{Version: stateVersion, SavedAt: now.UTC(), Tracks: st.Tracks, Seen: st.Seen, Learning: st.Learning}, "", "  ")
	if err != nil {
		return err
	}
	if err := writeAtomic(s.path, append(data, '\n')); err != nil {
		return err
	}
	s.signature = signature(st)
	s.savedAt = now
	return nil
}

// signature is a digest of the parts of the state whose change must reach the
// disk at once. The "last seen" fields are left out, they change every cycle.
func signature(st State) string {
	var b strings.Builder
	for _, key := range slices.Sorted(maps.Keys(st.Tracks)) {
		t := st.Tracks[key]
		fmt.Fprintf(&b, "%s|%s|%t|%t|%t|%t|%d;", key, t.Phase, t.ApproachNotified, t.LandedNotified, t.Presumed, t.Silent, t.CooldownUntil.Unix())
	}
	for _, airport := range slices.Sorted(maps.Keys(st.Seen)) {
		fmt.Fprintf(&b, "seen|%s|%d;", airport, len(st.Seen[airport]))
	}
	for _, airport := range slices.Sorted(maps.Keys(st.Learning)) {
		fmt.Fprintf(&b, "learning|%s|%d;", airport, st.Learning[airport].Unix())
	}
	return b.String()
}

// writeAtomic replaces path with data via a temporary file in the same
// directory, so a crash never leaves a half-written state file behind.
func writeAtomic(path string, data []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".state-*.tmp")
	if err != nil {
		return err
	}
	_, err = tmp.Write(data)
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(tmp.Name(), path)
	}
	if err != nil {
		_ = os.Remove(tmp.Name())
	}
	return err
}

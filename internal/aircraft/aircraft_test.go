package aircraft

import (
	"testing"
	"time"
)

var base = time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC)

func TestMergePrefersFreshestPositionAndFirstIdentity(t *testing.T) {
	adsb := Aircraft{Hex: "3c6444", Reg: "F-GSTA", Type: "A3ST", Lat: 53.0, Lon: 8.5, HasPos: true,
		AltFt: 2000, HasAlt: true, VRateFPM: -700, HasVRate: true, SeenAt: base.Add(-5 * time.Second)}
	opensky := Aircraft{Hex: "3c6444", Callsign: "BGA123", Reg: "WRONG", Lat: 53.01, Lon: 8.6, HasPos: true,
		AltFt: 1900, HasAlt: true, SeenAt: base.Add(-1 * time.Second), OnGround: false}

	got := Merge([]Group{{"adsb.lol", []Aircraft{adsb}}, {"opensky", []Aircraft{opensky}}})
	if len(got) != 1 {
		t.Fatalf("want 1 aircraft, got %d", len(got))
	}
	m := got[0]
	if m.Reg != "F-GSTA" || m.Type != "A3ST" {
		t.Errorf("identity must come from the first source: %+v", m)
	}
	if m.Callsign != "BGA123" {
		t.Errorf("empty identity fields are filled from later sources: %q", m.Callsign)
	}
	if m.Lat != 53.01 || m.AltFt != 1900 || !m.SeenAt.Equal(opensky.SeenAt) {
		t.Errorf("position must come from the fresher source: %+v", m)
	}
	if m.VRateFPM != -700 || !m.HasVRate {
		t.Errorf("a field the fresher source lacks is kept: %+v", m)
	}
	if len(m.Sources) != 2 || m.Sources[0] != "adsb.lol" || m.Sources[1] != "opensky" {
		t.Errorf("sources = %v", m.Sources)
	}
}

func TestMergeKeepsDistinctAircraftSortedAndDoesNotAliasInput(t *testing.T) {
	a := Aircraft{Hex: "bbbbbb", SeenAt: base}
	b := Aircraft{Hex: "aaaaaa", SeenAt: base}
	in := []Aircraft{a, b}
	got := Merge([]Group{{"x", in}})
	if len(got) != 2 || got[0].Hex != "aaaaaa" || got[1].Hex != "bbbbbb" {
		t.Fatalf("got %+v", got)
	}
	got[0].Sources = append(got[0].Sources, "y")
	if in[1].Sources != nil {
		t.Error("merge must not modify its input")
	}
}

func TestFresh(t *testing.T) {
	list := []Aircraft{
		{Hex: "000001", SeenAt: base.Add(-10 * time.Second)},
		{Hex: "000002", SeenAt: base.Add(-90 * time.Second)},
	}
	got := Fresh(list, base, time.Minute)
	if len(got) != 1 || got[0].Hex != "000001" {
		t.Fatalf("got %+v", got)
	}
}

func TestNormalizeHex(t *testing.T) {
	for in, want := range map[string]string{
		"3C6444": "3c6444", " 3c6444 ": "3c6444", "~3c6444": "", "3c644": "", "zzzzzz": "", "": "",
	} {
		if got := NormalizeHex(in); got != want {
			t.Errorf("NormalizeHex(%q) = %q, want %q", in, got, want)
		}
	}
}

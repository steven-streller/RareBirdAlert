package airports

import "testing"

func TestLookup(t *testing.T) {
	for icao, want := range map[string]string{"EDDW": "Bremen", "eddv": "Hannover", " EDDH ": "Hamburg Helmut Schmidt"} {
		a, ok := Lookup(icao)
		if !ok || a.ShortName() != want {
			t.Errorf("Lookup(%q) = %+v, %v; short name want %q", icao, a, ok, want)
		}
	}
	if _, ok := Lookup("KJFK"); ok {
		t.Error("only German airports are in the table")
	}
	if n := Count(); n < 300 {
		t.Errorf("only %d airports in the table", n)
	}
	a, _ := Lookup("EDDW")
	if a.ElevFt != 14 || a.Lat < 53.0 || a.Lat > 53.1 || a.Lon < 8.7 || a.Lon > 8.9 {
		t.Errorf("EDDW = %+v", a)
	}
}

func TestDistanceNM(t *testing.T) {
	bre, _ := Lookup("EDDW")
	haj, _ := Lookup("EDDV")
	// About 89 km as the crow flies, i.e. 48 NM (the road is longer).
	if d := DistanceNM(bre.Lat, bre.Lon, haj.Lat, haj.Lon); d < 46 || d > 50 {
		t.Errorf("Bremen-Hannover = %.1f NM", d)
	}
	if d := DistanceNM(53, 8, 53, 8); d != 0 {
		t.Errorf("same point = %v", d)
	}
	// One minute of latitude is one nautical mile.
	if d := DistanceNM(53, 8, 53+1.0/60, 8); d < 0.99 || d > 1.01 {
		t.Errorf("one arc minute = %.3f NM", d)
	}
}

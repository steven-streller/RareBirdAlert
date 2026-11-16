// Package airports knows the German airports and does the geometry around
// them. The table is embedded; it comes from OurAirports (public domain) and
// is cut down to ICAO code, position, elevation and name.
package airports

import (
	_ "embed"
	"math"
	"strconv"
	"strings"
	"sync"
)

//go:embed de.csv
var table string

// Airport is an aerodrome with its reference point.
type Airport struct {
	ICAO   string
	Name   string
	Lat    float64
	Lon    float64
	ElevFt int
}

var (
	once   sync.Once
	byICAO map[string]Airport
)

func load() {
	byICAO = map[string]Airport{}
	for line := range strings.Lines(table) {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		f := strings.SplitN(line, ",", 5)
		if len(f) != 5 {
			continue
		}
		lat, err1 := strconv.ParseFloat(f[1], 64)
		lon, err2 := strconv.ParseFloat(f[2], 64)
		elev, err3 := strconv.Atoi(f[3])
		if err1 != nil || err2 != nil || err3 != nil {
			continue
		}
		byICAO[f[0]] = Airport{ICAO: f[0], Lat: lat, Lon: lon, ElevFt: elev, Name: f[4]}
	}
}

// Lookup returns the airport with the given ICAO code, in any letter case.
func Lookup(icao string) (Airport, bool) {
	once.Do(load)
	a, ok := byICAO[strings.ToUpper(strings.TrimSpace(icao))]
	return a, ok
}

// Nearest returns the airport of the table that is closest to the position,
// if it is at most maxNM away.
func Nearest(lat, lon, maxNM float64) (Airport, float64, bool) {
	once.Do(load)
	var (
		best     Airport
		bestDist float64
		found    bool
	)
	for _, a := range byICAO {
		d := DistanceNM(lat, lon, a.Lat, a.Lon)
		if d > maxNM {
			continue
		}
		// The ICAO code breaks ties, so that the answer does not depend on
		// the order of the map.
		if !found || d < bestDist || (d == bestDist && a.ICAO < best.ICAO) {
			best, bestDist, found = a, d, true
		}
	}
	return best, bestDist, found
}

// Label is the airport in the words of a logbook: "EDVH Hodenhagen".
func (a Airport) Label() string {
	name := strings.TrimSpace(strings.TrimSuffix(strings.TrimSuffix(a.Name, " Airport"), " Airfield"))
	if name == "" || name == a.ICAO {
		return a.ICAO
	}
	return a.ICAO + " " + name
}

// Count is the number of airports in the table.
func Count() int {
	once.Do(load)
	return len(byICAO)
}

// ShortName is the name without the trailing "Airport".
func (a Airport) ShortName() string {
	name := strings.TrimSpace(strings.TrimSuffix(a.Name, " Airport"))
	if name == "" {
		return a.ICAO
	}
	return name
}

const earthRadiusNM = 3440.065

// DistanceNM is the great-circle distance in nautical miles.
func DistanceNM(lat1, lon1, lat2, lon2 float64) float64 {
	rad := math.Pi / 180
	dLat := (lat2 - lat1) * rad
	dLon := (lon2 - lon1) * rad
	h := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*rad)*math.Cos(lat2*rad)*math.Sin(dLon/2)*math.Sin(dLon/2)
	return 2 * earthRadiusNM * math.Asin(math.Min(1, math.Sqrt(h)))
}

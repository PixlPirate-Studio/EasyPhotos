"""Builds the offline place-name file from GeoNames data (run during docker build).

Usage: python build_geo.py <cities1000.txt> <admin1CodesASCII.txt> <countryInfo.txt> <out.npz>
GeoNames data is CC BY 4.0 (https://www.geonames.org/).
"""
import sys

import numpy as np


def main(cities_file, admin1_file, country_file, out_file):
    admin1 = {}
    with open(admin1_file, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                admin1[parts[0]] = parts[1]
    countries = {}
    with open(country_file, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 5:
                countries[parts[0]] = parts[4]
    lat, lon, city, state, country = [], [], [], [], []
    with open(cities_file, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 11:
                continue
            try:
                la, lo = float(p[4]), float(p[5])
            except ValueError:
                continue
            cc = p[8]
            lat.append(la)
            lon.append(lo)
            city.append(p[1])
            state.append(admin1.get(f"{cc}.{p[10]}", ""))
            country.append(countries.get(cc, cc))
    np.savez_compressed(
        out_file,
        lat=np.array(lat, dtype=np.float32),
        lon=np.array(lon, dtype=np.float32),
        city=np.array(city, dtype=str),
        state=np.array(state, dtype=str),
        country=np.array(country, dtype=str),
    )
    print(f"wrote {len(lat)} places to {out_file}")


if __name__ == "__main__":
    main(*sys.argv[1:5])

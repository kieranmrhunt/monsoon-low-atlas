# Monsoon Low-Pressure System Atlas

Static GitHub Pages atlas for the ERA5-derived LPS v5.4.1 recall-first core South Asian low-pressure-system catalogue.

Upload `index.html`, `assets/`, and `data/` together. Hashed asset filenames are referenced directly by `index.html`.

## Scientific conventions

- `continuity_parent_track_id` identifies a linked meteorological event and is the atlas grain for event counts, matching and climatology.
- Source `track_id` identifies one complete plotting segment. The atlas inserts hard map and GeoJSON breaks between all 4,626 source segments.
- Complete ERA5 physics is resampled at every published centre, including interpolated positions.
- Pressure deficit, vorticity, wind, minimum MSLP depth, and 24 h precipitation have independent catalogue-percentile filters that can be combined.
- Peak classes are persistent ERA5-derived IMD-equivalent classes, not official IMD classifications.
- Cyclone names come from a v5.4.1 parent-event match to NOAA IBTrACS v04r01 NI and WP best tracks. Low-confidence matches remain unnamed.
- State fills use IMD 0.25-degree daily gridded rainfall. Each event's value is the area-mean daily rainfall averaged over UTC dates touched by any of its plotting segments.
- The state/UT filter requires at least one hourly published centre inside the selected administrative boundary.
- The default map draws only positions in the selected months; whole-event lifecycles remain available.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Rebuild the catalogue assets

`scripts/build_ibtracs_crosswalk.py` creates an auditable parent-event-to-IBTrACS match using observed detector fixes only. Official basin CSVs are build inputs and are not deployed. `scripts/build_v541_assets.py` verifies the source hash and release documents, reconciles parent and plotting-segment identities, links state rainfall, and writes hashed core/detail assets plus `atlas-build-manifest.json`. Both scripts require pandas, NumPy and pyarrow.

```powershell
python scripts/build_ibtracs_crosswalk.py `
  --parquet lps_v5.4.1-era5-1940-2025-core.parquet `
  --ibtracs ibtracs.NI.list.v04r01.csv `
  --ibtracs ibtracs.WP.list.v04r01.csv `
  --output data/lps-v5.4.1-ibtracs-v04r01-crosswalk.json
```

```powershell
python scripts/build_v541_assets.py `
  --parquet lps_v5.4.1-era5-1940-2025-core.parquet `
  --release-manifest lps_v5.4.1-era5-1940-2025-core.release-manifest.json `
  --metadata lps_v5.4.1-era5-1940-2025-core.metadata.json `
  --qa lps_v5.4.1-era5-1940-2025-core.qa.json `
  --qa-amendment lps_v5.4.1-era5-1940-2025-core.qa-amendment.json `
  --template-core assets/atlas-core.<previous-hash>.json.gz `
  --ibtracs-crosswalk data/lps-v5.4.1-ibtracs-v04r01-crosswalk.json `
  --rainfall-data path/to/imd-rainfall-dashboard/data/dashboard_data.js `
  --output-dir assets
```

# Monsoon Low-Pressure System Atlas

Static GitHub Pages build for the ERA5-derived LPS v5.4 South Asian low-pressure-system catalogue.

Upload `index.html`, `assets/`, and `data/` together. Hashed asset filenames are referenced directly by `index.html`.

Notes:

- Track geometry uses the v5.4 globally smoothed published centres.
- Complete ERA5 physics is resampled at every published centre, including interpolated track positions.
- Peak classes are ERA5-derived IMD-equivalent classes, not official IMD classifications.
- The default map draws only positions in the selected months; whole-system lifecycles remain available.
- Cyclone names come from a v5.4-specific match to NOAA IBTrACS v04r01 NI and WP best tracks. Low-confidence matches remain unnamed.
- State fills use IMD 0.25-degree daily gridded rainfall. A system's state value is the area-mean daily rainfall averaged over UTC calendar days touched by that track. Cohorts are weighted by active system-days.
- The state/UT filter requires at least one hourly published centre to fall inside the selected administrative boundary.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Rebuild the catalogue assets

`scripts/build_ibtracs_crosswalk.py` first creates an auditable v5.4-to-IBTrACS match using published v5.4 centres at observed-support times. The official basin CSVs are build inputs and are not deployed. `scripts/build_v54_assets.py` validates the release, derives track summaries, links state rainfall, and writes hashed core/detail assets plus `atlas-build-manifest.json`. Both scripts require pandas and pyarrow.

```powershell
python scripts/build_ibtracs_crosswalk.py `
  --parquet lps_v5.4-era5-1940-2025-core.parquet `
  --ibtracs ibtracs.NI.list.v04r01.csv `
  --ibtracs ibtracs.WP.list.v04r01.csv `
  --output data/lps-v5.4-ibtracs-v04r01-crosswalk.json
```

```powershell
python scripts/build_v54_assets.py `
  --parquet lps_v5.4-era5-1940-2025-core.parquet `
  --metadata data/lps-v5.4-era5-1940-2025-core.metadata.json `
  --template-core assets/atlas-core.<current-hash>.json.gz `
  --ibtracs-crosswalk data/lps-v5.4-ibtracs-v04r01-crosswalk.json `
  --rainfall-data path/to/imd-rainfall-dashboard/data/dashboard_data.js `
  --output-dir assets
```

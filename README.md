# Monsoon Low-Pressure System Atlas

Static GitHub Pages atlas for the ERA5-derived LPS v5.4.2 recall-first core South Asian low-pressure-system catalogue.

Upload `index.html`, `assets/`, and `data/` together. Hashed asset filenames are referenced directly by `index.html`.

## Scientific conventions

- `track_id` identifies one hourly-complete, strength-qualified physical event and is the atlas grain for plotting, counts, matching, and climatology.
- `event_id` and `continuity_parent_track_id` are identical aliases of `track_id` in v5.4.2.
- Complete ERA5 physics is resampled at every published centre, including supported interpolated positions.
- Pressure deficit, vorticity, wind, minimum MSLP depth, and 24 h precipitation have independent catalogue-percentile filters that can be combined.
- Peak classes are persistent ERA5-derived IMD-equivalent classes, not official IMD classifications.
- Cyclone names come from a physical-event match to NOAA IBTrACS v04r01 NI and WP best tracks. Low-confidence matches remain unnamed.
- State fills use IMD 0.25-degree daily gridded rainfall. Each event's value is the area-mean daily rainfall averaged over UTC dates touched by its track.
- The state/UT filter requires at least one hourly published centre inside the selected administrative boundary.
- Genesis-region filters use the first published centre. Indian land uses the atlas state/UT polygons; the two ocean bins require a Natural Earth water point in 0–30 degrees north and 45–100 degrees east, split at 77.5 degrees east. Remaining locations are labelled Other.
- The default map draws only positions in the selected months; whole-event lifecycles remain available.
- A search in `YYYY-MM-DD` form highlights the part of every filtered track active on that UTC date and marks its position during the day. Adding an hour, for example `2016-07-16 12:00`, marks every active system at that exact catalogue hour.
- Clicking an already-selected track chooses the nearest hourly centre, reports its UTC time and position, and opens the 850-hPa relative-vorticity background. A track-hour slider and ±1-hour buttons move the selected centre and weather field together.
- The weather background is a visual context layer, not an additional catalogue diagnostic. It shows positive ERA5 850-hPa relative vorticity on a blue-to-red scale; values at or below zero are transparent. The field uses the native 0.25-degree ERA5 grid over 50–110°E, 6°S–40°N.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Weather archive and deployment

The atlas remains a static GitHub Pages site. Monthly weather videos live on the public JASMIN GWS and are fetched directly by the browser with CORS. Each video frame is one UTC ERA5 hour; the browser seeks to the frame selected by the track-hour slider.

`data/vorticity-active-months.csv` contains the 622 months touched by at least one released event. The Slurm array script renders only those months:

```bash
sbatch scripts/build_vorticity_videos.slurm
```

After the array finishes, validate every expected month and write the public manifest and checksums:

```bash
python scripts/build_vorticity_videos.py \
  --output-dir path/to/atlas-weather-v5.4.2-r2 \
  --month-manifest data/vorticity-active-months.csv \
  --container webm \
  --finalize
```

Deploy that directory at the `weatherBase` URL in `index.html`. A deployment is complete only when `manifest.json`, `checksums.sha256`, and all listed `vorticity/YYYY/YYYYMM.{webm,json}` files are public.

## Full catalogue archive

The Data tab links to the versioned Zenodo dataset through the `zenodo` value in the JSON configuration at the bottom of `index.html`. The Zenodo package contains only the full compressed CSV and typed Parquet catalogue. Its rendered Description carries the construction summary and a grouped one-sentence guide to all 319 columns.

## Rebuild the catalogue assets

`scripts/build_ibtracs_crosswalk.py` creates an auditable physical-event-to-IBTrACS match using observed detector fixes only. Official basin CSVs are build inputs and are not deployed. `scripts/build_v542_assets.py` verifies the source hash and release audits, links state rainfall, and writes hashed core/detail assets plus `atlas-build-manifest.json`. Both scripts require pandas, NumPy, and pyarrow.

```powershell
python scripts/build_ibtracs_crosswalk.py `
  --parquet lps_v5.4.2-era5-1940-2025-core.parquet `
  --ibtracs ibtracs.NI.list.v04r01.csv `
  --ibtracs ibtracs.WP.list.v04r01.csv `
  --output data/lps-v5.4.2-ibtracs-v04r01-crosswalk.json
```

```powershell
python scripts/build_v542_assets.py `
  --parquet lps_v5.4.2-era5-1940-2025-core.parquet `
  --release-manifest data/lps-v5.4.2-era5-1940-2025-core.release-manifest.json `
  --metadata data/lps-v5.4.2-era5-1940-2025-core.metadata.json `
  --qa data/lps-v5.4.2-era5-1940-2025-core.qa.json `
  --completion-audit data/lps-v5.4.2-era5-1940-2025-core.completion-audit.json `
  --protocol-amendment data/lps-v5.4.2-era5-1940-2025-core.protocol-amendment-5.json `
  --template-core assets/atlas-core.<previous-hash>.json.gz `
  --ibtracs-crosswalk data/lps-v5.4.2-ibtracs-v04r01-crosswalk.json `
  --rainfall-data path/to/imd-rainfall-dashboard/data/dashboard_data.js `
  --output-dir assets
```

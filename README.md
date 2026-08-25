# Monsoon Low-Pressure System Atlas

Static GitHub Pages atlas for the ERA5-derived LPS v5 South Asian low-pressure-system catalogue.

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
- Genesis and lysis filters use the first and last published centres respectively. Indian land uses the atlas state/UT polygons; All land uses the Natural Earth land mask. The Bay of Bengal and Arabian Sea require a water endpoint in 0–30 degrees north and 45–100 degrees east, split at 77.5 degrees east. Indian Ocean covers water endpoints in 30 degrees south–30 degrees north and 30–120 degrees east, including both named seas.
- The map draws positions in the selected months.
- A search in `YYYY-MM-DD` form highlights the part of every filtered track active on that UTC date and marks its position during the day. Adding an hour, for example `2016-07-16 12:00`, marks every active system at that exact catalogue hour. These time-focus markers are clickable and switch the selected system without changing the focused UTC time or weather frame.
- Exact-hour map views collapse centres separated by less than 150 km so near-duplicate contemporaneous systems do not obscure one another. The selected system is retained first; otherwise the stronger peak class and then peak vorticity determine which centre is shown. This display rule does not alter the catalogue or exported subset.
- Clicking an already-selected track chooses the nearest hourly centre, reports its UTC time and position, and opens the 850-hPa relative-vorticity background. A track-hour slider and ±1-hour buttons move the selected centre and chosen weather field together.
- When a selected system has an accepted IBTrACS association, its matched best track is the dashed green line; the map checkbox can hide that overlay independently.
- Weather backgrounds are visual context layers, not additional catalogue diagnostics. The menu offers positive ERA5 850-hPa relative vorticity at 0.25°, trailing 24-hour accumulated precipitation at 1°, and RH500 derived from 3-hourly ERA5 temperature and specific humidity and interpolated to the hourly slider at 1°. Subset tracks are hidden by default while a weather field is active and can be restored with the map checkbox.
- Climate filters are evaluated at genesis. BSISO-1 uses the APCC daily index during May–October (amplitude below 1 is inactive); ENSO uses the NOAA CPC three-month ONI anomaly centred on the genesis month. Missing and out-of-season values remain explicitly selectable.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Weather archive and deployment

The atlas remains a static GitHub Pages site. Monthly weather videos live on the public JASMIN GWS and are fetched directly by the browser with CORS. Each video frame is one UTC ERA5 hour; the browser seeks to the frame selected by the track-hour slider.

`data/vorticity-active-months.csv` contains the 622 months touched by at least one released event. The Slurm array script renders only those months; its third argument chooses the field:

```bash
sbatch scripts/build_vorticity_videos.slurm data/vorticity-active-months.csv path/to/atlas-weather-v5.4.2-r2 vorticity
sbatch scripts/build_vorticity_videos.slurm data/vorticity-active-months.csv path/to/atlas-weather-v5.4.2-r3 precipitation
sbatch scripts/build_vorticity_videos.slurm data/vorticity-active-months.csv path/to/atlas-weather-v5.4.2-r2 rh500
```

After the array finishes, validate every expected month and write the public manifest and checksums:

```bash
python scripts/build_vorticity_videos.py \
  --field precipitation \
  --output-dir path/to/atlas-weather-v5.4.2-r3 \
  --month-manifest data/vorticity-active-months.csv \
  --container webm \
  --finalize
```

Repeat finalization for each field. Deploy the common directory at the `weatherBase` URL in `index.html`, or use a field-specific entry in `weatherBases`; the videos live below `vorticity/`, `precipitation/` and `rh500/`.

## Rebuild the climate-filter asset

Download the [APCC BSISO index](https://download.apcc21.org/BSISO/BSISO.INDEX.NORM.LY.data) and [NOAA CPC ONI table](https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt), then build the deterministic track-grain payload:

```bash
python scripts/build_climate_filter_asset.py \
  --core assets/atlas-core.14e01a61e44d.json.gz \
  --bsiso path/to/BSISO.INDEX.NORM.LY.data \
  --oni path/to/oni.ascii.txt \
  --output-dir assets
```

Set the resulting hashed filename as `climate` in the JSON configuration at the bottom of `index.html`.

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

## Rebuild the boundary-worldview diagnostic

`scripts/build_boundary_worldviews_figure.py` downloads and checksum-verifies representative Natural Earth 1:10m v5.1.1 admin-0 point-of-view products, then holds the LPS density and colour scale fixed across the 11 distinct India outlines present in all 34 products. Products with identical India geometry are grouped in one panel. The shared groups are Pakistan/Turkey, China/Taiwan, ISO/top-level countries, and Bangladesh plus Argentina, Brazil, Egypt, France, Germany, Greece, Indonesia, Italy, Japan, Morocco, the Netherlands, Palestine, Poland, Portugal, Saudi Arabia, South Korea, Spain, Sweden, Ukraine and Vietnam.

```bash
python scripts/build_boundary_worldviews_figure.py
```

The panel labels describe Natural Earth cartographic worldview products; they are not primary governmental boundary datasets or endorsements by the atlas.

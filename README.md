# Monsoon Low-Pressure System Atlas

Static GitHub Pages atlas for the ERA5-derived LPS v5 South Asian low-pressure-system catalogue.

Upload `index.html`, `assets/`, and `data/` together. Hashed asset filenames are referenced directly by `index.html`.

## Scientific conventions

- `track_id` identifies one hourly-complete, strength-qualified physical event and is the atlas grain for plotting, counts, matching, and climatology.
- The public v5.5.1 files use `track_id` as their single event identifier; internal linker, family and compatibility identifiers are deliberately omitted.
- Complete ERA5 physics is resampled at every published centre, including supported interpolated positions.
- v5.5.1 evaluates six-hour persistence on the complete hourly trajectory. It retains the v5.5 background-removed 125-km P95 circulation-wind thresholds and adds a land-only depression floor requiring at least 95% land fraction, two actual closed standard 2-hPa MSLP contours and 17 kt circulation wind. DD and stronger classes remain wind-classified.
- Event existence and intensity are calibrated against official IMD southwest-monsoon reports. Precipitation, IBTrACS and genesis location do not select events.
- Pressure deficit, vorticity, wind, minimum MSLP depth, and 24 h precipitation have independent catalogue-percentile filters that can be combined.
- Peak classes are persistent ERA5-derived IMD-equivalent classes, not official IMD classifications. In particular, CS means Cyclonic Storm (34–47 kt), not Saffir–Simpson Category 1.
- Cyclone names come from a physical-event match to NOAA IBTrACS v04r01 NI and WP best tracks. Low-confidence matches remain unnamed.
- State fills use IMD 0.25-degree daily gridded rainfall. Each event's value is the area-mean daily rainfall averaged over UTC dates touched by its track. The optional fractional anomaly is `(LPS-period rain / all-record JJAS daily mean) - 1` for each state/UT, using 1901–2025; its fixed red–white–blue scale saturates at −1 and +1.
- The state/UT filter requires at least one hourly published centre inside the selected administrative boundary.
- Genesis and lysis filters use the first and last published centres respectively. Indian land uses the atlas state/UT polygons; All land uses the Natural Earth land mask. The Bay of Bengal and Arabian Sea require a water endpoint in 0–30 degrees north and 45–100 degrees east, split at 77.5 degrees east. Indian Ocean covers water endpoints in 30 degrees south–30 degrees north and 30–120 degrees east, including both named seas.
- The map draws positions in the selected months.
- The LPS-layer menu includes a selected-system-only mode that suppresses subset density, tracks and endpoints while retaining the selected track, selected-hour marker and optional matched IBTrACS line. Other subset tracks are neither drawn nor clickable, leaving state rainfall or weather fields unobstructed. The choice is retained in shareable URLs as `layer=none`.
- The static page makes one country-only request to `api.country.is`, whose provider states that it does not log requests. For an India result, it loads the official Survey of India 1:16-million international outline and removes the conflicting Natural Earth boundary segments around India; Natural Earth remains the source elsewhere. The atlas neither stores the returned IP nor changes analytical geography by visitor location, and falls back to Natural Earth if either request fails.
- A search in `YYYY-MM-DD` form highlights the part of every filtered track active on that UTC date and marks its position during the day. Adding an hour, for example `2016-07-16 12:00`, marks every active system at that exact catalogue hour. These time-focus markers are clickable and switch the selected system without changing the focused UTC time or weather frame.
- Exact-hour searches collapse centres separated by less than 150 km so near-duplicate contemporaneous systems do not obscure one another. During selected-track point focus, the selected centre is always retained, interpolated companion centres are omitted, and observation-supported companions are decluttered at 750 km. These display rules do not alter the catalogue or exported subset.
- Clicking an already-selected track chooses the nearest hourly centre, reports its UTC time and position, and opens the 850-hPa relative-vorticity background. The track-hour slider and ±1-hour buttons move the selected centre but do not switch a weather field on; if the user has already selected a weather field, its frame follows the chosen hour.
- When a selected system has an accepted IBTrACS association, its matched best track is the dashed green line; the map checkbox can hide that overlay independently.
- Weather backgrounds are visual context layers, not additional catalogue diagnostics. The menu offers positive ERA5 850-hPa relative vorticity at 0.25°, trailing 24-hour accumulated precipitation at 1°, and RH500 derived from 3-hourly ERA5 temperature and specific humidity and interpolated to the hourly slider at 1°. Subset tracks are hidden by default while a weather field is active and can be restored with the map checkbox.
- Selected-system composites use unrotated storm-relative geographic coordinates: every contributing field is translated so its contemporaneous published centre lies at relative longitude 0°, relative latitude 0°, with north left at the top. The fields are not rotated into the direction of travel.
- Each precipitation footprint is the lifecycle mean of UTC-day accumulations on a ±10°, 0.25° grid. A touched UTC day's centre is the published position closest to 12 UTC on that day. ERA5 uses hourly mean total precipitation rate accumulated over the day; IMERG Final Run is offered when at least one local contributing day is available (V06 `precipitationCal` or V07 `precipitation`), with its partial temporal coverage reported in the atlas.
- Each vertical composite is a zonal section through 0° relative latitude, averaged over nine equally spaced lifecycle snapshots and interpolated to 27 pressure levels from 1000 to 100 hPa. The controls switch between ERA5 relative vorticity and equivalent potential temperature calculated from temperature and specific humidity following Bolton (1980). Local JASMIN ERA5 model-level analyses supply 1979 onward; the public ARCO ERA5 pressure-level archive supplies earlier snapshots.
- Climate filters are evaluated at genesis. BSISO-1 uses the APCC daily index during May–October (amplitude below 1 is inactive); ENSO uses the NOAA CPC three-month ONI anomaly centred on the genesis month. Missing and out-of-season values remain explicitly selectable.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Weather archive and deployment

The atlas remains a static GitHub Pages site. Monthly weather videos live on the public JASMIN GWS and are fetched directly by the browser with CORS. Each video frame is one UTC ERA5 hour; the browser seeks to the frame selected by the track-hour slider.

`data/vorticity-active-months.csv` contains the 613 months touched by at least one released v5.5.1 event. The Slurm array script renders only those months; its third argument chooses the field:

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

## Storm-centred composite archive

The atlas loads one small gzipped JSON asset only after a user selects a system. The archive therefore adds no bulk transfer to initial page load. It is generated one system per Slurm array task from the v5.5.1 public Parquet catalogue; the submission helper defaults to 200 simultaneous tasks:

```bash
bash scripts/submit_storm_composites.sh
```

After every task finishes, validate all assets against the catalogue and create the archive manifest:

```bash
python scripts/build_storm_composite.py \
  --catalogue ../lps-v5.3-continuity-framework/production/v5.5.1/public-release/lps_v5.5.1-era5-1940-2025-core.parquet \
  --output path/to/atlas-composites-v5.5.1-r1 \
  --manifest
```

Serve that directory from a CORS-enabled public GWS and set `compositeBase` in the JSON configuration at the bottom of `index.html`. Assets live at `tracks/track-<track_id>.json.gz`; the manifest records catalogue completeness, source availability, warnings and checksums.

## Rebuild the climate-filter asset

Download the [APCC BSISO index](https://download.apcc21.org/BSISO/BSISO.INDEX.NORM.LY.data) and [NOAA CPC ONI table](https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt), then build the deterministic track-grain payload:

```bash
python scripts/build_climate_filter_asset.py \
  --core assets/atlas-core.6c7262721551.json.gz \
  --bsiso path/to/BSISO.INDEX.NORM.LY.data \
  --oni path/to/oni.ascii.txt \
  --output-dir assets
```

Set the resulting hashed filename as `climate` in the JSON configuration at the bottom of `index.html`.

## Full catalogue archive

The Data tab links to the versioned Zenodo dataset through the `zenodo` value in the JSON configuration at the bottom of `index.html`. The Zenodo package contains only the full compressed CSV and typed Parquet catalogue. Its rendered Description carries the construction summary and a grouped guide to all 58 public columns. Detector ranking, object segmentation, linker, gap-support, reconciliation, legacy and duplicated centre-audit fields remain in the reproducible internal catalogue rather than the general-user release.

## Rebuild the catalogue assets

`scripts/build_ibtracs_crosswalk.py` creates an auditable physical-event-to-IBTrACS match using observed detector fixes only. Official basin CSVs are build inputs and are not deployed. Because v5.5.1 changes classification only and preserves every v5.5 event ID and observed position exactly, the atlas reuses the audited v5.5 crosswalk. `scripts/build_v55_assets.py` verifies the source hash and release audits, derives state rainfall from the local native IMD 0.25-degree daily grids, and writes hashed core/detail assets plus `atlas-build-manifest.json`. These downstream joins do not modify catalogue selection. The scripts require pandas, NumPy, SciPy, Shapely and pyarrow.

```powershell
python scripts/build_ibtracs_crosswalk.py `
  --parquet lps_v5.5.1-era5-1940-2025-core.parquet `
  --ibtracs ibtracs.NI.list.v04r01.csv `
  --ibtracs ibtracs.WP.list.v04r01.csv `
  --catalogue-version v5.5.1 `
  --output data/lps-v5.5.1-ibtracs-v04r01-crosswalk.json
```

```powershell
python scripts/build_v55_assets.py `
  --parquet lps_v5.5.1-era5-1940-2025-core.parquet `
  --release-manifest data/lps-v5.5.1-era5-1940-2025-core.release-manifest.json `
  --metadata data/lps-v5.5.1-era5-1940-2025-core.metadata.json `
  --qa data/lps-v5.5.1-era5-1940-2025-core.qa.json `
  --completion-audit data/lps-v5.5.1-era5-1940-2025-core.completion-audit.json `
  --protocol-amendment data/lps-v5.5.1-era5-1940-2025-core.calibration-protocol.json `
  --template-core assets/atlas-core.<previous-hash>.json.gz `
  --ibtracs-crosswalk data/lps-v5.5-ibtracs-v04r01-crosswalk.json `
  --selection-table path/to/calibrated_event_selection.parquet `
  --rainfall-grd-dir ~/ncas/data/IMD-gauge `
  --output-dir assets
```

## Rebuild the boundary-worldview diagnostic

`scripts/build_boundary_worldviews_figure.py` downloads and checksum-verifies representative Natural Earth 1:10m v5.1.1 admin-0 point-of-view products, then holds the LPS density and colour scale fixed across the 11 distinct India outlines present in all 34 products. Products with identical India geometry are grouped in one panel. The shared groups are Pakistan/Turkey, China/Taiwan, ISO/top-level countries, and Bangladesh plus Argentina, Brazil, Egypt, France, Germany, Greece, Indonesia, Italy, Japan, Morocco, the Netherlands, Palestine, Poland, Portugal, Saudi Arabia, South Korea, Spain, Sweden, Ukraine and Vietnam.

```bash
python scripts/build_boundary_worldviews_figure.py
```

The panel labels describe Natural Earth cartographic worldview products; they are not primary governmental boundary datasets or endorsements by the atlas.

## Rebuild the India-view boundary asset

`scripts/build_soi_boundary_asset.py` checksum-verifies the official Survey of India International Boundary Vector data, simplifies it for the atlas scale, and clips conflicting Natural Earth boundary segments. Survey of India permits this outline for individual, internal, educational, research and website purposes but not commercial use.

```bash
python scripts/build_soi_boundary_asset.py \
  --archive path/to/Outline_of_India.zip \
  --core assets/atlas-core.6c7262721551.json.gz \
  --output-dir assets
```

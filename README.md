# Monsoon Low-Pressure System Atlas

Static GitHub Pages atlas for the ERA5-derived LPS v5 South Asian low-pressure-system catalogue.

Upload `index.html`, `assets/`, and `data/` together. Hashed asset filenames are referenced directly by `index.html`.

## Scientific conventions

- `track_id` identifies one hourly-complete, strength-qualified physical event and is the atlas grain for plotting, counts, matching, and climatology.
- The public v5.6 files use `track_id` as their single event identifier; internal linker, family and compatibility identifiers are deliberately omitted.
- Complete ERA5 physics is resampled at every published centre, including supported interpolated positions.
- v5.6 evaluates six-hour persistence on the complete hourly trajectory using the unchanged v5.5.1 method: background-removed 125-km P95 circulation-wind thresholds and a land-only depression floor requiring at least 95% land fraction, two actual closed standard 2-hPa MSLP contours and 17 kt circulation wind. DD and stronger classes remain wind-classified.
- v5.6 covers every calendar month. It preserves the validated v5.4 fixed-core route and admits an off-monsoon recall supplement to full ERA5 recomputation; both routes must pass the unchanged strict v5.5 final physical-event gate. Recent seasonal frequency is checked against official IMD annual reports and cyclone recall against IBTrACS. Precipitation, IBTrACS identity, genesis location and year-specific quotas do not select events.
- The analysis clock is independently audited against all 1,032 ordered v5.3 detector months, their per-month manifests, all 86 v5.5 linker blocks and the exact linked-catalogue hash. Release and browser metadata distinguish the full 1940-01-01 to 2025-12-31 analysis period from the first and last retained event positions.
- Pressure deficit, vorticity, wind, minimum MSLP depth, and 24 h precipitation have independent catalogue-percentile filters that can be combined.
- Peak classes are persistent ERA5-derived IMD-equivalent classes, not official IMD classifications. In particular, CS means Cyclonic Storm (34–47 kt), not Saffir–Simpson Category 1.
- Cyclone names come from a physical-event match to NOAA IBTrACS v04r01 NI and WP best tracks. Low-confidence matches remain unnamed.
- State fills use IMD 0.25-degree daily gridded rainfall. Each event's value is the area-mean daily rainfall averaged over UTC dates touched by its track. The optional fractional anomaly is `(LPS-period rain / all-record calendar-month-matched daily mean) - 1` for each state/UT, using 1901–2025; its fixed red–white–blue scale saturates at −1 and +1.
- The state/UT filter requires at least one hourly published centre inside the selected administrative boundary.
- Genesis and lysis filters use the first and last published centres respectively. Indian land uses the atlas state/UT polygons; All land uses the Natural Earth land mask. The Bay of Bengal and Arabian Sea require a water endpoint in 0–30 degrees north and 45–100 degrees east, split at 77.5 degrees east. Indian Ocean covers water endpoints in 30 degrees south–30 degrees north and 30–120 degrees east, including both named seas.
- The map draws positions in the selected months.
- Every active constraint is repeated in an always-visible filter bar and can be removed independently. An exact physical-event ID, atlas label or unique cyclone name is resolved against the complete catalogue: the requested system remains pinned if the surrounding subset excludes it, and the atlas states which filters conflict without silently clearing them. Date and free-text searches continue to define the subset.
- The LPS-layer menu includes a selected-system-only mode that suppresses subset density, tracks and endpoints while retaining the selected track, selected-hour marker and optional matched IBTrACS line. Other subset tracks are neither drawn nor clickable, leaving state rainfall or weather fields unobstructed. The choice is retained in shareable URLs as `layer=none`.
- The static page makes one country-only request to `api.country.is`, whose provider states that it does not log requests. For an India result, it loads the official Survey of India 1:16-million international outline and removes the conflicting Natural Earth boundary segments around India; Natural Earth remains the source elsewhere. The atlas neither stores the returned IP nor changes analytical geography by visitor location, and falls back to Natural Earth if either request fails. After the atlas opens, a discreet visit counter makes one separate request to Counter API; that provider states that it converts IP and browser details into non-reversible hashes and does not sell the data. The counter stays hidden if unavailable and is read-only outside the production hostname.
- A search in `YYYY-MM-DD` form highlights the part of every filtered track active on that UTC date and marks its position during the day. Adding an hour, for example `2016-07-16 12:00`, marks every active system at that exact catalogue hour. These time-focus markers are clickable and switch the selected system without changing the focused UTC time or weather frame.
- Exact-hour searches collapse centres separated by less than 150 km so near-duplicate contemporaneous systems do not obscure one another. During selected-track point focus, the selected centre is always retained, interpolated companion centres are omitted, and observation-supported companions are decluttered at 750 km. These display rules do not alter the catalogue or exported subset.
- Clicking an already-selected track chooses the nearest hourly centre, reports its UTC time and position, and opens the 850-hPa relative-vorticity background. The track-hour slider and ±1-hour buttons move the selected centre but do not switch a weather field on; if the user has already selected a weather field, its frame follows the chosen hour.
- When a selected system has an accepted IBTrACS association, its matched best track is the dashed green line; the map checkbox can hide that overlay independently.
- Weather backgrounds are visual context layers, not additional catalogue diagnostics. The menu offers positive ERA5 850-hPa relative vorticity and trailing 24-hour accumulated precipitation at 0.25°, plus RH500 derived from 3-hourly ERA5 temperature and specific humidity and interpolated to the hourly slider at 1°. Precipitation uses a terrain-inspired kiwi–mango–berry sequential ramp, with near-zero amounts transparent. Subset tracks are hidden by default while a weather field is active and can be restored with the map checkbox.
- Selected-system composites use unrotated storm-relative geographic coordinates: every contributing field is translated so its contemporaneous published centre lies at relative longitude 0°, relative latitude 0°, with north left at the top. The fields are not rotated into the direction of travel.
- Each precipitation footprint is the lifecycle mean of UTC-day accumulations on a ±10°, 0.25° grid. A touched UTC day's centre is the published position closest to 12 UTC on that day. ERA5 uses hourly mean total precipitation rate accumulated over the day; IMERG Final Run is offered when at least one local contributing day is available (V06 `precipitationCal` or V07 `precipitation`), with its partial temporal coverage reported in the atlas.
- Each vertical composite is a zonal section through 0° relative latitude, averaged over nine equally spaced lifecycle snapshots and interpolated to 27 pressure levels from 1000 to 100 hPa. The controls switch among ERA5 relative vorticity, equivalent potential temperature calculated from temperature and specific humidity following Bolton (1980), and mixed-phase relative humidity derived from the same temperature and humidity fields. The archive retains every pressure level; the atlas θₑ view omits the anomalously warm 100-hPa level and uses a fixed 330–370 K blue–white–red scale, while vorticity and RH retain 100 hPa. RH is bounded to 0–100% and uses a fixed sequential yellow–green–blue scale. Local JASMIN ERA5 model-level analyses supply 1979 onward; the public ARCO ERA5 pressure-level archive supplies earlier snapshots.
- Climate filters are evaluated at genesis. BSISO-1 uses the APCC daily index during May–October; MJO uses the Bureau of Meteorology all-season Wheeler–Hendon RMM index; and ENSO uses the NOAA CPC three-month ONI anomaly centred on the genesis month. Amplitudes below 1 are inactive, while missing, pre-index and BSISO out-of-season values remain explicitly selectable.
- On screens up to 760 px wide, the actual map time controls remain directly beneath the map and the selected-system dossier follows the map card in the normal page flow.
- Climatology combines annual systems, exposure-normalised rates or system-days with an 11-year mean and descriptive Theil–Sen slope; monthly class count/share, class frequency by decade, genesis-to-lysis pathways, track density and MJO/BSISO/ENSO composition all honour the current filters. Its storm-centred comparison shows vertical structure or an ERA5 precipitation footprint for the current subset beside the complete catalogue or a user-pinned reference subset on a shared scale.
- Extremes ranks both physical and QA diagnostics and links the top systems to a histogram, class-stratified 5th–95th-percentile boxes, a clickable two-variable relationship plot and genesis-month/peak-class timing for the most extreme decile. These are catalogue diagnostics, not authoritative meteorological records.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Weather archive and deployment

The atlas remains a static GitHub Pages site. Monthly weather videos live on the public JASMIN GWS and are fetched directly by the browser with CORS. Each video frame is one UTC ERA5 hour; the browser seeks to the frame selected by the track-hour slider.

`scripts/submit_v56_weather.sh` derives the active-month manifest from the passing v5.6 public catalogue, hard-links already validated field videos to avoid duplicating large files, renders only missing months, and schedules a validating finalizer for every field:

```bash
bash scripts/submit_v56_weather.sh
```

To validate or rebuild one archive manually:

```bash
python scripts/build_vorticity_videos.py \
  --field precipitation \
  --output-dir path/to/atlas-weather-v5.6-r1 \
  --month-manifest data/v56-weather-active-months.csv \
  --container webm \
  --finalize
```

Repeat finalization for each field. Deploy the common directory at the `weatherBase` URL in `index.html`, or use a field-specific entry in `weatherBases`; the videos live below `vorticity/`, `precipitation/` and `rh500/`.

## Forecast guidance

The Forecasts tab is a lazy static client backed by compact files on the public JASMIN GWS. It ingests GFS and GEFS from NOAA Open Data, and AIGFS and AIGEFS from their explicit operational-version directories on NOAA NOMADS; GFS- and IFS-initialized GraphCast from the NOAA/CIRA AIWP archive; MOGREPS-G from the Met Office AWS Open Data rolling archive; and IFS, IFS ENS, AIFS Single and AIFS ENS from ECMWF Open Data. Users can compare any combination of model tracks on one pannable/zoomable map; weather remains an explicit single-model source so fields from unlike grids or ensembles are never blended accidentally. Every published member is passed through the frozen v5.6 three-pressure-level detector and continuity linker; a lightweight tracker is retained only as an independent QA comparison and never supplies displayed coordinates. Six-hourly model fields are linearly interpolated onto the linker's hourly clock. Tracks fully observed within the forecast must pass the frozen v5.6 final physical-event gate. Only tracks touching initialization or the forecast horizon may scale its duration requirements, while retaining the same strong release-domain evidence requirement. A terminal same-member coalescence guard retains both identities while they are distinct, then publishes one centre after they remain within 50 km for at least 18 hours. On the map, thin solid lines are stitched analyses and thicker solid lines are forecasts; the valid-time slider changes the marker rather than splitting a forecast into past/future styles. All forecast labels remain provisional guidance.

Latest files include positive 850-hPa relative vorticity and trailing-24-hour precipitation on a common 1-degree grid. Relative vorticity is derived from winds only after every provider is resampled to that grid; this avoids mixing IFS native spectral vorticity with wind-derived AIFS/GFS fields. Ensemble weather is the arithmetic member mean, while individual member tracks are shown by default whenever an ensemble run is selected and remain user-toggleable. The storm-evolution panel is visible whenever systems are loaded. It matches the same storm between selected runs from their overlapping storm-centre paths and compares them on a shared valid-time axis: thin curves show every available member's tracked vorticity, bold curves show each model/run system mean, and precipitation remains the honestly labelled deterministic or ensemble-mean field because per-member precipitation grids are not published. Map clicks and the chart marker use the same system group and time slider. Latest exposes available operational initializations from the preceding 72 hours, reusing their permanent archive payloads instead of downloading or storing duplicates; lightweight track-only sidecars are fetched first so unused weather grids do not delay the map. Latest compares selected models at one explicit initialization, or each model's own latest available initialization. AIGEFS retries whole members once after transient NOMADS redirect/throttle failures while retaining the operational 70% ensemble publication floor.

The Archive view supports a typed date/cyclone search and a processed-availability calendar across both operational and TIGGE guidance. Calendar marks are coloured by model, while the valid-time matrix exposes every processed model–lead combination and allows any number of cells to be compared. Named storms automatically open at the valid time with the best overlap. ERA5 v5.6 verification is a separate default-on matrix tile whenever a matched track exists. Operational archive files retain ensemble-mean vorticity and trailing-24-hour precipitation, omit internal tracking QA, and attach the ERA5 track where the catalogue overlaps by at least six hours and passes the documented distance limits. Weather-bearing archive cycles also expose lightweight track-only sidecars; full grids are deferred until a map field or selected-track rainfall evolution needs them. Every operational update writes this permanent archive asset before the duplicate full-weather cycle is allowed to leave the rolling 72-hour Latest namespace. TIGGE payloads remain track-only—including IMD and NCMRWF—and use the same matrix, track controls and verification overlay; they are not presented as current Latest guidance. Each cycle certifies that it retains its complete common provider lead axis—including centre- and cycle-specific TIGGE horizons discovered from the ECDS machine-readable catalogue—together with every track published by the detector/linker and zero-disturbance cycles. A centre such as BoM that omits the required surface fields at t+0 is represented honestly from its first complete lead rather than receiving an invented analysis. Cycle-specific operational model-generation labels come from documented implementation dates where an exact crosswalk exists; otherwise the centre's TIGGE operational family is stated without inventing a patch version.

Displayed forecast paths prepend a compact analysis history when a system is already present at initialization. The service retains 14 days of six-hourly t+0 centres for active guidance and embeds t+0 centres in each processed archive entry. Matching is model- and operational-version-specific: an older forecast signature must reproduce the newer analysed centre, pass distance and motion checks, and be unambiguous relative to contemporaneous systems. The joined history is solid with small centre markers; guidance after the selected valid time is a thinner solid line. Forecast-only genesis therefore remains unstitched, and historical spacing reflects the archive cycles actually processed rather than implying unavailable six-hourly analyses.

The operational historical collection includes source-complete twice-daily Met Office Global cycles from the local BADC archive from 19 March 2016 onward; the completeness planner audits each field and precipitation interval and omits source gaps rather than patching them. NOAA's directly downloadable record begins on 1 January 2017: that operational-archive segment is explicitly labelled **GEFS control** through 25 February 2021, not the full ensemble, and deterministic GFS takes over from the first cloud-archive cycle on 26 February 2021. The NOAA/CIRA archive supplies both GFS- and IFS-initialized GraphCast from 2022 onward, including all frozen detector inputs and six-hour precipitation. The TIGGE NCEP series uses the full NOAA GEFS ensemble from 2017 onward, while the TIGGE ECMWF series uses the public WeatherBench 2 IFS ENS archive for 2018–2022; other cycles and centres continue through ECDS. WeatherBench 2 also supplies deterministic IFS HRES cycles for 2016–2022, including the pressure-level winds and six-hour precipitation needed by the unchanged tracker. Rare isolated member files missing from NOAA's historical object store are reconstructed only when both adjacent six-hour frames are source-present; the affected member and lead are recorded in the cycle payload, while edge or consecutive gaps remain hard failures. The plan samples an initialization 24 hours before each ERA5 v5.6 event and then every 48 hours through that event, collapsing duplicate cycles. TIGGE assets remain a distinct backend collection because model generations, centres, member counts and licences vary through time, but the client presents them in the single Archive view. The manifest records each selection policy, model transition, planned count, completed count and source gap without implying that unprocessed six-hourly initializations are present.

Run an update interactively with:

```bash
bash scripts/run_forecast_update.sh
```

The production wrapper submits the eleven models as independent Slurm jobs and one `afterany` finalizer. The finalizer validates and merges every completed model into the public manifest atomically; a failed model retains its previous Latest cycle. The wrapper submits no duplicate while that finalizer is queued or running:

```bash
bash scripts/submit_forecast_update.sh
```

Production cron invokes that wrapper four times daily after provider dissemination. Source discovery retains the newest cycle whose complete model-specific lead axis is available and falls back through recent six-hour cycles independently for each model. A failed model preserves its prior valid Latest entry and records the failure in `manifest.json`.

Fast local contract tests are independent of the network:

```bash
python -m unittest forecast_pipeline.test_pipeline
```

To seed or repair the rolling initialization window and historical archive in parallel on Slurm:

```bash
bash scripts/submit_forecast_backfills.sh
```

The historical TIGGE ensemble assets remain separate from the operational
archive on disk, although the client combines both in its Archive view. The
original ECMWF event-spanning backfill can be submitted or resumed with:

```bash
bash scripts/submit_tigge_archive_backfill.sh
```

Cycles in the WeatherBench 2 interval bypass ECDS automatically; remaining
ECMWF cycles default to four simultaneous ECDS staging jobs with 12 hours per
task. Each job submits its independent control/perturbed and
pressure-level/surface pieces concurrently. ECDS queue-limit rejections are
retried with exponential backoff instead of failing a cycle and churning
through the array. A known 2016
case must first pass at the complete +360-hour horizon; the large array has an
`afterok` dependency on that canary and later submissions skip the gate once
its full-horizon asset is public. Each task attempts a non-blocking atomic
publication as soon as it finishes; if another publisher owns the manifest
lock, validated staging is retained for the batching publisher instead of
tying up a compute allocation. The finalizer then reconciles the whole plan
and reports any gaps.
`LPS_TIGGE_MAX_ACTIVE`, `LPS_TIGGE_TIME_LIMIT`,
`LPS_TIGGE_CANARY_CYCLE`, `LPS_TIGGE_QUEUE_RETRY_ATTEMPTS` and
`LPS_TIGGE_QUEUE_RETRY_BASE_SECONDS` override the submission settings.

The multi-centre extension covers BoM, CMA, CPTEC, DWD, ECCC, IMD, JMA, KMA,
Météo-France, NCEP, NCMRWF and UKMO in addition to ECMWF. It pins the current
ECDS constraint catalogue, omits centre/cycle combinations lacking a complete
tracker field axis, tests one full-horizon cycle per centre, and chunks the
availability-aware plan below JASMIN's Slurm array limit:

```bash
bash scripts/submit_tigge_multicentre_backfill.sh
```

Every completed cycle is published immediately. DWD, ECCC, ECMWF, KMA, NCEP
and UKMO TIGGE data are CC BY 4.0; BoM, CMA, CPTEC, IMD, JMA, Météo-France and
NCMRWF are CC BY-NC 4.0. The public manifest retains the provider and licence
for every model rather than presenting TIGGE as one homogeneous system.

ECDS control and perturbed forecasts are submitted as one genuine multi-value
request for pressure levels and one for surface fields. This preserves every
member while halving the remote queue footprint; missing accumulated-rainfall
frames are recorded and excluded from the optional precipitation score rather
than interpreted as dry intervals.

The Met Office rolling MOGREPS-G archive can be captured before its oldest
objects expire with:

```bash
bash scripts/submit_mogreps_archive_backfill.sh
```

The newest complete cycle is a full 18-member production canary; the remaining
rolling cycles have an `afterok` dependency on it. Future cycles are then
captured by the normal six-hourly operational updater. The adapter reads only
HDF5 chunks intersecting the atlas domain and selected pressure levels through
anonymous S3 byte ranges, reconstructs vector wind from speed/direction, and
accumulates the native one-hour then three-hour precipitation intervals.

Event-spanning GraphCast archives use the current anonymous NOAA/CIRA object
inventory and can be submitted independently for either initialization stream:

```bash
bash scripts/submit_graphcast_archive_backfill.sh
LPS_GRAPHCAST_MODEL=graphcast-ifs-noaa bash scripts/submit_graphcast_archive_backfill.sh
```

CMA's synchronized TIGGE portal is an independent recovery route for missing
UKMO, IMD and NCMRWF cycles after August 2019 and outside its recent two-month
window. It is asynchronous rather than a guaranteed faster mirror. Create a
recovery plan with `scripts/prepare_cma_tigge_recovery.sh`; submission requires
`LPS_CMA_TIGGE_USERNAME` and `LPS_CMA_TIGGE_PASSWORD`, or a private
`~/.config/monsoon-low-atlas/cma-tigge.json`. The stateful client prepares and
submits bounded applications, downloads staged packages without resubmitting,
and safely exposes their GRIB files. Set `LPS_CMA_TIGGE_RECOVERY_PLAN` and run
`scripts/submit_cma_tigge_cache.sh` to process complete pressure/surface caches
through the same detector/linker and publish them into the TIGGE archive. The
current portal login is HTTP-only from JASMIN; the client therefore refuses to
send credentials unless `LPS_CMA_TIGGE_ALLOW_INSECURE=1` explicitly acknowledges
that transport limitation.

## Storm-centred composite archive

The atlas loads one small gzipped JSON asset only after a user selects a system. The archive therefore adds no bulk transfer to initial page load. It is generated one system per unthrottled Slurm array task from the v5.6 public Parquet catalogue; the submission helper schedules a validating manifest job after every system succeeds:

```bash
bash scripts/submit_storm_composites.sh
```

To add RH to an otherwise complete archive without rereading precipitation or
wind fields, run the same unthrottled array in incremental mode:

```bash
LPS_COMPOSITE_RH_ONLY=1 bash scripts/submit_storm_composites.sh
```

After every task finishes, validate all assets against the catalogue and create the archive manifest:

```bash
python scripts/build_storm_composite.py \
  --catalogue ../lps-v5.3-continuity-framework/production/v5.6/public-release/lps_v5.6-era5-1940-2025-core.parquet \
  --output path/to/atlas-composites-v5.6-r1 \
  --manifest
```

Serve that directory from a CORS-enabled public GWS and set `compositeBase` in the JSON configuration at the bottom of `index.html`. Assets live at `tracks/track-<track_id>.json.gz`; the manifest records catalogue completeness, source availability, warnings and checksums.

The filterable Climatology sections are packed from that validated per-system archive into one lazy, hashed static asset. The packer preserves all 27 pressure levels, coarsens only relative longitude from 0.25° to 0.5°, verifies physical-event ordering against the core atlas asset, and stores signed 16-bit values losslessly at each source field's published scale:

```bash
python scripts/build_subset_section_asset.py \
  --core assets/atlas-core.cefb51e2bde1.json.gz \
  --composite-dir path/to/atlas-composites-v5.6-r1 \
  --output-dir assets \
  --longitude-step 0.5
```

Set the resulting hashed filename as `sections` in the JSON configuration at the bottom of `index.html`. The browser fetches this archive only when Climatology is opened and computes filtered means locally; changing filters does not make a server-side request.

## Rebuild the climate-filter asset

Download the [APCC BSISO index](https://download.apcc21.org/BSISO/BSISO.INDEX.NORM.LY.data), [Bureau of Meteorology RMM index](https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt) and [NOAA CPC ONI table](https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt), then build the deterministic track-grain payload:

```bash
python scripts/build_climate_filter_asset.py \
  --core assets/atlas-core.6c7262721551.json.gz \
  --bsiso path/to/BSISO.INDEX.NORM.LY.data \
  --rmm path/to/rmm.74toRealtime.txt \
  --oni path/to/oni.ascii.txt \
  --output-dir assets
```

Set the resulting hashed filename as `climate` in the JSON configuration at the bottom of `index.html`.

## Full catalogue archive

The Data tab links to the versioned Zenodo dataset through the `zenodo` value in the JSON configuration at the bottom of `index.html`. The Zenodo package contains only the full compressed CSV and typed Parquet catalogue. Its rendered Description carries the construction summary and a grouped guide to all 58 public columns. Detector ranking, object segmentation, linker, gap-support, reconciliation, legacy and duplicated centre-audit fields remain in the reproducible internal catalogue rather than the general-user release.

## Rebuild the catalogue assets

`scripts/build_ibtracs_crosswalk.py` creates an auditable physical-event-to-IBTrACS match using observed detector fixes only. Official basin CSVs are build inputs and are not deployed. `scripts/build_v55_assets.py` verifies the source hash and release audits, derives state rainfall and calendar-month-matched climatologies from the local native IMD 0.25-degree daily grids, and writes hashed core/detail assets plus `atlas-build-manifest.json`. The detail asset carries all public physical diagnostics for the grouped selected-system and subset-evolution controls and is fetched lazily. These downstream joins do not modify catalogue selection. The scripts require pandas, NumPy, SciPy, Shapely and pyarrow.

```powershell
python scripts/build_ibtracs_crosswalk.py `
  --parquet lps_v5.6-era5-1940-2025-core.parquet `
  --ibtracs ibtracs.NI.list.v04r01.csv `
  --ibtracs ibtracs.WP.list.v04r01.csv `
  --catalogue-version v5.6 `
  --output data/lps-v5.6-ibtracs-v04r01-crosswalk.json
```

```powershell
python scripts/build_v55_assets.py `
  --parquet lps_v5.6-era5-1940-2025-core.parquet `
  --release-manifest data/lps-v5.6-era5-1940-2025-core.release-manifest.json `
  --metadata data/lps-v5.6-era5-1940-2025-core.metadata.json `
  --qa data/lps-v5.6-era5-1940-2025-core.qa.json `
  --completion-audit data/lps-v5.6-era5-1940-2025-core.completion-audit.json `
  --protocol-amendment data/lps-v5.6-era5-1940-2025-core.calibration-protocol.json `
  --template-core assets/atlas-core.<previous-hash>.json.gz `
  --ibtracs-crosswalk data/lps-v5.6-ibtracs-v04r01-crosswalk.json `
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

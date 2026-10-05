# Dynamical.org forecast sources

The atlas uses the original provider fields exposed through dynamical.org's
STAC-discovered virtual GRIB/Icechunk datasets. The detector, linker, physical
gates, common one-degree grid, member support threshold and ERA5 verification
are unchanged. No dynamical.org credentials are required.

## Coverage

- **AIFS Single**: the nominal archive begins April 2024, but precipitation
  needed by the frozen detector begins **24 February 2025, 06 UTC**. Earlier
  cycles are ineligible, not interpreted as dry forecasts. See the
  [provider's variable-availability report](https://dynamical.org/catalog/ecmwf-aifs-single-forecast-virtual/validation/).
  All four daily initializations and 61 six-hourly frames through +360 h are
  supported. Historical cycles use dynamical.org; the live ECMWF route stays
  unchanged. Experimental and operational generations are labelled separately.
- **GEFS 35-day** (`gefs-extended`): 31 members, 00 UTC daily, 141 six-hourly
  frames through +840 h, archived from 1 October 2020. The ordinary six-hourly
  GEFS stream remains a separate choice. The extension is the same model,
  not independent evidence when selected alongside GEFS. Beyond two weeks,
  interpret ensemble activity, not individual storm trajectories.

Both products include positive 850-hPa relative vorticity and trailing-24-hour
rainfall maps, individual member tracks and the existing evolution charts.
GEFS interval rainfall is accumulated before tracking; AIFS run-total rainfall
is used directly. Missing fields, incorrect units, lead gaps and grid mismatch
fail validation. Every pressure-level field must cover the common atlas grid.

## Isolated reader

The operational tracker remains in its Python 3.11 environment. Icechunk uses
a separate Python >=3.12 environment at `.forecast-envs/dynamical`, or the
interpreter in `LPS_DYNAMICAL_PYTHON`. Install
`forecast_pipeline/requirements-dynamical.txt` there. Access resolves current
storage through STAC, rather than using the retired `data.dynamical.org` URLs.

The reader writes an identity-checked NPZ with explicit units and finite-cell
fractions. The tracker reads it with `allow_pickle=False`. Failed tracking
retains disposable regional inputs under `.forecast-runs/dynamical-inputs`;
successful tracking removes its input cache. Public archives exclude internal
tracking QA as before. `LPS_DYNAMICAL_INPUT_CACHE` can relocate the cache.

## Scheduling and publication

```bash
# Daily GEFS initializations spanning the latest 72 hours:
bash scripts/submit_dynamical_forecasts.sh recent

# Every eligible initialization in a historical interval:
bash scripts/submit_dynamical_forecasts.sh archive 2025-07-03 2025-07-21

# AIFS only:
LPS_DYNAMICAL_MODELS=aifs bash scripts/submit_dynamical_forecasts.sh archive 2025-02-24 2025-12-31
```

The existing operational cron wrapper invokes the recent planner. It skips
complete published cycles, avoids duplicate recent arrays, retains the daily
72-hour selector and archives every successful cycle. Older AIFS cycles in
the ordinary recent repair automatically use the archive reader.

One Slurm task handles each model-cycle, with four member workers by default;
up to 64 cycles run concurrently. Each completed cycle publishes immediately,
with a finalizer to reconcile concurrent publications. Override with
`LPS_DYNAMICAL_WORKERS` and `LPS_DYNAMICAL_CONCURRENCY` as needed. Initial
canaries are staged separately and inspected before public merging.

## Validation evidence

On 5 October 2026 the 12 July 2025 00 UTC AIFS and GEFS-control input tests
passed every required field and lead: respectively 61 and 141 frames, all
finite on the atlas domain. A NOAA GRIB cross-check at +6 h gave identical
850-hPa u wind, 700-hPa v wind and precipitation; mean-sea-level pressure
differed by at most 0.0000611 hPa (float rounding). The AIFS tracking canary
passed payload QA and published two tracks with two ERA5 matches.

Run the source and pipeline regression tests with:

```bash
python -m unittest discover -s forecast_pipeline -p 'test*.py'
```

# Monsoon Low-Pressure System Atlas

Static GitHub Pages build for the ERA5-derived LPS v5.3.1 fixed-core South Asian low-pressure-system catalogue.

Upload `index.html` and the `assets/` directory together. The hashed filenames are referenced directly by `index.html` and should stay unchanged.

Notes:

- Peak classes are ERA5-derived IMD-equivalent classes, not official IMD classifications.
- Gap-posterior rows preserve linked-track continuity, carry no detector physics and never count as observations.
- The v5.2 IBTrACS/RSMC crosswalk and state-rainfall diagnostics are intentionally not mixed into the v5.3.1 build; they need to be recomputed against the new identities.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Rebuild the catalogue assets

`scripts/build_v531_assets.py` validates the parquet, track summary and release summary before writing hashed core/detail assets and `atlas-build-manifest.json`. It requires pandas and pyarrow.

```powershell
python scripts/build_v531_assets.py `
  --parquet lps_v5.3.1-era5-1940-2025-core-enriched.parquet `
  --track-summary lps_v5.3.1-era5-1940-2025-core-enriched-track-summary.csv `
  --release-summary lps_v5.3.1-era5-1940-2025-core-enriched-summary.json `
  --template-core assets/atlas-core.<current-hash>.json.gz `
  --output-dir assets
```

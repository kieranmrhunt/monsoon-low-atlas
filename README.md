# Monsoon Low-Pressure System Atlas

Static GitHub Pages build for the ERA5-derived LPS v5.3.1 fixed-core South Asian low-pressure-system catalogue.

Upload `index.html` and the `assets/` directory together. The hashed filenames are referenced directly by `index.html` and should stay unchanged.

Notes:

- Peak classes are ERA5-derived IMD-equivalent classes, not official IMD classifications.
- Gap-posterior rows preserve linked-track continuity, carry no detector physics and never count as observations.
- Cyclone names come from a v5.3.1-specific match to NOAA IBTrACS v04r01 NI and WP best tracks. Low-confidence matches remain unnamed.
- State-rainfall diagnostics are intentionally not mixed into the v5.3.1 build; they need to be recomputed against the new identities.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.

## Rebuild the catalogue assets

`scripts/build_ibtracs_crosswalk.py` first creates an auditable v5.3.1-to-IBTrACS match using observed detector fixes only. The official basin CSVs are build inputs and are not deployed. `scripts/build_v531_assets.py` then validates the catalogue inputs before writing hashed core/detail assets and `atlas-build-manifest.json`. Both scripts require pandas and pyarrow.

```powershell
python scripts/build_ibtracs_crosswalk.py `
  --parquet lps_v5.3.1-era5-1940-2025-core-enriched.parquet `
  --ibtracs ibtracs.NI.list.v04r01.csv `
  --ibtracs ibtracs.WP.list.v04r01.csv `
  --output data/lps-v5.3.1-ibtracs-v04r01-crosswalk.json
```

```powershell
python scripts/build_v531_assets.py `
  --parquet lps_v5.3.1-era5-1940-2025-core-enriched.parquet `
  --track-summary lps_v5.3.1-era5-1940-2025-core-enriched-track-summary.csv `
  --release-summary lps_v5.3.1-era5-1940-2025-core-enriched-summary.json `
  --template-core assets/atlas-core.<current-hash>.json.gz `
  --ibtracs-crosswalk data/lps-v5.3.1-ibtracs-v04r01-crosswalk.json `
  --output-dir assets
```

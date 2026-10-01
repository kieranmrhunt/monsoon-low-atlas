# Interactive climate comparisons

The climate tab retains its research-preview status. Interactive filtering does
not change detection, tracking or intensity classification.

## Rebuild event inputs

After rebuilding the climate browser bundle, run:

```sh
python -m cmip6_pipeline.explorer --core assets/atlas-core.cefb51e2bde1.json.gz
node scripts/test_climate_subsets.cjs
node scripts/test_climate_browser.mjs http://127.0.0.1:4173/
```

The builder matches source summaries by catalogue checksum and native-year
coverage, then verifies catalogue checksums. It publishes one compressed event
asset per unique run and an `explorer.json` mapping from summary URLs. Publish the
manifest, index, all referenced summary assets, explorer mapping and all its event
assets together. Never relabel the bundle as approved based on these engineering
checks: scientific admissibility and explicit approval are separate.

The browser regression uses Playwright 1.55 and the matching Chromium test runtime;
`PLAYWRIGHT_MODULE` and `CHROMIUM_PATH` can override their locations. Serve the
repository on localhost before running it, or pass the deployed atlas URL.
The test saves a mobile viewport capture and uses an isolated temporary browser
profile. It does not alter user browser data. On this host, use `/dev/shm` rather
than Chromium's `--disable-dev-shm-usage` fallback to the small `/tmp` filesystem:
the fallback can crash the renderer during a long graphics-heavy test.

## Interpretation

- Regional track selection means a published centre passes through a selected
  state/region, or the genesis/lysis centre lies there. A box can also be drawn or
  entered. Membership in states and 1-degree density cells is computed using the
  original centres. Box coordinates are rounded to 0.001 degree in the browser
  asset. Entire selected tracks, not cropped track fragments, enter diagnostics.
- Monthly selection uses native-calendar genesis month and overrides the season.
  Annual rates include all years, including years with zero qualifying systems.
  Missing event physics and annual means with no qualifying events remain missing.
- Annual event reductions match `summarise.py`: event statistics are reduced
  within each year, then years receive equal weight. Models receive equal weight;
  category proportions are calculated within each model before ensemble averaging.
- Custom track subsets use 600 deterministic, independent annual bootstrap
  resamples for each model's 90% change intervals. The unfiltered precomputed
  intervals are retained otherwise. Custom ensemble confidence limits are not
  fabricated by averaging individual bounds: model spread and individual
  intervals are displayed separately.
- Rainfall attribution and gridded precipitation composites retain all LPSs in
  the selected models. Their regional selector changes where rainfall is measured;
  it does not use the track subset. A notice appears when these scopes differ.
  Track-centred rainfall statistics for the actual subset remain available in
  Overview and Relationships. Historical ERA5 skill is also whole-domain.
- Highlighting a model is distinct from applying model inclusion checkboxes.
  Highlighting leaves the ensemble unchanged. Applying a model selection
  recomputes comparisons, maps, agreement, class shares and rainfall point estimates.

## Interaction checks

Check desktop and mobile views; model/variable selection; clicking heatmap cells,
scatter points and regional rainfall points; map zoom, pan, state picking and box
selection; explicit empty subsets; restoring shared URLs with cleared local
storage; and CSV/PNG exports from every subview. CSV exports identify scope and
include the selected-view URL. Check transient fetch recovery on cached reloads.

The source-reconciliation test compares all published single-run annual values
and density grids against event-derived results, plus ensemble central estimates,
category proportions and density sign-agreement counts. A successful test is not
an endorsement of model historical skill or a substitute for scientific review.

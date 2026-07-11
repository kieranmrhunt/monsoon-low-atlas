# Monsoon Low-Pressure System Atlas

Static GitHub Pages build for the ERA5-derived LPS v5.2 South Asian low-pressure-system track catalogue.

Upload `index.html` and the `assets/` directory together. The hashed filenames are referenced directly by `index.html` and should stay unchanged.

Notes:

- Peak classes are ERA5-derived IMD-equivalent classes, not official IMD classifications.
- State rainfall diagnostics are concurrent state-mean rainfall while a system exists, not storm-attributable rainfall.
- The split build loads compressed catalogue payloads from `assets/*.json.gz` and decompresses them in modern browsers.
- Raw IMD tracks are not redistributed; official New Delhi aggregate grades and intensities are treated separately from atlas-derived diagnostics.

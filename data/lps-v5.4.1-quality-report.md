# v5.4.1 full-run quality report

## Technical summary

The 1940–2025 v5.4.1 full run is complete and is suitable for promotion as a
release candidate. The final catalogue contains 525,579 hourly rows describing
2,750 linked meteorological events. Unsupported stitches are represented as
4,626 complete plotting segments without discarding any observed centre from a
retained parent event.

All 17 final release checks pass. Every published row has complete re-sampled
ERA5 physics, including relative humidity and precipitation. The catalogue has
no duplicate track-times, no missing timestamps inside a publication segment,
no isolated precipitation collapse detected by the release gate, and no
observed centre duplicated across tracks.

The catalogue was promoted to the INCOMPASS GWS v5.4.1 public track-data
location on 2026-07-17. The prior v5.4 release remains alongside it unchanged.

## Release-candidate evidence

| Measure | Result |
|---|---:|
| Coverage | 1940–2025 |
| Published hourly rows | 525,579 |
| Observed rows | 374,398 (71.24%) |
| Posterior/interpolated rows | 151,181 (28.76%) |
| Linked meteorological parent events | 2,750 |
| Complete plotting/publication segments | 4,626 |
| Publication splits | 1,876 |
| Posterior rows removed at unsupported stitches | 36,185 |
| Duplicate track-times | 0 |
| Missing times inside publication segments | 0 |
| Final QA checks passed | 17 / 17 |

`continuity_parent_track_id` is the meteorological-event identity and should be
used for event counts, matching, POD, and event-level statistics. `track_id` is
the complete plotting-segment identity and should be used to draw tracks
without crossing an unsupported jump. A split does not imply a second storm.

## Continuity and geometry

The final maximum hourly translation speed is 29.445 m s-1, below the
29.5 m s-1 publication cap. The maximum observed-centre projection residual is
124.982 km, below the 125 km cap. Only 33 of 525,579 rows lie outside the
nominal physics buffer, a fraction of 0.0063%, and all have valid source
provenance.

The low-path-efficiency tail is substantially smaller at the scientifically
appropriate event grain:

| Catalogue/grain | Path efficiency below 0.1 | Rate |
|---|---:|---:|
| v5.3.1 events | 150 / 2,918 | 5.14% |
| v5.4.1 parent events | 73 / 2,750 | 2.65% |
| v5.4.1 plotting segments | 153 / 4,626 | 3.31% |

The segment count is higher because 1,876 unsupported publication stitches
were split. Comparing the raw number of v5.4.1 segments with unsplit v5.3.1
events is therefore not meaningful; both the event-level and segment-level
rates improve.

## Physics and precipitation

All 525,579 rows have complete final-centre ERA5 diagnostics. Required-field
null counts are zero for:

- relative humidity at 850, 700, and 500 hPa and through the deep layer;
- specific humidity at the same levels and through the deep layer;
- temperature at 850, 700, and 500 hPa;
- hourly and 24-hour precipitation plus 24-hour valid-history count;
- vorticity, wind, pressure deficit, land fraction, and orography.

There are zero invalid physics-provenance rows, zero materially negative
precipitation values, zero isolated hourly precipitation collapses, and zero
isolated 24-hour precipitation collapses. The 15,983 rows with fewer than 24
valid antecedent precipitation hours are explicitly marked by
`precip_24hr_valid_hours`; they are not silently treated as missing or zero.

## Intensity

Persistent intensity is recalculated from final-centre physics using six hours
of contiguous observed support. At publication-segment grain, peak categories
are:

| Persistent peak category | Segments |
|---|---:|
| Low | 1,680 |
| Depression | 1,741 |
| Deep depression | 870 |
| Cyclonic storm | 301 |
| Severe cyclonic storm | 32 |
| Very severe cyclonic storm | 2 |

These counts describe plotting segments, not independent meteorological events.
For climatological storm counts, aggregate by `continuity_parent_track_id`.
The recall-first ordering is preserved: liberal candidates are linked first,
the complete parent event is strength-filtered, and unsupported plotting
stitches are split only afterwards.

## Named-system audit

| Earlier track | Result in v5.4.1 |
|---|---|
| DD 1940 13 / ID 507 | 139 of 252 observed centres retained in one parent event across three plotting segments |
| DD 1941 11 / ID 1173 | all 161 observed centres retained in one complete segment |
| DD 1968 08 / ID 21864 | all 142 observed centres retained in one parent event across two complete segments |
| DD 1989 12 / ID 37933 | 71 of 116 observed centres retained in one parent event across three plotting segments |
| DD 2006 07 / ID 50723 | pathological parent absent from the strength-filtered core |

## QA amendment

The initial QA job failed two checks because the frozen validator treated
publication segments as if they were independent meteorological events. That
made DD 1968 08 appear only 75.35% retained in its primary segment even though
all 142 observed centres were retained in one parent event across two segments.
It also compared the absolute number of split v5.4.1 segments with unsplit
v5.3.1 events.

The validator was amended to evaluate named-system retention and the absolute
low-efficiency tail at `continuity_parent_track_id` grain, while retaining a
segment-rate guardrail. The catalogue was not modified. Slurm job 40081501
then passed all 17 checks. The frozen protocol, old validator hash, amended
validator hash, catalogue hash, and rerun QA hash are recorded in
`protocol-amendment-parent-aware-qa.json`.

## Remaining limitations and release advice

- There are short publication segments because a retained parent can be split
  at an unsupported stitch. These should not be interpreted as separate weak
  storms or independently filtered without considering the parent identity.
- There are 137 selected stitch-ledger edges with only one endpoint surviving
  the strength-filtered core. They cannot connect two retained core parents and
  do not create cross-parent joins, but remain a useful diagnostic for a future
  linker revision.
- Depression-class events still outnumber low-class events at parent-event
  grain. v5.4.1 improves the low-to-depression balance relative to v5.4, but the
  distribution should remain visible in the release diagnostics.
- The comparison-figure suite should be regenerated from this exact public file
  so that maps and summary counts use the new parent/segment semantics
  consistently.

## Reproducibility

The release-candidate SHA-256 is
`ef24b1614155102547b1a5fa3abe803cc8f679c50ee00e64936187bcd4391737`.
The machine-readable release manifest, final QA JSON, physics summary,
continuity summary, intensity summary, frozen protocol, and protocol amendment
are stored alongside this report.

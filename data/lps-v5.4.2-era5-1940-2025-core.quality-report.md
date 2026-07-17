# v5.4.2 full-run quality report

## Release result

The 1940–2025 v5.4.2 catalogue satisfies the requested physical continuity
contract: every retained event has one row at every hour from its first to last
published time. A candidate-free interval is retained only when it can be
filled retrospectively under the frozen short-interpolation or reanalysis-
extremum support rule. Otherwise, the intervening posterior rows are removed
and the observations on either side receive distinct event identities.

The final release contains 368,925 hourly rows in 2,980 strength-qualified
physical events. All 25 final release gates and all 22 independent completion
checks pass.

| Measure | Result |
|---|---:|
| Coverage | 1940–2025 |
| Published hourly rows | 368,925 |
| Observed rows | 300,348 (81.41%) |
| Retrospectively filled rows | 68,577 (18.59%) |
| Strength-qualified physical events | 2,980 |
| Liberal association families represented | 2,509 |
| Duplicate event-times | 0 |
| Non-hourly steps inside events | 0 |
| Unsupported filled rows | 0 |
| Required physics nulls | 0 |

## Identity semantics

`track_id`, `event_id`, and `continuity_parent_track_id` are identical in this
release. They identify an hourly-complete physical event and are suitable for
plotting, event counts, matching, and climatology.

`association_family_id` is retained only as provenance from the deliberately
liberal linker. A family can contain several physical events after rejected
bridges are split, so it must not be used as a storm identity.

This differs deliberately from v5.4.1, where a parent identity could span
several plotting segments. v5.4.2 does not publish an identity across an
unsupported interval.

## Retrospective gap decisions

The full physics catalogue initially contained 143,169 posterior rows across
32,574 candidate-free blocks. Every row was re-sampled from ERA5 at its
published centre before the final decision.

- Gaps of at most six hours may use bounded endpoint interpolation only when
  the hourly path is no faster than 20 m s-1 and reanalysis fields are complete.
- Intermediate blocks require at least 65% supported hours.
- Blocks of at least 12 hours require at least 75% supported hours.
- Supported hours require either vorticity of at least 5 within 225 km or a
  pressure deficit of at least 4 hPa with an MSLP minimum within 250 km.
- No retained supported block may contain more than four consecutive
  unsupported hours.

The validator retained 29,399 blocks, rejected 3,175 blocks, and removed
52,341 unsupported posterior rows. Rejected blocks produced distinct identities
on either side. After the strength filter, the release contains 68,577 filled
rows in 21,653 audited blocks; every one passes its applicable support rule.

## Strength filtering after linking

Candidate generation and linking remain recall-first. Strength is assessed
only after physical segmentation, using final-centre reanalysis diagnostics.
Each published event has:

- at least 30 hours between its first and last observed centre;
- at least 12 observed positions; and
- at least three qualifying positions in the release domain.

Of 8,005 physical segments entering this gate, 2,980 pass and 5,025 weak
segments are rejected. This preserves the requested ordering: weak candidate
points can help form a linked system, but weak physical events do not enter the
core release.

## Geometry and path quality

The maximum hourly translation speed is 19.960 m s-1, below the 20 m s-1
publication cap. The maximum distance between a published observed centre and
its detected centre is 124.896 km, below the 125 km cap. There are no duplicate
observed candidate identifiers.

The path-efficiency-below-0.1 rate is 2.92% (87 of 2,980 events), compared with
5.14% (150 of 2,918 events) in v5.3.1. Acceleration is retained as a diagnostic
rather than a hard gate because development experiments found that a universal
acceleration cap made valid multi-hour geometry infeasible. The maximum
diagnosed value is therefore not a release constraint.

## Physics and precipitation

Every published row has final-centre ERA5 provenance and complete values for
vorticity, wind, pressure deficit, hourly and trailing-24-hour precipitation,
relative and specific humidity at 850, 700, and 500 hPa, deep-layer humidity
and vorticity, temperature at 850, 700, and 500 hPa, orography, and land
fraction.

There are no null required fields, materially negative precipitation values,
incomplete 24-hour precipitation histories, isolated hourly precipitation
collapses, or isolated 24-hour precipitation collapses.

## Persistent intensity

Persistent intensity is recalculated from the final-centre physics with six
hours of contiguous observed support.

| Persistent peak category | Physical events |
|---|---:|
| Low | 655 |
| Depression | 1,249 |
| Deep depression | 746 |
| Cyclonic storm | 293 |
| Severe cyclonic storm | 35 |
| Very severe cyclonic storm | 2 |

The low-to-depression balance remains a catalogue limitation worth retaining
in downstream diagnostics; it is not hidden by the continuity correction.

## Named-case audit

- DD 1941 11 / old ID 1173 retains all 161 observed centres in one complete
  event.
- DD 1968 08 / old ID 21864 has all 142 observed centres accounted for before
  strength filtering. The coherent July event retains 107 observations over
  185 hours and 45 qualifying points. Four precursor fragments contain 3, 10,
  3, and 19 observations, zero qualifying points, and are correctly rejected
  as distinct weak events after three unsupported bridges.
- Pathological DD 2006 07 / old ID 50723 remains absent from the core.
- The final named-case disposition audit verifies that an unpublished observed
  candidate is never silently lost between physical splitting and strength
  selection.

## Reproducibility

The release catalogue SHA-256 is
`e5f3a3c8a6ea1c662c21383aa97de957dfd1d7a9cf07e4b3c962d5e52994401e`.
The frozen protocol, five explicit amendments, gap decision ledger, physical
segment strength table, final QA report, independent completion audit, and
release manifest remain in `production/v5.4.2`.

The first final-QA result is archived under
`production/v5.4.2/attempts/r7_legacy_retention_gate`. That failure exposed an
obsolete legacy-retention assertion; it did not change the catalogue. The
amended QA explicitly proves the physical disposition of every named-case
observation and passes.

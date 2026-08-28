# LPS v5.5 release quality summary

LPS v5.5 contains 287,773 hourly positions in 1,678 physical events from 17 May
1940 to 3 December 2025. All events have a complete hourly time axis, unique
event/time rows and complete ERA5 diagnostics at every published centre.

Version 5.5 replaces the broad environmental maximum-wind classification used
in v5.4.2 with the persistent 95th percentile of the 10-m wind anomaly within
125 km, after subtracting the mean 300–500 km background vector. Event
existence additionally requires globally applied duration, observation,
pressure, vorticity and trajectory-coherence support. Precipitation is not an
input to event selection or intensity.

For the like-for-like June–September population whose observed trajectory
enters 65–100°E, 0–32°N, the 2015–2025 means are 13.82 systems yr⁻¹, 9.45
low-type systems yr⁻¹ and 4.36 depression-or-stronger systems yr⁻¹. The
corresponding official IMD means are 13.55, 8.91 and 4.64, giving relative
errors of 2.0%, 6.1% and 5.9%. Exact depression, deep-depression and
cyclonic-storm-or-stronger means are each within 12% over the same period.
Development (2020–2025) and locked validation (2015–2019) independently pass
the pre-registered 25% broad-class gates. Mooley–Shukla is retained only as a
soft historical context check.

The complete Gulab–Shaheen lifecycle is one event, including its east-of-100°E
origin, Indian crossing and Arabian Sea re-intensification. All twelve 2025
source months are present, and the catalogue reproduces the 19-event 2025 IMD
ledger under the stated comparison population.

The atlas's IBTrACS names and IMD state-rainfall displays are downstream
contextual joins. They do not alter catalogue membership, trajectories or
intensity classes.

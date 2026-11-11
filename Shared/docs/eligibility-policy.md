# Observed-only eligibility policy v1

This policy defines which examples and delivery windows are usable **without
filling any production values**. It is implemented in `Backend/app/eligibility.py`
as a standalone audit. It does not relax the application's strict upload/training
validator or implement the future day-ahead prediction engine.

## Valid observations and availability

An observed interval is valid if its timestamp is explicitly offset-aware and
quarter-hour aligned, and its production is a finite number between 0 and 1e12
MW inclusive (the existing engine's numerical bound). Zero is valid; a blank,
negative, nonnumeric or non-finite value is not. Duplicate, unordered, naive and
off-grid timestamps are rejected. An absent quarter-hour has false membership
in an internal validity grid. No generation rows or values are inserted into
the input file, exported as repairs, or inferred from forecasts or capacity.

Availability is an **assumption**, calculated as:

```text
available_at(interval_start) = interval_start + 15 minutes + extra_delay
```

The CLI requires an explicit nonnegative `--availability-delay-minutes`. It has
no silently assumed production latency. The main audit below uses 15 minutes
after completion; 0- and 60-minute scenarios are also reported for transparency,
not selected using forecasting performance. No historical first-publication or
revision-vintage guarantee is established.

At an 18:00 Brussels issue time with extra_delay=15, the latest admissible
interval starts at **17:30 local time**. It completes at 17:45 and is assumed
available at 18:00. A 17:45-start observation is not yet available under this
scenario. A value assumed available exactly at the deadline is admitted.

## One-step ML training examples

The current future strategy refits a one-step model, then predicts recursively.
This audit therefore considers labels at quarter-hour target `t` and observation
origin `o = t - 15 minutes`. It does not count direct multi-hour target models.

An example is eligible only when all three conditions hold:

1. All **97** source observations from `o - 24 hours` through `o`, inclusive,
   exist and are valid. The oldest one supplies the 24-hour lag; the latest 96
   supply the full rolling-day statistics. This requirement matches the actual
   `features_at_origins` implementation and is tested against it.
2. The target at `t` exists and is valid.
3. The target's assumed availability is no later than the fitting cutoff.
   Feature observations are earlier and consequently also available by that cutoff.

Candidate targets are in the explicitly selected local training date range,
including targets whose preceding context lies outside the downloaded extent;
those candidates are reported as insufficient history, not silently omitted.
No feature is calculated across a missing observation with a shortened window.

Each example has separate boolean flags and overlapping exclusion reasons:
insufficient history extent, missing/invalid history, target outside the input,
missing/invalid target, and label unavailable by the fitting cutoff. Reason counts
can overlap; they must not be summed to obtain the number of excluded examples.
Calendar features need no generation measurements beyond these dependencies.

## Day-ahead window feasibility

For each delivery date, the audit enumerates the following local day at
quarter-hour granularity, with an 18:00 local issue time on the previous day.
The internal UTC target grid has 92, 96 or 100 intervals according to DST.
This is a membership/feasibility check, not a forecast scheduler or prediction.

Three questions are kept separate:

| Mask | Requirement |
|---|---|
| `persistence_issuable` | Latest admissible observation exists and is valid |
| `history_ml_issuable` | Complete 97-observation context at that origin is valid |
| `complete_delivery_targets` | Every delivery interval has a valid reference target |
| `persistence_scoreable_window` | Persistence input is usable and delivery targets are complete |
| `history_ml_scoreable_window` | ML context is usable and delivery targets are complete |
| `common_scoreable_window` | Intersection for persistence and this ML feature profile |

An unobserved future target never prevents **issuing** a forecast. It only
prevents fully scoring that window retrospectively. Full-day comparisons must
use common windows with all target intervals; do not compare one model on a
convenient subset or silently drop just the missing 23:45 target from a day.

These masks do not prove a model has enough training samples, establish a
per-origin refitting schedule, or certify accuracy. In particular, 2024 window
counts are diagnostic feasibility, not out-of-sample evaluation using a model
fitted at the end of 2024. Eligibility for a local-clock seasonal baseline is
deferred until its ambiguous/repeated DST-time rules are defined.

## Results on the saved study snapshot

Input: 70,176 rows, 288 missing values, no absent timestamps. Training target
dates: 2024. Fitting cutoff: **31 December 2024 at 18:00 Europe/Brussels**,
before the first validation issue. Extra delay: **15 minutes**.

| One-step training audit | Count |
|---|---:|
| Candidate targets | 35,136 |
| Eligible examples | 7,368 |
| Excluded examples | 27,768 |

Eligible target runs, expressed in Brussels local time:

- **23 January 2024, 00:15–23:30:** 94 examples.
- **17 October 2024, 00:15 through 31 December 2024, 17:30:** 7,274 examples.

The earliest 97 candidates lack the full required source extent. The missing
targets affect subsequent 24-hour contexts, so their impact is much larger than
288 isolated exclusions. A further 25 end-of-year labels are not yet available
at the chosen fitting deadline. These causes overlap.

| Delivery dates | Candidate days | Complete targets | Persistence issuable | ML issuable | Common fully scoreable |
|---|---:|---:|---:|---:|---:|
| Training-year diagnostic, 2024 | 366 | 78 | 365 | 76 | 75 |
| Validation, Jan–Sep 2025 | 273 | 273 | 273 | 273 | 273 |
| Reserved test, Oct–Dec 2025 | 92 | 92 | 92 | 92 | 92 |
| Total | 731 | 443 | 730 | 441 | 440 |

The common full windows are **18 October 2024 through 31 December 2025**.
ML inputs are also available for 24 January 2024, but that delivery day has a
missing target, so it cannot enter a complete-day comparison. The earlier
18:00 issue explains why window recovery follows training-example recovery.

The 2025 masks inspect data completeness only; no models, errors, tuning or
reserved-test performance were examined. The proposed splits are unchanged.
Usable 2024 training is concentrated in autumn/winter and a small January run;
it does not represent a full year's seasonal coverage. This limitation must be
addressed explicitly before drawing all-season performance conclusions.

Delay sensitivity, keeping the fitting cutoff fixed:

| Extra delay after interval completion | Eligible training examples | Common full windows |
|---|---:|---:|
| 0 minutes | 7,369 | 440 |
| 15 minutes | 7,368 | 440 |
| 60 minutes | 7,365 | 440 |

The number of complete windows is unchanged in these scenarios; this does not
establish that the numerical predictions or their errors would be unchanged.

## Reproduce and inspect individual decisions

From `Backend`:

```powershell
.\.venv\Scripts\python.exe -m app.eligibility --input ../Shared/data/elia/study-2024-2025/normalized.csv --output ../Shared/data/elia/eligibility-policy-v1-delay15 --availability-delay-minutes 15 --fit-cutoff 2024-12-31T18:00:00+01:00
```

Choose a new output directory if that run already exists. The CLI produces:

- `training-eligibility.csv`: one row per training candidate, with origin,
  target, assumed label-availability time, flags and exclusion reasons.
- `window-eligibility.csv`: one row per delivery date, with issue time, latest
  admissible observation, reference counts, model-profile masks and reasons.
- `summary.json`: input checksum, policy version, explicit assumptions and counts.

It emits no generation values and performs no training, inference, imputation or
source download. Hash the input before/after to confirm it is unchanged. Use
`--training-start`, `--training-end`, `--window-start` and `--window-end` for
other explicit local-date ranges. Defaults follow the proposed 2024–2025 study;
end dates are exclusive. All output timestamps retain explicit UTC offsets.

The checked-in [audit summaries](research/eligibility-policy-v1/) bind the results
to the input checksum. Full example/window CSVs remain in the ignored local data
directory. The original [source investigation](missing-production-investigation.md)
and [benchmark protocol](belgian-benchmark.md) provide provenance and limitations.

## Next integration

Commit 5 now provides the shared [day-ahead window planner](dayahead-windows.md),
including full bridge/delivery timelines. Eligibility uses its calendar definition;
the observations and training rules described here are unchanged.

Verification for this delivery: **22 focused eligibility cases passed; 123 backend
tests passed in total**. Coverage includes exact feature-dependency matching,
gap propagation, fitting-cutoff equality, completed-interval availability,
0/7/15/60-minute delays, both DST delivery days, future-label separation,
invalid/absent observations, rejected ambiguous clocks and overwrite refusal.
Independent regeneration reproduced the full 15-minute-delay CSVs and summary
byte-for-byte; the original normalized-input SHA-256 was unchanged. The existing
non-failing Starlette/HTTPX deprecation warning remains. No frontend code changed.

The later benchmark engine must apply these masks before generating features
or fitting, construct predictions without using newly observed actuals after
issue, and score identical complete windows. It must bridge from the latest
available observation through any unpublished intervals to the delivery day.
The current app's strict whole-series validation remains intact until this
separate benchmark path is implemented and verified.

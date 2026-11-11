# Day-ahead window planner v1

Commit 5 implements reusable Brussels day-ahead schedules and exports their
complete timelines. The planner is executable on real-data eligibility masks;
baseline production forecasts/backtesting remain commit 6 and ML evaluation
remains later work. No production values are generated or filled here.

## Schedule contract

- Input: a Brussels delivery calendar date and explicit extra publication delay
  in whole, nonnegative minutes after interval completion.
- Issue: **18:00 Europe/Brussels on the preceding date**.
- Observation origin: the latest UTC quarter-hour `o` satisfying
  `o + 15 minutes + extra_delay <= issue_at`. The origin remains an interval
  start, distinct from both issue time and assumed availability time.
- Delivery: every interval start in `[local midnight, next local midnight)`,
  stored as unique UTC timestamps. The final interval ends at the exclusive
  boundary; no target leaks into the next local day.
- Recursive timeline: every quarter-hour from `origin + 15 minutes` through
  the final delivery interval, without jumping directly to midnight.
- Bridge: timeline intervals before delivery midnight. Delivery: the remaining
  92, 96 or 100 intervals. Only delivery intervals enter day-ahead scoring.

The planner uses local calendar boundaries and elapsed-time UTC stepping. Autumn's
two 02:00 local times are distinguished by their `+02:00` and `+01:00` offsets;
spring's nonexistent 02:00 hour is not invented. Year boundaries and leap days
use the same contract. There is no hardcoded 96-point day or 24-hour maximum.

With extra_delay=15, the latest observation starts at 17:30 local time and is
assumed available at 18:00. The first bridge interval starts at 17:45. Its
start precedes the issue time, but its measurement is not yet assumed available:
this is an unpublished interval to predict while advancing state, not an observed
value to fill. Leads from issue can therefore be negative for bridge points;
all leads from the observation origin are positive. Exported lead columns name
their reference clock explicitly.

## Exact example lengths

All examples below use extra_delay=15 and issue at the previous local 18:00.

| Delivery date | Day type | Delivery intervals | Bridge intervals | Total recursive steps | Last target lead from issue | Last target lead from origin |
|---|---|---:|---:|---:|---|---|
| 15 January 2025 | Normal | 96 | 25 | 121 | 29h 45m | 30h 15m |
| 30 March 2025 | Spring DST | 92 | 25 | 117 | 28h 45m | 29h 15m |
| 26 October 2025 | Autumn DST | 100 | 25 | 125 | 30h 45m | 31h 15m |

First delivery target: six hours after issue and six hours thirty minutes after
the latest observation origin in these examples. The first bridge point is step
1; first delivery point is step 26. A later recursive predictor must execute
every bridge step using its own predictions, without refreshing observed inputs
after issue. The planner itself has no model or generation array.

## Shared implementation and eligibility

`Backend/app/dayahead_schedule.py` supplies the reusable `window_for_day` object,
UTC/local target clocks, bridge/delivery timelines, step roles and lead metadata.
The existing eligibility audit now calls this same calendar implementation;
there are not two independently maintained definitions of a day-ahead window.

`Backend/app/dayahead.py` connects that schedule to the
[observed-only eligibility policy](eligibility-policy.md):

- `history_ml`: emit a timeline only when its complete 97-observation context
  is usable at the selected observation origin.
- `persistence`: emit a timeline when the latest admissible observation is valid.
- Record every candidate day, including exclusions and their input reasons.
- Never use future target completeness to decide whether to emit a plan. That
  flag is exported separately for later retrospective scoring decisions.

This input check does not certify sufficient fit examples or a per-origin fitting
schedule. Historical 2024 windows remain diagnostic schedules, not out-of-sample
evaluation using a model fitted at the end of 2024. Seasonal-baseline mapping and
its DST source rules remain part of subsequent baseline work.

## Run on the saved Belgian data

From `Backend`:

```powershell
.\.venv\Scripts\python.exe -m app.dayahead --input ../Shared/data/elia/study-2024-2025/normalized.csv --output ../Shared/data/elia/dayahead-plan-v1-history-ml --start 2024-01-01 --end 2026-01-01 --availability-delay-minutes 15 --profile history_ml
```

Use a new output directory if that run already exists. For a future delivery day,
an input file containing only the necessary history can produce the schedule;
no future actuals are required. Input timestamps must still be unique, ordered,
offset-aware and quarter-hour aligned.

| Output | Contents |
|---|---|
| `windows.csv` | Every candidate day, eligibility flags/reasons, issue/origin, delivery boundaries, true leads and counts |
| `trajectory.csv` | One row per bridge/delivery step of each input-eligible plan, with per-window unique UTC and explicit-offset local timestamps |
| `summary.json` | Completion status, profile, explicit delay assumption, input hash, counts and output checksums |

No artifact contains predicted or actual production columns. An all-excluded
run still exports a header-only trajectory and an explanatory windows ledger.
An interrupted run leaves `complete=false`; an existing run is never overwritten.
Raw/normalized input data is not modified. Full local exports remain Git-ignored
under `Shared/data/elia/`; small [archived examples and summary](research/dayahead-schedule-v1/)
are checked in for review.

The trajectory row key is **(issue_at, target_timestamp_utc)**. A bridge point in
one issue can share a target timestamp with another issue's delivery point; those
are separate forecast events and must not be deduplicated by target timestamp alone.

## Real-data verification

The complete 2024–2025 audit, history_ml profile and 15-minute extra delay, gives:

| Result | Count |
|---|---:|
| Candidate delivery dates | 731 |
| Excluded for input eligibility | 290 |
| Planned delivery windows | 441 |
| Planned windows with complete reference targets | 440 |
| Delivery intervals across planned windows | 42,340 |
| Bridge intervals | 11,025 |
| Total trajectory intervals | 53,365 |

Planned days comprise 438 normal days, two autumn DST days and one spring DST
day. The extra issuable-but-not-fully-scoreable day is 24 January 2024, as already
identified in commit 4. It remains planned; excluding it on the basis of future
target missingness would improperly mix issue-time feasibility with scoring.

The original input hash remains unchanged. The earlier eligibility summaries
and full per-row masks must remain identical after the calendar refactor. No
models or reserved-test forecast errors are inspected in this delivery.

Verification: **144 backend tests passed**, including 21 new schedule/export cases
and the 22 eligibility cases. Tests cover DST gaps/repeats, leap/year boundaries,
true leads, publication-delay bridge lengths, explicit role separation, future
days without actuals, incomplete targets, input-profile exclusions, empty exports,
reproducible artifacts, overwrite refusal and overlapping targets across issues.
The old eligibility summaries at delays 0/15/60 and full 15-minute-delay CSVs
reproduce unchanged. Output SHA-256 checks and the unchanged source hash were
verified against the complete real-data plan. The existing non-failing
Starlette/HTTPX deprecation warning remains; no frontend code changed.

## Boundaries and next work

Commit 6 now implements [baseline prediction and validation](baseline-validation.md)
against these schedules. The archived planning artifacts above remain schedules;
prediction/error artifacts are separate outputs of the baseline engine.

The existing upload UI, fixed-lead backtest and maximum-24-hour generic future
endpoint retain their contracts. This CLI schedule is the separate national
benchmark foundation; it does not silently reinterpret those earlier scores or
extend the UI's forecast horizon.

Next, implement baseline prediction and historical evaluation against these
schedules, preserving bridge/delivery roles and identical complete-day scoring
sets. The subsequent ML engine must use the observed-only training masks and
execute the full recursive trajectory. Until then, the exported timelines are
plans, not electricity production forecasts or accuracy results.

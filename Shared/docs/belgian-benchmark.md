# Belgian solar benchmark — protocol v1

## Research question

How accurately can SolarCast predict Belgium's national measured-and-upscaled
photovoltaic power for the next local calendar day, using inputs available at
18:00 Europe/Brussels on the preceding day?

This is a proposed evaluation protocol, not a performance result. The existing
synthetic demo and its scores do not establish Belgian forecast accuracy.

## Target and clocks

- Target: Elia's national measured-and-upscaled solar production estimate, MW,
  at 15-minute interval starts. It is not an exhaustive meter reading or MWh.
- Region: Belgium. In the new schema select gridconnectiontype=Total; do not
  sum Total, TSO and DSO or national and provincial records.
- Store timestamps in UTC after parsing their explicit offsets. Interpret date
  arguments and delivery-day boundaries in Europe/Brussels.
- Issue at 18:00 local time; deliver every interval of the following local day.
  Normal days have 96 intervals; DST transition days have 92 or 100.
- This requires approximately 6–30 hours of lead time (DST changes alter the
  elapsed range). It differs from the app's current maximum 24-hour trajectory.
  Later work must implement and evaluate this strategy before reporting scores.

## Sources and migration

Research checked 11 November 2026 using live metadata and sample API records:

| Source | Selection | Production | Capacity metadata |
|---|---|---|---|
| ods032, before 26 October 2026 local midnight | region=Belgium | measured | monitoredcapacity |
| ods209, from 26 October 2026 local midnight | region=Belgium, gridconnectiontype=Total | measured_upscaled | monitoredcapacity |

The midnight boundary follows the records observed during research; the importer
will report missing intervals rather than assume the publication switch is seamless.
Monitored capacity is retained as such, not relabelled installed capacity.
The mapping establishes schema compatibility, not proof of methodological continuity.
Audit the transition separately before pooling it for model evaluation.

Sources: [legacy dataset and migration notice](https://opendata.elia.be/explore/dataset/ods032/),
[new historical dataset](https://opendata.elia.be/explore/dataset/ods209/),
[API](https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods209).
Metadata says downloads/API are UTC, but sampled API records carried explicit
local offsets: parse offsets instead of assuming a clock.

## Availability and reference forecasts

Interval-start production is not known at the start of that interval. The earliest
physical completion is start + 15 minutes; publication delays can extend this.
Later evaluation must expose an availability-delay assumption and only use completed
intervals before the issue time. No operational lag is established by these downloads.

Historical data retrieved today may include revisions. Retrospective evaluation
with an assumed delay is not evidence of real-time performance. Operational
evaluation requires timestamped snapshots of the inputs actually available then.
The historical API's record timestamp is a target interval, not a publication time.

Preserve dayahead11hforecast and dayaheadforecast (labelled 11:00 and 18:00
day-ahead by Elia) in a separate reference file. Neither these values nor
mostrecentforecast are model predictors in this protocol. Labelled publication
times do not alone prove historical availability at an exact deadline. Compare
Elia only after auditing deadline alignment, timezone meaning and revisions.

## Proposed study and evaluation

- Training: local delivery dates 1 January–31 December 2024.
- Validation/model selection: 1 January–30 September 2025.
- Reserved final test: 1 October–31 December 2025.
- Dates are subject to the ingestion quality audit, before fitting or scoring.
  This change documents and downloads data; it does not train models or inspect
  test-period performance. Record changes to the protocol before evaluation.
- Never random-split. Purge training examples whose labels cross the fitting
  cutoff; fit transforms only on training data. Evaluate complete day-ahead
  trajectories from identical issue times and score identical targets per model.
- Compare persistence and seasonal baselines first, then existing ML models.
  Baseline definitions must respect availability and local-day/DST alignment;
  do not assume the current elapsed-24-hour baseline is a local-clock baseline.
- Report MAE, RMSE and bias (prediction minus reference), in MW; sample counts,
  excluded dates and coverage must accompany results. Report all-interval and
  daylight/lead breakdowns later. Nighttime zeros can hide daytime errors.
- Reserve final-test results until model choices are frozen. This seasonal final
  quarter cannot alone establish all-season generalisation.

## Quality and reproducibility

The [observed-only eligibility policy](eligibility-policy.md) now defines and
audits one-step training examples and day-ahead input/reference feasibility.
It keeps source nulls intact and makes interval-end publication-delay assumptions
explicit. It does not implement or score the future day-ahead forecasting engine.

Keep raw responses, metadata, exact queries, retrieval times and SHA-256 checksums.
Rebuilding the same snapshots must produce byte-identical processed artifacts.
A later API download may differ because of revisions.

Report missing/non-finite/negative production, duplicates, unexpected timestamps,
gaps, missing capacity/reference values and source boundaries. Never interpolate,
forward-fill, substitute forecasts for missing production, or turn missing into zero.
Do not silently deduplicate or merge conflicting records. A defective series must
not be labelled ready for forecasting. Forecast-only future rows are not actuals.

## Attribution

Source: Elia Transmission Belgium SA, Elia Open Data, datasets ods032/ods209.
The [Elia licence](https://opendata.elia.be/pages/licence/) identifies
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
SolarCast filters national totals, normalises timestamps, renames columns and
reports quality; it is independent of Elia. Retain this credit with derived data.

## Delivery boundaries

Commits 1–2 provide this protocol and an offline-capable CLI data pipeline with
CSV upload compatibility. Forecast-engine changes, weather, performance claims,
deployment and automated daily collection remain subsequent work.

Commit 4 adds observed-only eligibility. Commit 5 implements the shared
[Brussels day-ahead schedule](dayahead-windows.md) and full recursive timelines.
The existing app's generic future endpoint retains its 24-hour limit; baseline
generation and benchmark evaluation against the new schedule remain subsequent work.

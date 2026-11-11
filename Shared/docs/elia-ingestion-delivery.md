# Elia ingestion delivery — 11 November 2026

## Implemented

- Documented the Belgian next-local-day 18:00 forecasting protocol separately
  from the application's existing rolling fixed-lead evaluation.
- Added `python -m app.elia download` and `rebuild` without new dependencies.
- Preserved raw metadata, count-query responses and bounded JSON exports with
  retrieval timestamps, exact URLs and SHA-256 verification.
- Added legacy/current mappings, national Total selection, UTC normalization,
  separate reference forecasts, CSV outputs and quality reports.
- No training, test-performance inspection, imputation or forecast-engine changes.

## Live-data audit

The first run used Brussels local dates `[2024-01-01, 2026-01-01)`.
All **70,176** expected timestamps were present, with no duplicate, unexpected
or off-grid records. There were **288 missing production values**, all in 2024
at local **23:45**, across 288 dates. There were no non-finite or negative supplied
production values. Capacity and both day-ahead reference columns were populated.

These blanks are present in the downloaded source snapshots. Their pattern does
not justify replacing them with zero. The series is marked **not ready** and
the current application validator rejects it for forecasting. This is a source
quality finding, not an importer failure. The proposed split remains provisional;
investigate the source issue and define an explicit modelling policy before fitting.

The separate transition audit used `[2026-10-25, 2026-10-28)` local dates:
**292** intervals, comprising 100 legacy records and 192 new-schema records.
The 25-hour DST day and source switch were contiguous. No target, capacity or
reference quality defects were found. This validates this sample's schema and
continuity, not long-term equivalence of the two production estimation methods.

Both runs are preserved locally under the ignored `Shared/data/elia/`
directory (`study-2024-2025` and `transition-2026-10`). The audit describes those
snapshots; future downloads may differ.

## Verification

- Backend regression suite: **101 passed** (85 existing plus 16 ingestion cases).
- Rebuilt each real-data run twice offline; generated artifact hashes were identical.
- Uploaded both complete CSVs through FastAPI TestClient, exercising the actual
  upload and validation endpoints. Study: 70,176 rows, 1,910,959 bytes, invalid
  due to 288 missing targets. Transition: 292 rows, 7,779 bytes, healthy.
- Focused tests cover both mappings, filters, DST days, offset conversion,
  duplicates, gaps, missing/invalid/negative values, bounded retries, count
  mismatch, incomplete runs, checksum mismatch, overwrite refusal and CSV validation.
- A pre-existing non-failing Starlette/HTTPX deprecation warning remains.

Bounded exports with independent count checks replace the originally proposed
pagination. This avoids offset ceilings; truncated exports fail explicitly.
There are no frontend changes, browser UI checks or deployment in this delivery.

## Next work

Follow-up: the [missing-production investigation](missing-production-investigation.md)
confirms the source nulls with fresh JSON/CSV and regional samples; their root
cause remains unknown. It preserves the missing values and recommends quantifying
eligibility before changing the study period.

Investigate the 23:45 missing production values and document the evaluation
policy without using final-test performance to choose it. Then implement the
full day-ahead issuance/window strategy with explicit measurement availability,
chronological validation and the reserved test period. The current 24-hour
future-inference workflow is not yet this protocol's approximately 30-hour lead.

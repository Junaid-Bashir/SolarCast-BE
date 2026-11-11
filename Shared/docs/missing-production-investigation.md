# Investigation: missing Belgian solar production at 23:45

**Checked 11 November 2026. Status: source blanks confirmed; root cause unknown.**

This investigation changes no production values, forecast models, split dates or
missing-data eligibility rules. It concerns the data reference, not model accuracy.

## Confirmed findings

The original 2024–2025 download contains 70,176 distinct quarter-hour timestamps
and 288 `measured: null` source values. We verified the checksums of all its raw
files and analysed the raw records directly, rather than relying on processed CSVs.

| Observation | Evidence |
|---|---|
| All 288 blanks are at 23:45 Europe/Brussels in 2024 | Full saved national snapshot |
| First affected date: 1 January; last: 15 October 2024 | Full saved national snapshot |
| Every date in that inclusive span is affected except 22 January | 288 affected dates out of 289 calendar dates |
| Missing times shift with the local clock | 89 at 22:45 UTC; 199 at 21:45 UTC |
| Both adjacent observations are 0 MW for every blank | All 288 cases have the preceding and following records exactly 15 minutes away |
| Capacity and both day-ahead reference fields remain populated | All 288 affected national records |
| The fresh API returns the same complete set of 288 null timestamps | New filtered JSON export compared against the saved snapshot |
| No production blanks occur in the downloaded 2025 portion | Data-quality audit only; no test-period model performance examined |

The original snapshot is bound to the manifest hash recorded in the
[machine-readable summary](research/elia-missing-2024/summary.json).

## Independent format and regional checks

We requested 30 national observations: 23:30, 23:45 and the following 00:00
around ten selected dates. Fresh API JSON and CSV export values match each
other and the original snapshot for every selected field.

| Local date | National production at 23:45, MW | Purpose |
|---|---:|---|
| 2024-01-01 | null | First affected date |
| 2024-01-21 | null | Before isolated unaffected day |
| 2024-01-22 | 0 | Unaffected day inside the pattern |
| 2024-01-23 | null | Pattern resumes |
| 2024-03-30 | null | Before spring clock change |
| 2024-03-31 | null | After spring clock change |
| 2024-07-01 | null | Summer clock |
| 2024-10-15 | null | Last affected date |
| 2024-10-16 | 0 | First day after affected span |
| 2025-01-01 | 0 | Later control |

At 23:45 on 1 January, 1 July and 15 October 2024, all 14 returned geographic
categories have null production. On 22 January and 16 October all 14 have zero.
These are overlapping national/regional/provincial categories, not 14 independent
sites; they must not be summed. The 70 regional records are a sample, not a survey
of all regional values for all 288 affected dates. This sample offers no regional
measurement with which to reconstruct the missing national observations.

## What we can and cannot conclude

**Confirmed:** the blanks are already present in Elia's public source. They are
not created by SolarCast CSV conversion, missing timestamps, UTC normalization
or an incorrect national filter. JSON null and CSV empty cells agree. A simple
timezone-conversion error does not explain this source-level pattern, which tracks
23:45 local time across the spring DST transition.

**Plausible but unconfirmed:** a shared publication, aggregation or day-boundary
processing issue. The regular local-time position and shared regional pattern
are consistent with this class of explanation. They do not identify a particular
pipeline step, software defect, incident or correction date.

**Not established:** the exact underlying production, the reason 22 January is
unaffected, why the pattern stops after 15 October, or whether historical records
will later be corrected. Zero-valued adjacent observations and forecast fields
do not turn a missing measurement into a measured zero.

We searched the public portal/news material for solar missing-data and 23:45
explanations. The material inspected did not establish a cause for this exact
pattern. Some portal pages returned a general technical-issue notice during the
check; we did not treat that current notice as an explanation for 2024 records.
Elia has not been contacted as part of this investigation.

## Decision for the next implementation

Implemented follow-up: the [observed-only eligibility policy](eligibility-policy.md)
now provides explicit masks and quantifies usable examples/windows without filling
source values. Its data audit leaves the proposed split dates unchanged.

Keep all 288 values missing in source and normalized data. Do not replace them
with zero, an adjacent observation, a published forecast or a sum of overlapping
regions. The current strict validator should continue to reject the full series
as ready for forecasting.

The next commit should introduce explicit availability/eligibility masks and
report how many training examples and delivery windows are usable. A missing
value can invalidate later lag/rolling features as well as its own target; 288
missing observations does not imply only 288 unusable examples. Quantify that
effect before deciding whether to revise the proposed study period. Any future
imputation experiment must be labelled as such and evaluated separately from
observed-only results, without choosing policy from reserved-test performance.

## Reproduce offline

From `repository root` with the original local download available:

```powershell
Backend\.venv\Scripts\python.exe Shared/docs/research/summarize-missing-production.py Shared/data/elia/study-2024-2025 Shared/docs/research/elia-missing-2024
```

The standard-library script is read-only. It verifies the original raw checksums
and every recheck response checksum, compares the entire missing timestamp set,
checks the 30 national JSON records against the source snapshot, and compares
their numeric/null values with the CSV export. Its output matches the committed
summary. Running without the final argument produces the original-snapshot audit
only. Another download may differ after source revisions.

The checked-in evidence is a small selected extract, not the full two-year dataset.
Its [manifest](research/elia-missing-2024/manifest.json) contains the exact request
URLs and SHA-256 values. The null-only export covers the entire study range;
the other requests are representative samples. Repeating those URLs is an online
recheck, not an offline reproduction of the archived response. Git attributes
preserve response bytes so checksum verification survives line-ending conversion.

Verification: the full snapshot analysis and archived-evidence comparison ran
twice with identical summaries. This documentation/research commit changes no app
code; the existing application regression suite was not rerun.

## Sources and attribution

- [Elia ods032 dataset and definitions](https://opendata.elia.be/explore/dataset/ods032/)
- [Elia records API](https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods032/records)
- [Elia public news](https://opendata.elia.be/pages/news/)
- [Archived JSON, CSV, exact queries and checksums](research/elia-missing-2024/manifest.json)
- [Elia Open Data licence](https://opendata.elia.be/pages/licence/), CC BY 4.0

Source: Elia Transmission Belgium SA, Elia Open Data, ods032. SolarCast selects
and summarizes records and converts times to Europe/Brussels for interpretation;
source production values are preserved. Independent research, not an
Elia-endorsed explanation of the missing data.

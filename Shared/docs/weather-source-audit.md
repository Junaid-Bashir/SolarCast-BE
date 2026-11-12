# Archived weather source audit — commit 8

Checked 12 November 2026. This is a sample data/provenance audit, not a solar
forecast evaluation. No prediction accuracy is measured here.

## Decision

Use **Open-Meteo Previous Runs, `dwd_icon_eu`, `_previous_day2`** as the initial
historical research candidate, subject to a full-coverage check during ingestion.
All 99 sampled location/date combinations passed the data checks. This is a
**conditional retrospective source decision**, not proof of exact historical
publication times, complete study coverage or live-model equivalence.

The model identifier must be explicit: no `best_match`, automatic provider
fallback or replacement with observed weather/reanalysis. Raw responses and
returned grid coordinates must be frozen alongside query and retrieval metadata.

## Sample and results

We requested three hourly forecast variables: shortwave radiation (W/m²), total
cloud cover (%) and 2 m temperature (°C), all at a nominal 48-hour lead.
Eleven fixed representative city coordinates cover the ten provinces and Brussels.
These are sampling points, not provincial centroids or PV-capacity weights.
The [location registry](research/weather-source-audit-v1/locations.json) is fixed
before model evaluation. Equal geographic summaries are the first proposed
aggregation; they must not be presented as capacity-weighted national weather.

Nine primary dates span winter, summer, the spring clock change and the autumn
clock change: 23 January, 31 March, 15 June, 27 October and 1 November 2024;
15 January, 30 March, 15 June and 30 September 2025. Three of those dates were
also checked for the native-grid product. No reserved-test solar labels were read.

| Requested model | Sampled dates | Location/date cases | Passed data checks |
|---|---:|---:|---:|
| `dwd_icon_eu` | 9 | 99 | 99 |
| `dwd_icon_eu_native` | 3 | 33 | 0 |
| `gfs_global` | 9 | 99 | 97 |

The native product returned no populated values for the required sampled
historical hours. GFS had finite values throughout, but Liège cloud cover reached
**101%** on both spring-transition samples, causing two range-check failures.
Those values remain unchanged. Neither missing values nor out-of-range clouds
were silently filled, capped or replaced with another model's forecast.

Each request covers two UTC calendar dates, 48 hourly records per location.
Checks cover location mapping, UTC metadata, unique ordered hourly clocks,
required-hour coverage, units, finite values, nonnegative radiation and cloud
cover in [0,100]. Zero radiation is valid. API error responses fail the audit.

## Meaning of the archive and timing assumption

The [Previous Runs documentation](https://open-meteo.com/en/docs/previous-runs-api)
defines these variables by fixed forecast lead relative to valid time. This
product is not an individual, deadline-selected weather run. The response does
not provide historical first-publication timestamps or per-value run identifiers.
`generationtime_ms` measures API processing, not forecast publication time.

For this experiment, predeclare the following deliberately conservative nominal
availability rule:

```text
assumed_available_at = weather_valid_time - 48 hours + 6 hours 10 minutes
```

The six-hour processing allowance is informed by the provider's description of
global model computation delays; the extra ten minutes follows its distributed
server update guidance. It is an **assumption**, not a guaranteed bound on every
historical outage or update. See [Single Runs timing](https://open-meteo.com/en/docs/single-runs-api)
and [model-update metadata](https://open-meteo.com/en/docs/model-updates).

All required sampled weather timestamps satisfy that rule before the preceding
18:00 Brussels solar issue. The smallest assumed margin is **10h 50m** on the
autumn transition day; ordinary days give 11h 50m and spring days 12h 50m.
The audit always reports `historical_publication_times_verified=false`.
We cannot describe this as a proven exact-as-of archive or as the freshest
weather forecast available at 18:00. A 24-hour fixed-lead product would not
satisfy the same deadline for all late targets.

## Hourly weather versus quarter-hour production

The [DWD API definitions](https://open-meteo.com/en/docs/dwd-api) specify trailing
hour means for the selected radiation field. A production interval beginning
at `t` must use the forecast mean covering `[t,t+15min)`, not simply join the
hourly label as if it represented an interval start. Temperature and cloud
resampling may use already-known hourly forecasts, without bridging missing hours.

The audit checks both local midnight boundaries, permitting those later joins:
25 hourly boundaries for a normal day, 24 for the spring day and 26 for the
autumn day. These correspond to 96/92/100 quarter-hour production targets.
It generates no quarter-hour weather values and makes no native-15-minute claim.

## Model transition and stronger-provenance alternatives

The [DWD API](https://open-meteo.com/en/docs/dwd-api) announces a native-grid feed
transition after 30 November 2026, including changes behind the legacy identifiers.
Pinning an identifier alone therefore does not freeze its semantics. Preserve
the historical snapshot, query parameters, returned grids and retrieval times.
Do not blend native responses into missing legacy archive values.

Live forecasts require a separate model/grid compatibility check and capture of
actual retrieval/availability metadata. No live compatibility is certified here.
The [Single Runs API](https://open-meteo.com/en/docs/single-runs-api) is preferable
when genuinely historical runs exist, but its DWD history does not cover this
2024–2025 study. Its early ECMWF HRES archive is described as cycle-49R1
hindcasts; do not relabel it as the operational forecasts seen in 2024.

## Reproduce the audit

From `Backend`, with the existing environment:

```powershell
.\.venv\Scripts\python.exe research/audit_weather_source.py collect --output ../Shared/data/weather/source-audit-v1
.\.venv\Scripts\python.exe research/audit_weather_source.py verify --input ../Shared/data/weather/source-audit-v1
```

Collection requires a new output directory, preserves raw response bytes and
checksums, uses bounded retries and throttles requests. There are 21 small
requests, covering 231 location/date cases. Verification is offline and read-only;
it rejects changed configurations, incomplete request coverage and bad checksums.
Rebuilding the same snapshots produced identical summaries.

Verification: **180 backend tests passed**, including seven weather-audit cases
covering DST boundaries, nominal timing margins, missing-versus-zero values,
out-of-range clouds, units, clocks, location mapping and API errors. All 21 raw
response checksums were verified offline. The existing non-failing Starlette/HTTPX
warning remains. No forecasting model, production value or frontend code changed.

Raw forecast arrays remain in ignored `Shared/data/weather/`. Only the compact
[summary](research/weather-source-audit-v1/summary.json),
[request manifest](research/weather-source-audit-v1/request-manifest.json),
location registry and value-free schema example are committed. Repeating a URL
later is a fresh download, not proof of byte-identical historical service output.

## Next gate

Commit 9 must check the complete required training and validation date ranges,
count weather exclusions, preserve raw provenance and implement the explicitly
versioned temporal/spatial joins. Failed data checks must remain visible. The
production missing-data policy and October–December 2025 evaluation lock remain
unchanged. Improved solar predictions have not yet been established.

Weather data by [Open-Meteo](https://open-meteo.com/), underlying DWD ICON and
NCEP GFS. API data attribution: [CC BY 4.0](https://open-meteo.com/en/licence).
Service usage is governed separately by the [API terms](https://open-meteo.com/en/terms).
No paid service or credentials were used for this pilot.

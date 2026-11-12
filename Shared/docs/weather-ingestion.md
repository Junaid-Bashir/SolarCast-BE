# Weather ingestion and solar alignment — commit 9

The complete archived-weather pipeline now prepares radiation, cloud cover and
temperature features for the Belgian study. This delivery downloads, normalizes
and joins data; it does not fit a model or establish better forecast accuracy.

## Frozen source contract

- Open-Meteo Previous Runs API, explicit **`dwd_icon_eu`** model.
- Three `_previous_day2` variables, nominal 48-hour forecast lead.
- The 11 fixed representative city coordinates from the
  [source-audit registry](research/weather-source-audit-v1/locations.json).
- Local study dates **[2024-01-01, 2025-10-01)**, excluding reserved-test solar targets.
- Nominal weather availability: valid time minus 48 hours plus **6h 10m**.
  This remains an assumption; exact historical publication times are unverified.

There is no best-match selection, native-grid replacement, alternative provider
fallback, actual-weather substitution or automatic repair. The
[source-audit limitations](weather-source-audit.md) remain in force, including
the announced provider grid migration and unverified live-feed compatibility.

## Download and offline normalization

The collector splits UTC requests into at most 28 days and includes the UTC days
containing the two local study boundaries. These padding hours permit valid
hourly joins; only the explicitly requested local solar target range is exported.

Each raw response is immutable within its run and has a checksum, full query,
requested date bounds and retrieval timestamp. The manifest remains incomplete
until all responses have been captured. Transient request failures use bounded
retries; HTTP error bodies are retained when available. An existing run is never
overwritten and partial runs cannot be normalized as complete archives.

Normalization verifies raw checksums, exact request coverage and source settings.
It rejects truncated, duplicated or unordered clocks, wrong units, unexpected
locations and changed grid/elevation metadata between chunks. Canonical JSON
hashing of the registry avoids making its meaning depend on Git line endings.
The captured grid representation was constant across all 23 real-data chunks;
this is not proof that underlying NWP physics or live products are equivalent.

Missing forecast values remain missing. Negative radiation and cloud cover
outside [0,100] are flagged rather than clipped. Temperature must be finite.
Invalid numeric values remain visible in the normalized source columns, with
separate validity flags. A completed download does not imply all values are valid.

## Quarter-hour feature definition

For a solar interval starting at `t` and ending at `t + 15 minutes`:

| Variable | Alignment |
|---|---|
| Radiation, W/m² | Use the trailing-hour forecast mean whose averaging interval covers the solar interval |
| Temperature, °C | Interpolate the two adjacent hourly forecasts at the solar interval's midpoint |
| Cloud cover, % | The same midpoint interpolation, requiring valid endpoints in [0,100] |

For example, 00:00–00:15 uses radiation labelled 01:00, representing 00:00–01:00.
All four quarters share that radiation mean, preserving the hourly radiation
integral. Temperature/cloud interpolation weights are 0.125, 0.375, 0.625 and
0.875. This is explicit hourly-derived resampling, not native 15-minute weather.
Interpolation never crosses a missing or invalid source hour.

For each variable, equal-weight mean/min/max summaries are emitted only when
**all 11 locations** are valid. Otherwise its three summaries stay blank; we do
not average a changing subset of cities. Counts and exclusion reasons accompany
every target row. These are geographical features, not capacity-weighted
estimates of national irradiance.

All joins use unique UTC timestamps. Explicit-offset Brussels labels preserve
the autumn repeated hour and omit the spring nonexistent hour. Each target is
tied to its preceding local 18:00 issue, and the nominal availability rule is
checked against that deadline. Source initialization/publication times are not
inferred from the API's response-processing duration.

## Joining solar references

`weather-features.csv` contains nine `forecast_*` feature columns and provenance/
quality fields, without solar generation. `solar-aligned.csv` additionally contains
`target_generation_mw`, `solar_target_valid` and `joint_target_valid`.

**Target generation and solar-validity flags are reference labels, not model
inputs or issue-time issuance decisions.** Future training must also enforce
label availability at its fitting cutoff and any required production-history
context. A valid weather/solar join alone does not establish a usable history
window or an operational forecast.

Every requested target is retained. Missing solar measurements remain blank;
zero remains zero. No reference target on or after 1 October 2025 is joined.
Weather completeness and reference completeness are reported separately.

## Full-study data findings

The download captured **23 chunks**, containing **168,960 location/hour rows**
across 11 locations. Each variable has 5,412 missing location/hour values, all in
the leading archive gap. The first populated hourly forecast is
**20 January 2024 at 12:00 UTC**. There are no later gaps or out-of-range values
in this captured source. This describes this snapshot, not a service guarantee.

The resulting join retains **61,340 quarter-hour targets**:

| Target range | Targets | Complete weather | Valid solar target | Both valid |
|---|---:|---:|---:|---:|
| Training year 2024 | 35,136 | 33,260 | 34,848 | 32,991 |
| Validation Jan–Sep 2025 | 26,204 | 26,204 | 26,204 | 26,204 |
| Total | 61,340 | 59,464 | 61,052 | 59,195 |

There are 346 complete-weather days in 2024 and all **273 validation days** have
complete weather and solar targets. The 2024 complete-joint count is 78 days,
because the original solar gaps affect many otherwise weather-complete days.

The leading weather gap causes 1,876 incomplete weather-feature targets; these
are explicitly flagged. The original **288 missing solar values** remain unchanged.
A coverage-only check of commit 7's existing one-step training mask finds weather
for **all 7,368 eligible targets**. This is not a new direct-model training mask
or a model-fitting result. The first complete quarter-hour weather features begin
20 January at 12:00 UTC; partial radiation-only features can precede that boundary.

## Reproduce

From `Backend`, using the existing environment and normalized Elia source:

```powershell
.\.venv\Scripts\python.exe -m app.weather download --start 2024-01-01 --end 2025-10-01 --output ../Shared/data/weather/archive-v1
.\.venv\Scripts\python.exe -m app.weather rebuild --input ../Shared/data/weather/archive-v1 --output ../Shared/data/weather/hourly-v1
.\.venv\Scripts\python.exe -m app.weather align --weather ../Shared/data/weather/hourly-v1 --solar ../Shared/data/elia/study-2024-2025/normalized.csv --output ../Shared/data/weather/aligned-v1
```

Every output directory must be new. Rebuild and alignment are offline; a later
download can differ if the provider revises data or migrates its feed. These
commands add no runtime dependencies. The repository's `Shared` registry must
remain available when running from `Backend`.

| Stage | Artifacts |
|---|---|
| Download | `raw/*.json`, `manifest.json` |
| Normalize | `hourly.csv`, `grid.csv`, `summary.json` with artifact hashes and variable-quality counts |
| Align | `weather-features.csv`, `solar-aligned.csv`, `daily-coverage.csv`, `ATTRIBUTION.txt`, `summary.json` |

Full data and exports remain ignored under `Shared/data/`. Only compact
[manifests and QA summaries](research/weather-ingestion-v1/) are committed.
Published manifest/summary bytes are preserved by Git attributes so their
checksum chain survives checkout. Preserve both weather and solar attribution
when sharing a joined export.

## Verification

**193 backend tests passed**, including 13 ingestion/alignment cases covering
offline determinism, checksum rejection, clocks, units, grid changes, missing and
out-of-range values, interval/energy alignment, interpolation without gaps,
complete-location aggregation, both DST days, preserved solar blanks, canonical
registry hashes, HTTP error capture, study bounds and overwrite refusal.
The existing non-failing Starlette/HTTPX warning remains.

Independent full-study checks verified the entire target clock and every joined
solar value, preserved the source hash, and checked the nominal deadline rule.
Twelve raw-response formula checks include the leading gap, its recovery,
spring/fall transitions, summer and the final validation interval. Offline
normalization and alignment CSVs reproduced byte-for-byte from the same snapshots.

```powershell
.\.venv\Scripts\python.exe research/verify_weather_alignment.py ../Shared/data/weather/archive-v1 ../Shared/data/weather/hourly-v1 ../Shared/data/weather/aligned-v1 ../Shared/data/elia/study-2024-2025/normalized.csv
```

No forecasting model or source production value was changed. Better predictions
must be demonstrated in the next weather-aware model comparison, with matched
training examples, deadlines, target sets and the reserved test still locked.

Weather: [Open-Meteo](https://open-meteo.com/), DWD ICON EU, [CC BY 4.0](https://open-meteo.com/en/licence).
Solar references: Elia Transmission Belgium SA,
[ods032](https://opendata.elia.be/explore/dataset/ods032/),
[Elia Open Data licence](https://opendata.elia.be/pages/licence/).
This is an independent SolarCast data preparation experiment.

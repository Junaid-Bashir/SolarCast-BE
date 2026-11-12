# Weather-aware model and matching control (commit 10)

The weather-aware model reduced validation RMSE by **12.46% against its matched
no-weather control** and **14.04% against the 48-hour seasonal baseline**. These
are historical validation results, not a live operational accuracy claim.

| Forecast | RMSE (MW, lower is better) | MAE (MW) |
| --- | ---: | ---: |
| Repeat production from 48 hours earlier | 1,019.70 | 497.07 |
| Direct XGBoost correction, no weather | 1,001.31 | 500.45 |
| Direct XGBoost correction, with weather | **876.53** | **446.94** |

All three are scored on the same **26,204 quarter-hours across 273 complete
delivery days, January–September 2025**. Flat persistence is also recorded in
the machine-readable report. October–December 2025 remains unscored and the CLI
rejects requests to evaluate it.

## Experiment declared before examining results

Both models directly predict a correction to the known 48-hour seasonal
production profile. The training label is actual production minus that profile;
the final forecast is `max(0, seasonal production + predicted correction)`.
Each delivery quarter is predicted independently, without recursive feedback or
bridge predictions. The earlier recursive models are a different experiment;
their comparison with this model does not isolate the effect of weather.

The pair shares **7,274 eligible 2024 examples**, identical residual labels,
15 common features, and identical XGBoost settings: 120 trees, depth 5, learning
rate 0.05, squared-error objective, histogram trees, random seed 42. No validation
tuning, early stopping, or refitting was performed. The weather model adds only
nine features: mean/minimum/maximum radiation, cloud cover and temperature over
the same 11 equally weighted locations. See the [ingestion report](weather-ingestion.md).

The shared predictors contain 97 observed quarter-hours ending at the issue-time
observation origin, UTC target calendar features, lead time, and the 48-hour
seasonal value. The rolling day statistics use the latest 96 observations; the
97th supplies the 24-hour lag. Neither model reads delivery-period production.
Future references are used only after predictions have been generated.

Forecasts are issued at 18:00 Brussels time the previous day. A solar interval
is assumed available 15 minutes after it finishes, so the normal observation
origin is 17:30 local. Labels must also have become available by the frozen fit
cutoff, 31 December 2024 at 18:00 Brussels time. The first training delivery is
24 January 2024; the final included label starts at 16:30 UTC on 31 December.

Missing solar/weather values are never filled. Both models use the same
intersection of eligible rows, including the weather availability requirement
for the control. Missing history removes a training window; a missing seasonal
source, weather value, or label removes that training target. Issuance requires
complete inputs for the entire delivery day. Scoring requires complete delivery
references, on the same days for all four forecasts. Spring DST has 92 quarters;
autumn DST has 100, with the latter covered by synthetic calendar tests only.

## Limits on interpretation

- Daily missing solar observations sharply restrict training, particularly in
  spring/summer. The 7,274 examples are mostly winter/autumn; this is not a
  representative full-year training sample.
- Open-Meteo Previous Runs `_previous_day2` is a nominal 48-hour product, not an
  exact archived run selected by the historical issue timestamp. Availability
  is conservatively assumed as valid time minus 48 hours plus 6 hours 10 minutes;
  historical publication times remain unverified. This is not proof of an
  operationally reproducible as-of forecast.
- Fits use the first validation issue time as the label cutoff. Initial fitting
  computation time is not subtracted from that deadline.
- Locations are equally weighted, not weighted by installed solar capacity.
- The models are research artifacts, not yet connected to the live application.
  Live weather compatibility remains unverified.

## Reproduction and checks

From `Backend`, using the project Python environment and the ignored local source
artifacts produced in commit 9:

```powershell
python -m app.weather_pair --solar ../solarcast-platform/data/elia/study-2024-2025/normalized.csv --weather ../Shared/data/weather/aligned-v1-final --output ../Shared/data/weather/pair-validation-v1 --solar-delay-minutes 15
python -m research.verify_weather_pair --run ../Shared/data/weather/pair-validation-v1 --solar ../solarcast-platform/data/elia/study-2024-2025/normalized.csv --weather ../Shared/data/weather/aligned-v1-final
python -m pytest -q
```

Use a new output directory for another run; existing results are not overwritten.
The solar CSV is the unchanged Elia study input documented in earlier commits;
its legacy local path is not the repository layout. Raw weather, source solar,
native model weights and prediction CSVs stay outside Git under ignored paths.
Compact [run metadata](research/weather-pair-v1/summary.json) and
[verification results](research/weather-pair-v1/verification.json) are published.
The verifier checks checksums, identical scoring keys, source labels, independent
MAE/RMSE calculations, fitting cutoff, persisted model reproduction on four days,
and invariance when all production after the observation origin is removed.

Next: commit 11 will turn these saved predictions into clearly labelled
prediction-versus-actual charts and a concise results post, with these limitations.

Weather: Open-Meteo.com / DWD ICON EU, CC BY 4.0. Solar: Elia Transmission Belgium
SA, [ods032](https://opendata.elia.be/explore/dataset/ods032/), CC BY 4.0.

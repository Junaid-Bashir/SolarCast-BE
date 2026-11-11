# LinkedIn draft — first baseline results

SolarCast now has its first reproducible day-ahead forecasting baseline results.

I compared two simple methods for Belgian national solar production:

- Flat persistence: hold the latest available production value.
- A seasonal baseline: use the production observed 48 hours before each target.

Across January–September 2025, both were evaluated on the same 273 complete days
and 26,204 quarter-hour observations.

RMSE was 2,166 MW for flat persistence and 1,020 MW for the seasonal baseline —
a 52.9% reduction relative to that simple reference.

The engineering work behind those numbers matters: an 18:00 Brussels deadline,
an explicit 15-minute delay after interval completion, daylight-saving changes,
source-availability checks and no filling of missing production values.

This is a baseline milestone. It does not establish an improvement over Elia's
forecast, and no AI model was used in this comparison. The October–December 2025
test period remains unscored while I develop the models.

My next step is to test machine learning models against this benchmark.

I would welcome feedback from people working in renewable forecasting:
which baseline or evaluation check would you add next?

[Code and methodology](https://github.com/Junaid-Bashir/SolarCast-BE/blob/main/Shared/docs/baseline-validation.md)

Source: Elia Transmission Belgium SA / Elia Open Data, CC BY 4.0.
Independent project.

#EnergyForecasting #SolarEnergy #OpenSource

---

Attach `Shared/images/baseline-validation-v1.png`. The chart shows a preselected
illustrative week; its metric bars cover the full validation period. Publish
after the commit is pushed and the repository reproduction links are accessible.
This is a draft only; no post has been published.

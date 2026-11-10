# Platform design decisions

- Application code lives in the root `Backend` and `Frontend` folders; `Shared` contains sample data, methodology and README images. The separate research project is not modified or imported implicitly.
- The HTTP boundary, typed schemas, and dataset services are deliberately small modules rather than empty layered directories.
- Next.js performs same-origin API proxying; FastAPI is independently runnable and documents its contracts at `/docs`.
- The store is a replaceable ephemeral repository. It has a count limit, logical expiry, and a lock around mutations; use a single Uvicorn worker. Multiple workers would have independent stores.
- Upload inspection preserves strings and checks CSV shape before creating a dataframe. Mapping is required before time-series interpretation.
- Validation separates parse errors, data health warnings, and informational checks. It never transforms stored data. Invalid reports can still be inspected to explain what needs fixing.
- Browser presentation uses source units and preserves naive timestamp labels. Explicit UTC offsets are normalized only in analysis views. The sample's original offset is visible in its source preview.
- Charts are bounded, historical only, and do not contain fabricated model metrics. A contiguous prefix retains source ordering and null values.
- Phase 2 adds `forecast_schemas.py` for typed contracts, `forecasting.py` for causal features/evaluation, and `jobs.py` for bounded background execution. Existing dataset APIs and source-preservation behavior remain intact.
- A single worker runs baselines before fixed-hyperparameter ML models. All models share an explicit origin/target purge and the same evaluated timestamps; `backtesting.md` defines the contract.
- Browser polling exposes job stages, per-model failures, measured holdout metrics, and historical predictions. CSV streams full prediction vectors; chart responses are bounded.
- Store mutations/job snapshots are locked. Cancellation is cooperative, and dataset deletion cascades to jobs. No model files are persisted or untrusted serialized models accepted.
- Phase 3 adds `future.py` and future request/result schemas. It refits the selected algorithm for one-step recursion on all observed history, keeping the frozen holdout outputs unchanged. Both paths share causal feature construction.
- Both job kinds share the same queue/limits; removing a source comparison also removes dependent future jobs. Forecast responses/CSV have explicit future kinds and no invented actual/error/accuracy fields.
- Future charts use a marked cutoff and cyan dashed lines, with origin, generation time, refit/recursion assumptions, and relative-to-dataset date disclosure. See `future-forecasting.md`.

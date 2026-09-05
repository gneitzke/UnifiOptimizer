# Historical-data accuracy fixes

## B7 — historical end time used as the retention clock

- Changed: `netadmin/server/routers/metrics.py:146-150`.
- Root cause: the endpoint passed the requested historical `end_ts` as
  `read_window(..., now=...)`, so retention was evaluated in the past and chose
  raw data that the real retention pass had already deleted.
- Fix: retain `end_ts` solely as the query boundary and pass the current clock
  for retention selection. No controller interaction was added or changed.
- Failing-then-passing regression:
  `test_window_uses_current_clock_for_historical_retention` in
  `tests/netadmin/server/routers/test_metrics.py:59`. After raw history is
  pruned at a clock 40 days later, its key assertions are `tier == "hourly"`
  and non-empty buckets. Against the historical implementation it returned
  `tier == "raw"`.

## B8 — unaligned retention seams double-counted full rollup buckets

- Changed: `netadmin/store/repository.py:124-132`, `857-864`, and `1274-1284`.
- Root cause: tier reads selected rollups by bucket start while raw/hourly
  retention cutoffs could sit inside those buckets. The overlapping fine-tier
  rows were then appended to an aggregate that already contained them.
- Fix: assign the entire seam bucket to the coarser tier by ceiling the
  raw/hourly boundary to an hour and the hourly/daily boundary to a UTC day.
  `prune` applies the identical aligned cutoffs, so retained rows and read
  ownership remain consistent.
- Failing-then-passing regressions:
  - `test_read_window_assigns_hour_boundary_to_hourly_without_overlap_or_gap`
    at `tests/netadmin/store/test_repository_findings.py:217` asserts exactly
    `[(3600, 30.0), (7210, 20.0)]`; historical behavior included the duplicate
    raw `(3640, 20.0)`.
  - `test_read_window_assigns_day_boundary_to_daily_without_overlap_or_gap`
    at `tests/netadmin/store/test_repository_findings.py:241` asserts exactly
    `[(DAY_SECONDS, 30.0), (2 * DAY_SECONDS, 40.0)]`; historical behavior also
    included the overlapping hourly `(93600, 20.0)`.

## Compatibility and verification

- No `repository.py` method signature changed; callers require no adaptation.
- Passed: `PYTHONPATH="$PWD" python3 -m pytest tests/netadmin/store
  tests/netadmin/server/routers -q -p no:cacheprovider` — 267 passed.
- The requested `test_retention.py` command found no tests (the file is absent).
- No commit was created.

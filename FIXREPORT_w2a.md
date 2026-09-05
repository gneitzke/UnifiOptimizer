# FIXREPORT — worktree w2a

Root-cause fixes for B4 (HIGH) and Q2 (MEDIUM). GET-only contract untouched
(no controller calls added); mock-only tests. Full suite: **639 passed**.

Run:
```
PYTHONPATH="$PWD" python3 -m pytest tests/netadmin/detect tests/netadmin/issues tests/netadmin/store -q -p no:cacheprovider
```

---

## B4 (HIGH) — losing the event feed could resolve real event-based issues

### Root cause
`FlakyClientDetector` (`client.flaky`) builds its verdict **entirely from
disconnect events**, but gated only on `ctx.coverage(window_s, "fast_sta")` —
which measures **client-poll** availability, not **event-stream** availability.
With healthy polling but a broken WebSocket / `stat/event` feed, old disconnects
age out of the window, the detector sees `[]` and returns a clean `[]`, and the
detect engine (`_collect_clears`) then clears the open issue — a real client
disconnect resolved on missing data. Fix verification inherits the same exposure:
the false clear drives the K clear-streak in `IssueEngine._process_clear`, and if
a fix is armed the issue resolves-and-VERIFIES on missing event data.

### Event-coverage API used
The C3/C4 ingest coverage ledger (`ingest_coverage` table). Event-source
observation is recorded there by the event catch-up as completed
`kind='event_history', scope='site', status='complete'` intervals
(`netadmin/ingest/events.py` → `repo.record_ingest_coverage(...)`, read back via
`latest_ingest_coverage_end`). A broken feed stops advancing these intervals, so
the honest gap signal is "how much of the detector window falls inside completed
event-history coverage" — never inferred from the presence/absence of event rows
(that conflation is the bug).

### Changes
- **`netadmin/store/repository.py`** — added read-only additive method
  `observed_event_coverage(start_ts, end_ts, *, kind="event_history",
  scope="site")` (marked `# B4:`), inserted before `read_events`
  (~line 1085). Returns the fraction of `[start_ts, end_ts)` covered by merged
  `status='complete'` intervals for that kind/scope. Touches only the C3/C4
  ledger table; no shared method or migration changed.
- **`netadmin/detect/context.py`** — added `# B4:` helper `event_coverage(
  window_seconds)` (before the Tunables section) delegating to
  `repo.observed_event_coverage(now-window, now)`.
- **`netadmin/detect/detectors/client.py`** — `FlakyClientDetector.evaluate`
  (~line 158): after the existing `fast_sta` gate, added
  `if ctx.event_coverage(window_s) < COVERAGE_MIN: return UNKNOWN`. Freezes the
  verdict during event-feed gaps so clearing and fix verification are frozen, not
  cleared. Engine/issue wiring already treats whole-detector `UNKNOWN` as
  "advance nothing", so no change was needed in `detect/engine.py` or
  `issues/engine.py` for B4 — the freeze propagates through the existing UNKNOWN
  path. **Client-poll-based detectors unchanged**: `DhcpClientDetector`
  (poll-only) and `KnownPathologyDetector` (mixed; its poll-based roam arm must
  keep firing during an event gap) were deliberately not gated.

### Tests (`tests/netadmin/detect/detectors/test_client.py`) — fail→pass
- **(a)** `test_flaky_unknown_when_event_feed_gap_despite_healthy_poll` — healthy
  `fast_sta`, no event coverage recorded. Key assert:
  `FlakyClientDetector().evaluate(_ctx(repo)) is UNKNOWN`. Was `[]` (a false
  clear) before the fix.
- **(b)** `test_flaky_clears_when_event_feed_healthy_and_events_gone` — healthy
  `fast_sta` **and** seeded event coverage, disconnects genuinely gone. Key
  assert: `... == []` (a legitimate clear). Stay-green control proving the gate
  does not over-freeze real resolutions.
- **(c)** `test_event_feed_gap_does_not_verify_a_flaky_fix` — full engine + issue
  lifecycle. Issue fires ACTIVE, `apply_fix` arms the 48 h window; then an
  event-feed gap (healthy poll, aged-out events, client re-seen so not
  "departed"). Key asserts after two clean-looking passes: issue still open and
  `state == ACTIVE` (frozen, not resolved), and `fix_state != VERIFIED` (fix not
  credited on missing data). Was resolved-and-VERIFIED before the fix.

Three existing `client.flaky` firing tests were updated to seed
`seed_event_coverage(...)` (they now model a healthy event feed, which is what a
firing/clearing case requires). New test helper `seed_event_coverage` added to
`tests/netadmin/detect/support.py`.

---

## Q2 (MEDIUM) — a pass with a crashed detector was recorded as ok

### Root cause
`DetectorEngine._record_pass` (`netadmin/detect/engine.py`): when
`error is None and failed:` it set the explanatory error string
(`"N detector(s) failed"`) but still recorded `ok=ok` (True) to `poll_runs`, so
health counted a pass containing an isolated detector crash as a success
(reproduced: `{"ok":1,"error":"1 detector(s) failed"}`).

### Change
- **`netadmin/detect/engine.py`** `_record_pass` (~line 492): in that branch,
  also set `ok = False` (marked `# Q2:`). Only the **recorded** flag is
  corrected; per-detector isolation is preserved (other detectors still run) and
  `PassResult.ok` stays True to say the pass itself survived — matching the
  existing `test_broken_detector_is_isolated` contract.

### Tests (`tests/netadmin/detect/test_engine.py`) — fail→pass
- `test_failed_detector_marks_the_recorded_pass_not_ok` (new). Key asserts:
  `result.ok is True` (isolation preserved) **and** `row["ok"] == 0` with
  `"1 detector(s) failed" in row["error"]` (recorded flag corrected).
- `test_firewall_failure_annotates_the_poll_run` (existing) — its stale
  `assert row["ok"] == 1` was corrected to `assert row["ok"] == 0`; this
  assertion encoded the old buggy behavior and fails without the fix.

---

## Files touched
- `netadmin/detect/detectors/client.py` (B4 gate)
- `netadmin/detect/engine.py` (Q2 recorded-ok)
- `netadmin/store/repository.py` (`# B4:` additive read-only `observed_event_coverage`)
- `netadmin/detect/context.py` (`# B4:` additive `event_coverage` helper)
- `tests/netadmin/detect/detectors/test_client.py`, `tests/netadmin/detect/test_engine.py`,
  `tests/netadmin/detect/support.py` (tests + helper)

`netadmin/issues/engine.py` was analyzed but needed no change: the B4 freeze is
enforced upstream via the detector's `UNKNOWN`, which the existing engine wiring
already routes as "advance nothing", protecting `_process_clear` /
`_resolve` fix verification. Not committed.

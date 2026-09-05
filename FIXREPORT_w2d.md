# Fix Report — w2d

## B5 — historical SLE attachment attribution

- Changed `netadmin/sle/minutes.py:240-245`, `314-394`, and `803-868`.
  Historical coverage samples now resolve their AP at the sample timestamp.
  Capacity evaluation is divided at attachment changes and selects/evaluates a
  radio only within the interval when the client was attached to that radio's AP.
  Bucket-level roaming/connect attribution uses the AP that owned the largest
  recorded portion of that historical bucket. No historical sample falls back to
  the client's current `entities.parent_id`.
- State-history helper used: `Repository.list_state_changes`, read once for the
  opening `ap_mac` value before the bucket and once for changes within the bucket;
  the added `SleMinutesJob._client_attachment_intervals` builds half-open
  intervals, and `Repository.find_entity` resolves each recorded AP native ID.
- Failing-then-passing regression:
  `tests/netadmin/sle/test_minutes.py:189::test_historical_roam_attributes_each_bucket_to_its_ap_and_radio`.
  Before the fix, the early coverage assertion expected AP1 (`{1}`) but got the
  current AP2 (`{2}`). After the fix, early/late coverage attributes to AP1/AP2,
  and capacity independently proves the historical radio selection: the early
  bucket is `non_wifi_util` on radio1 while the later bucket is `ok` on radio2.

## R5 — shared authentication failure cooldown

- Changed `netadmin/ingest/unifi/auth.py:49-112`, `224-228`, `271-275`,
  `367-370`, and `396-400`. Added typed `UnifiAuthCooldownError`, parsing for
  both forms of `Retry-After`, explicit 429 handling for API-key verification,
  cookie login, and the login-free UniFi OS probe, plus prevention of cookie
  fallback after an API-key rate limit.
- Changed `netadmin/ingest/unifi/client.py:128-268`. Authentication failures now
  establish one monotonic shared cooldown while holding the existing auth lock.
  Queued and subsequent callers check that deadline and raise the typed cooldown
  error without opening another request. Explicit server `Retry-After` wins;
  other authentication refusals use a 30-second quiet period. Successful auth
  clears the failure state. The same handling covers initial auth, re-login, and
  the isolated WebSocket cookie strategy.
- Failing-then-passing regression:
  `tests/netadmin/unifi/test_client.py:64::test_concurrent_auth_429_shares_retry_after_cooldown`.
  Before the fix, four concurrent callers caused four login POSTs (`4 != 1`).
  After the fix, all callers receive `UnifiAuthCooldownError` with a positive
  cooldown no greater than the supplied 60 seconds, the login route is called
  exactly once, and another caller within the cooldown leaves that count at one.
- Controller contract: no read POST was added. The only POST exercised here is
  the pre-existing password login handshake; API-key verification and controller
  detection remain GETs.

## Verification

- Pre-fix focused run: both new regression tests failed with the signatures
  recorded above.
- Post-fix requested suite:
  `PYTHONPATH="$PWD" python3 -m pytest tests/netadmin/sle tests/netadmin/unifi tests/netadmin/ingest -q -p no:cacheprovider`
  — **354 passed**.
- `python3 -m flake8` on all changed Python files — passed.
- `git diff --check` — passed.

# FIXREPORT — B6: four wired FEEDS rules were unreachable

## Root cause
`StoreCorrelationRepository.topology()` built a parent/child-only
`TopologyIndex`, never passing the `uplinks` argument. `TopologyIndex._feeds`
was therefore always empty in production, so `feeds`/`is_fed_by` always returned
`False` and the four `FEEDS` rules (`wired.port_flapping`, `wired.bad_cable`,
`wired.stp_loop`, `wired.broadcast_storm`) could never group a downstream
symptom under its wired feeder — a whole rule class that never fired. The deeper
cause: the ingest layer parsed the GET `stat/device` `uplink` block but never
persisted the feeder identity, so no feeder edge could be reconstructed.

## GET-inventory field the feeder edges are derived from
`Device.uplink.uplink_mac` (the upstream switch MAC) and
`Device.uplink.uplink_remote_port` (the remote switch port index) — both already
present in the polled `stat/device` payload (`netadmin/ingest/unifi/models.py`
`Uplink`). No new controller call; GET-only contract preserved. The feeder port
entity's native_id is `f"{uplink_mac}:{uplink_remote_port}"`, matching the port
native_id `map_device` already mints.

Physical FEEDING is kept strictly distinct from CONTAINMENT: the switch/port
*feeds* the AP but is not its `parent_id` (the AP stays site-level). Containment
still flows through `entities.parent_id` only.

## Changes (file:line)

1. `netadmin/ingest/mapping.py` (~line 297, `map_device`) — **ingest mapping, additive**
   Persist the feeder identity into device meta:
   `dev_meta["uplink_mac"]` and `dev_meta["uplink_remote_port"]` when the
   `uplink` block reports them. Additive, derived only from data already in the
   polled payload. (Flagged: this is the one change slightly outside the core
   correlate set.)

2. `netadmin/store/repository.py` (new method `feeder_edges()`, inserted after
   `entity_topology`) — reconstructs physical feeder edges
   `(feeder_entity_id, fed_entity_id)` from the `uplink_mac`/`uplink_remote_port`
   in each device's meta. Emits up to two edges into each uplinked device: from
   the upstream **switch** (roots `wired.broadcast_storm`, which is switch-scoped)
   and from the uplink **port** (roots the port-scoped `wired.port_flapping` /
   `wired.bad_cable` / `wired.stp_loop`). Feeders not yet present as entities are
   skipped. Pure SQL over the existing `entities` table.

3. `netadmin/correlate/store_repository.py` (`topology()`, line ~85) — loads
   `feeder_edges()` and passes them as `uplinks=` to `TopologyIndex`. Docstring
   updated (feeds is no longer "dormant in production").

4. `netadmin/correlate/topology.py` — module docstring updated to state the
   edges are now rebuilt by `Repository.feeder_edges` and remain distinct from
   containment.

## No migration
No migration was needed (0011 NOT added). The feeder identity rides in the
existing `entities.meta` JSON column, and edges are derived at topology-build
time. This is the true root-cause fix and is strictly cleaner than a dedicated
edge table: it avoids collector edge-population timing bugs (the feeder switch
may be polled after the device that uplinks to it — at topology-build time every
entity already exists, so resolution is total). Persistence requirement is met:
the uplink identity is persisted at ingest; edges are a pure projection of it.

## Tests — failing before, passing after

New file `tests/netadmin/correlate/test_store_feeder_topology.py`
(drives the real `Repository` -> `StoreCorrelationRepository`):
- `test_feeder_edges_reconstructed_from_uplink_meta` — asserts both
  `(switch_id, ap_id)` and `(port_id, ap_id)` in `repo.feeder_edges()`, and that
  nothing feeds the switch/port themselves.
- `test_store_topology_resolves_feeds_but_not_containment` — **failed before**:
  `topo.feeds(port_id, ap_id)` and `topo.feeds(switch_id, ap_id)` are `True`,
  while `is_ancestor(switch_id, ap_id)` and `is_ancestor(port_id, ap_id)` stay
  `False` (containment distinct) and `is_ancestor(switch_id, port_id)` stays
  `True`.
- `test_feeds_dormant_without_uplink_meta` — guards the old behavior: no uplink
  meta -> `feeder_edges() == []` and `feeds(...) is False`.
- `test_port_flap_rule_correlates_downstream_ap_symptom` — **failed before**
  (`assert {10} == {10, 11}`): a `wired.port_flapping` on the uplink port becomes
  the incident root and the AP's `net.coverage_hole` is grouped under it; the
  recorded member `rule` starts `port_flapping->` and ends `:feeds`.

New tests in `tests/netadmin/ingest/test_mapping.py`:
- `test_device_meta_carries_uplink_feeder_identity` — **failed before**
  (`KeyError: 'uplink_mac'`): meta carries `uplink_mac` + `uplink_remote_port`.
- `test_device_meta_omits_uplink_identity_when_absent` — no uplink block -> keys
  absent; a wireless uplink with no remote port records `uplink_mac` but omits
  `uplink_remote_port` (no phantom port edge).

Red run (fix reverted): 4 failed, 43 passed — the two topology/rule tests and
the two mapping tests.

## Verification
- Target suites: `tests/netadmin/correlate tests/netadmin/store
  tests/netadmin/ingest` -> **317 passed**.
- Full `tests/netadmin` -> **2093 passed, 1 skipped** (no regressions).

Not committed; changes left in the working tree.

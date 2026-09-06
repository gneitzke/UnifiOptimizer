import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { SeverityPill, SeverityGlyph } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { Skeleton } from '../../components/ui/Skeleton';
import { EntityLink } from '../shared/EntityLink';
import { formatDuration } from '../shared/format';
import {
  getIncident,
  entityLabel,
  type IncidentSummary,
  type IncidentMember,
} from '../shared/api';

/**
 * One incident, rendered as a row: root-cause line + entity/duration, with a
 * "+N related" expander that lazily loads and reveals the symptoms. A standalone
 * issue (incident-of-one) links straight to the issue and shows its state pill.
 * Used by the dashboard's "Needs attention" card, which requests the engine's
 * uniform projection (`include_singletons=true`) so every open issue shows up
 * here one way or another. The genuine-incident case in the Issues list (Gitea
 * #21) is a different, denser presentation (`IssueRowsList`'s `GroupRow`) built
 * from the issue list's own data rather than this `IncidentSummary` shape.
 */
export function IncidentRow({
  incident,
  now,
}: {
  incident: IncidentSummary;
  now: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [symptoms, setSymptoms] = useState<IncidentMember[] | null>(null);
  // C5 (round 16): the detail fetch is cached per-row, but the parent polls
  // every 30s and preserves this row's component identity by incident id — so
  // a poll that changes current membership left the cached `symptoms` stale
  // (still carrying a now-cleared member as current:true) while the "+N
  // related" count above read the fresh `incident.symptom_count`. Tracking
  // the CURRENT count the cache was fetched at, and refetching whenever it
  // drifts from the live prop, keeps the cache honest without refetching on
  // every poll when membership hasn't actually changed.
  //
  // C5 (round 18): count alone misses a membership REPLACEMENT at a constant
  // count (symptom A clears, symptom B joins in the same reconcile pass —
  // `member_count`/`symptom_count` doesn't move). `IncidentSummary` carries no
  // membership identity/version, so the best available signal is
  // `last_seen_ts`: the correlation engine sets it from
  // `max(member.issue.last_seen_ts)` on every reconcile (`engine.py`'s
  // `_reconcile` — see `evidence_ts` / `prev.last_seen_ts = max(...)`), so a
  // membership change from fresh evidence (the realistic case a replacement
  // is triggered by) advances it even when the count doesn't. The cache key
  // is therefore the PAIR (count, last_seen_ts), not count alone.
  const [symptomsFetchedAt, setSymptomsFetchedAt] = useState<{
    count: number;
    seen: number;
  } | null>(null);
  // C5 (round 17): tracks the signature a fetch most recently FAILED at,
  // distinct from `symptomsFetchedAt` (which now only ever records a SUCCESS
  // — see `fetchSymptoms` below). This exists purely to stop the passive
  // in-place effect from spinning: without it, a persistently-failing fetch
  // would leave `stale` true forever, and the effect would refire the moment
  // `loadingSymptoms` flips back to false, forever. A manual expand
  // (`toggle`) ignores this and always retries, matching "retry on next user
  // expand"; it's only the automatic effect that backs off once a given
  // signature has already failed.
  const [symptomsFailedAt, setSymptomsFailedAt] = useState<{
    count: number;
    seen: number;
  } | null>(null);
  const [loadingSymptoms, setLoadingSymptoms] = useState(false);

  const isGroup = incident.symptom_count > 0;
  const root = incident.root;
  const headTitle = root?.title ?? incident.title;
  const href = isGroup ? `/incidents/${incident.id}` : `/issues/${incident.root_issue_id}`;
  const ongoing = `ongoing ${formatDuration(now - incident.first_seen_ts)}`;
  const currentSignature = { count: incident.symptom_count, seen: incident.last_seen_ts };
  const stale =
    symptoms !== null &&
    (symptomsFetchedAt === null ||
      symptomsFetchedAt.count !== currentSignature.count ||
      symptomsFetchedAt.seen !== currentSignature.seen);
  const failedAtCurrent =
    symptomsFailedAt !== null &&
    symptomsFailedAt.count === currentSignature.count &&
    symptomsFailedAt.seen === currentSignature.seen;

  async function fetchSymptoms() {
    setLoadingSymptoms(true);
    const atSignature = currentSignature;
    try {
      const detail = await getIncident(incident.id);
      setSymptoms(detail.symptoms);
      // Only a SUCCESSFUL fetch validates the cache for this signature. A
      // failed fetch must NOT record `symptomsFetchedAt` — doing so would
      // mark an empty/stale `symptoms` as satisfied for the current
      // signature, so a transient failure permanently hid real symptoms even
      // after the server recovered (no retry on collapse/re-expand or the
      // in-place staleness effect below, since `stale` would already read
      // false).
      setSymptomsFetchedAt(atSignature);
      setSymptomsFailedAt(null);
    } catch {
      // Leave `symptomsFetchedAt` untouched so `stale` stays true (or
      // `symptoms` stays null on a first-ever attempt) — the row keeps
      // retrying on the next expand or the next time the signature changes
      // again. Record the failed signature separately so the passive effect
      // below (not the user-driven `toggle`) can stop retrying a signature
      // that's already known to fail, avoiding a busy loop.
      setSymptoms([]);
      setSymptomsFailedAt(atSignature);
    } finally {
      setLoadingSymptoms(false);
    }
  }

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && !loadingSymptoms && (symptoms === null || stale)) {
      await fetchSymptoms();
    }
  }

  // Membership can change out from under an already-expanded row (the
  // dashboard polls every 30s and keeps this component mounted across
  // refreshes) — including a REPLACEMENT that leaves `symptom_count`
  // unchanged (C5 round 18), which `stale` now catches via `last_seen_ts`.
  // Refetch in place so the open list stays in agreement with the fresh
  // signature instead of waiting for a collapse/re-expand that may never
  // come. Skips a signature that has already failed (`failedAtCurrent`) so a
  // persistently-failing fetch retries only on the next user expand or the
  // next signature change, rather than spinning every render.
  useEffect(() => {
    if (expanded && stale && !loadingSymptoms && !failedAtCurrent) {
      void fetchSymptoms();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, stale, loadingSymptoms, failedAtCurrent, incident.symptom_count, incident.last_seen_ts]);

  return (
    <li style={{ borderTop: '1px solid var(--hairline)' }}>
      <div className="flex items-center gap-2 py-2">
        <SeverityPill severity={incident.severity} glyphOnly />
        <Link
          to={href}
          className="flex flex-col min-w-0 flex-1 -my-1 py-1 rounded transition-colors hover:bg-canvas"
        >
          <span className="t-body truncate" style={{ color: 'var(--fg)' }}>
            {headTitle}
          </span>
          <span className="t-caption truncate" style={{ color: 'var(--fg-muted)' }}>
            {isGroup
              ? incident.summary || `${incident.symptom_count} related symptom(s)`
              : `${root?.entity ? entityLabel(root.entity) : 'network-wide'} · ${ongoing}`}
          </span>
        </Link>
        {isGroup ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1 h-[24px] px-2 rounded-control t-caption cursor-pointer transition-colors hover:bg-canvas whitespace-nowrap"
            style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}
          >
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}+
            {incident.symptom_count} related
          </button>
        ) : (
          root && <StatePill state={root.state} severity={incident.severity} />
        )}
      </div>

      {isGroup && expanded && (
        <ul className="flex flex-col pb-2 pl-6" style={{ gap: 2 }}>
          {symptoms === null || (loadingSymptoms && stale) ? (
            <Skeleton className="h-5 w-2/3" />
          ) : (
            // C5: `getIncident` returns the incident's full historical symptom
            // list, but the "+N related" count above is `incident.symptom_count`
            // — the backend's CURRENT-membership count (cleared_ts IS NULL),
            // same source IncidentDetailPage's header uses. Filtering this list
            // to `current !== false` (mirroring IncidentDetailPage's
            // `currentMembers`) keeps the expanded list in agreement with that
            // count instead of listing cleared/former symptoms as if they were
            // still part of the incident. `current !== false` treats an older
            // payload without the flag as current, matching api.ts's
            // optional-field contract.
            (symptoms ?? [])
              .filter((m) => m.current !== false)
              .map((m) => (
                <li key={m.issue.id} className="flex items-center gap-2 py-1">
                  <SeverityGlyph severity={m.issue.severity} size={10} />
                  <Link
                    to={`/issues/${m.issue.id}`}
                    className="t-caption truncate hover:underline"
                    style={{ color: 'var(--fg-muted)' }}
                  >
                    {m.issue.title}
                  </Link>
                  {m.entity && (
                    <span className="t-micro truncate" style={{ color: 'var(--fg-subtle)' }}>
                      · <EntityLink entity={m.entity} muted />
                    </span>
                  )}
                </li>
              ))
          )}
        </ul>
      )}
    </li>
  );
}

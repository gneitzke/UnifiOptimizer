/**
 * Shared event/state vocabulary — the ONE place the Timeline, the dashboard's
 * Recent activity ticker, the client journey and the device state-history rail
 * turn raw controller codes into plain language (Gitea #52/#53). Keeping it in
 * one module is the whole point: four surfaces used to each humanise keys their
 * own partial way, so the same event read as a different sentence (or a raw code)
 * depending on where you looked.
 *
 * UniFi event keys are `EVT_<FAMILY>_<Name>` (e.g. `EVT_WU_Roam`,
 * `EVT_SW_PoeOverload`) and per-client anomalies arrive as `ANOMALY_<CODE>`
 * (e.g. `ANOMALY_USER_HIGH_TCP_LATENCY`). The FAMILY prefix is the filter axis;
 * it does NOT encode severity. Color, per DESIGN_FOUNDATION chart rules, appears
 * "only where the event IS a severity" — the density is drawn in the single
 * accent and a separate *fault* band gets the one severity tint. Families never
 * get a rainbow.
 *
 * The expert is never cut off from the raw code: every surface keeps the literal
 * key visible (in mono) or on hover beside the plain sentence.
 */

export type FamilyId = 'wifi' | 'wired' | 'ap' | 'switch' | 'gateway' | 'wan' | 'other';

export interface Family {
  id: FamilyId;
  /** UniFi key prefix(es) that map here, matched after the leading `EVT_`/`ANOMALY_`. */
  prefixes: string[];
  label: string;
  /** One-line description for the filter chip's title attribute. */
  hint: string;
}

/**
 * Ordered for the filter row. `other` is the catch-all for keys that do not
 * match a known prefix (kept honest rather than silently dropped).
 *
 * `USER` sits in the Wi-Fi-clients family: per-client anomalies arrive as
 * `ANOMALY_USER_*` (latency, DNS), which are about a wireless client's
 * experience, so they belong beside its connect/roam events (Gitea #53).
 */
export const FAMILIES: Family[] = [
  {
    id: 'wifi',
    prefixes: ['WU', 'USER'],
    label: 'Wi-Fi clients',
    hint: 'Wireless client events and anomalies: connect, roam, disconnect, latency',
  },
  { id: 'wired', prefixes: ['LU'], label: 'Wired clients', hint: 'LAN client events: connect, disconnect' },
  { id: 'ap', prefixes: ['AP'], label: 'Access points', hint: 'Access-point events: adopt, restart, lost contact, DFS' },
  { id: 'switch', prefixes: ['SW'], label: 'Switches', hint: 'Switch events: PoE, STP, port up/down' },
  { id: 'gateway', prefixes: ['GW'], label: 'Gateways', hint: 'Gateway events: restart, lost contact' },
  { id: 'wan', prefixes: ['WAN'], label: 'WAN', hint: 'WAN events: uplink transition, failover' },
  { id: 'other', prefixes: [], label: 'Other', hint: 'Events outside the known families' },
];

const FAMILY_BY_ID: Record<FamilyId, Family> = Object.fromEntries(
  FAMILIES.map((f) => [f.id, f]),
) as Record<FamilyId, Family>;

/** Longest-prefix-first so `WAN`/`USER` win over a hypothetical shorter clash. */
const PREFIX_INDEX: Array<[string, FamilyId]> = FAMILIES.flatMap((f) =>
  f.prefixes.map((p) => [p, f.id] as [string, FamilyId]),
).sort((a, b) => b[0].length - a[0].length);

/** Strip the transport prefix (`EVT_` for events, `ANOMALY_` for anomalies). */
function keyBody(key: string): string {
  if (key.startsWith('EVT_')) return key.slice(4);
  if (key.startsWith('ANOMALY_')) return key.slice(8);
  return key;
}

/** Resolve an event key to its family; unknown keys fall to `other`. */
export function familyOf(key: string): FamilyId {
  const body = keyBody(key);
  for (const [prefix, id] of PREFIX_INDEX) {
    if (body === prefix || body.startsWith(`${prefix}_`) || body.startsWith(prefix)) {
      // Guard against `WU` matching `WUX`: require a boundary (end or `_`) OR an
      // uppercase-name boundary like `WUDisconnected` (UniFi omits separators).
      const rest = body.slice(prefix.length);
      if (rest === '' || rest[0] === '_' || /[A-Z0-9]/.test(rest[0])) return id;
    }
  }
  return 'other';
}

export function familyLabel(id: FamilyId): string {
  return FAMILY_BY_ID[id].label;
}

/**
 * Whether a key denotes an operational fault (link/adopt loss, PoE overload,
 * WAN failover, DFS radar, unexpected restart) — the ONLY place a severity color
 * is licensed for events. A benign roam/connect is never tinted. Every
 * `ANOMALY_*` is a fault by definition: the controller only emits one when a
 * client's experience has degraded (Gitea #52), and most anomaly codes miss the
 * substring set below.
 */
const FAULT_RE =
  /(disconnect|lost|overload|error|fail|down|radar|rogue|blocked|reset|restart|degrad|critical|alarm|isolat|storm|loop|flap|denied|reject)/i;

export function isFaultKey(key: string): boolean {
  return key.startsWith('ANOMALY_') || FAULT_RE.test(key);
}

/** Human label for a raw key: strip the transport prefix, space out CamelCase. */
export function humanizeKey(key: string): string {
  return keyBody(key)
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim();
}

/** A controller `msg` that is itself a machine code (e.g. a raw anomaly label),
 * not human prose. Codes get humanised; prose is shown as-is. */
function isCode(s: string): boolean {
  return /^[A-Z0-9_]+$/.test(s);
}

function sentenceCase(s: string): string {
  const t = s.trim().toLowerCase();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
}

/**
 * Curated plain-language names for the anomaly codes we actually see, keyed by
 * the uppercase `ANOMALY_<CODE>` tail. "Clients", never "devices"/"users" (a
 * UniFi admin reads "device" as an AP/switch and "user" as an administrator).
 * Anything not listed falls back to a stripped, sentence-cased humanisation, so
 * a new code is legible rather than raw.
 */
const ANOMALY_LABEL: Record<string, string> = {
  USER_DNS_TIMEOUT: 'DNS timeout',
  USER_HIGH_DNS_LATENCY: 'High DNS latency',
  USER_HIGH_TCP_LATENCY: 'High TCP latency',
  AP_LONG_MAX_ASSOCIATION_TIME: 'Clients slow to associate',
};

/**
 * Plain-language label for an `ANOMALY_*` event. The code comes from the raw
 * `msg`/`data.anomaly` when present (that is the exact controller label), else
 * from the key tail. Curated first, humanised fallback second.
 */
export function anomalyLabel(key: string, rawCode?: string | null): string {
  const raw = rawCode && isCode(rawCode) ? rawCode : key.replace(/^ANOMALY_/, '');
  const code = raw.toUpperCase();
  const curated = ANOMALY_LABEL[code];
  if (curated) return curated;
  return sentenceCase(code.replace(/^(USER|AP|LU|WU|SW|GW|WAN)_/, '').replace(/_/g, ' '));
}

/**
 * The plain-language sentence for one event, shared by the Timeline and the
 * dashboard ticker. An anomaly resolves through the curated map; a real event
 * whose `msg` is human prose uses that prose; a code-only `msg` (or none) falls
 * back to humanising the key. The raw key stays available beside it for experts.
 */
export function eventSentence(key: string, msg?: string | null): string {
  if (key.startsWith('ANOMALY_')) return anomalyLabel(key, msg);
  if (msg && !isCode(msg)) return msg;
  return humanizeKey(key);
}

/**
 * UniFi `device.state` integer -> plain word (mirrors the documented set in
 * `netadmin/detect/detectors/infra.py`). Unknown codes keep an honest
 * `state N` rather than a guess. Callers that already print the "State" noun
 * (the device state-history rail) get just the word back — no "State state 0".
 */
const DEVICE_STATE_LABEL: Record<string, string> = {
  '0': 'offline',
  '1': 'connected',
  '2': 'pending adoption',
  '4': 'upgrading',
  '5': 'provisioning',
  '6': 'heartbeat missed',
  '7': 'adopting',
};

export function deviceStateLabel(value: string | null): string {
  if (value == null) return '—';
  return DEVICE_STATE_LABEL[value] ?? `state ${value}`;
}

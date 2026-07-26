/**
 * Event-key taxonomy for the /timeline density view.
 *
 * UniFi event keys are `EVT_<FAMILY>_<Name>` (e.g. `EVT_WU_Roam`,
 * `EVT_SW_PoeOverload`, `EVT_WAN_Transition`). The FAMILY prefix is the filter
 * axis; it does NOT encode severity. Color, per DESIGN_FOUNDATION chart rules,
 * appears "only where the event IS a severity" — so the density is drawn in the
 * single accent, and a separate *fault* band (a curated set of keys that denote
 * an operational fault) gets the one severity tint. Families never get a rainbow.
 */

export type FamilyId = 'wifi' | 'wired' | 'ap' | 'switch' | 'gateway' | 'wan' | 'other';

export interface Family {
  id: FamilyId;
  /** UniFi key prefix(es) that map here, matched after the leading `EVT_`. */
  prefixes: string[];
  label: string;
  /** One-line description for the filter chip's title attribute. */
  hint: string;
}

/**
 * Ordered for the filter row. `other` is the catch-all for keys that do not
 * match a known prefix (kept honest rather than silently dropped).
 */
export const FAMILIES: Family[] = [
  { id: 'wifi', prefixes: ['WU'], label: 'Wi-Fi clients', hint: 'Wireless client events: connect, roam, disconnect' },
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

/** Longest-prefix-first so `WAN` wins over a hypothetical `W*`. */
const PREFIX_INDEX: Array<[string, FamilyId]> = FAMILIES.flatMap((f) =>
  f.prefixes.map((p) => [p, f.id] as [string, FamilyId]),
).sort((a, b) => b[0].length - a[0].length);

/** Resolve an event key to its family; unknown keys fall to `other`. */
export function familyOf(key: string): FamilyId {
  const body = key.startsWith('EVT_') ? key.slice(4) : key;
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
 * WAN failover, DFS radar, unexpected restart). Substring match on the key name,
 * case-insensitive. This is the ONLY place a severity color is licensed for
 * events — a benign roam/connect is never tinted.
 */
const FAULT_RE =
  /(disconnect|lost|overload|error|fail|down|radar|rogue|blocked|reset|restart|degrad|critical|alarm|isolat|storm|loop|flap|denied|reject)/i;

export function isFaultKey(key: string): boolean {
  return FAULT_RE.test(key);
}

/** Human label for an event key: strip `EVT_`, space out CamelCase. */
export function humanizeKey(key: string): string {
  const body = key.startsWith('EVT_') ? key.slice(4) : key;
  return body
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .trim();
}

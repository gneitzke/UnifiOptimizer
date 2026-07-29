import { test, expect, type Page, type Route } from '@playwright/test';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Operator suppression, on its primary surfaces (Gitea #49/#50). Self-contained:
 * every `/api/*` call is mocked with page.route from fixtures below, so this needs
 * no daemon, no controller and no auth-setup, and never touches the live network.
 *
 * What it guards:
 *   - the Suppressed filter tab shows the suppressed-but-active issues, and the
 *     attention views (Open) hide them behind a disclosure caption (nothing shrinks
 *     silently);
 *   - the escalation-void note (D3): a suppression lifted by a severity rise past
 *     the suppressed level is DERIVED with no event, so the lifecycle trail
 *     synthesizes the row that explains why the issue is back in the counts;
 *   - the incident bulk-suppress control (D4) round-trips: one action parks the
 *     whole incident and one lifts it, reflected in the member badges (D5).
 *
 * It also drops light/dark screenshots into scratch_validation/suppression/
 * (gitignored), and proves each light shot actually rendered light two ways: the
 * computed body background at capture time, and the saved PNG's own brightness.
 */

const require = createRequire(import.meta.url);
// pngjs, bundled inside playwright-core — lets the saved PNG be re-sampled to
// prove a "light" file is not secretly the dark one (an earlier agent shipped
// two dark files labelled light/dark).
const { PNG } = require('playwright-core/lib/utilsBundle') as {
  PNG: { sync: { read(buf: Buffer): { width: number; height: number; data: Buffer } } };
};

const BASE = 'http://localhost:5173';
const OUT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../scratch_validation/suppression',
);
const NOW = Math.floor(Date.now() / 1000);

/** Match a backend path anchored at the host root — /api/… — so the app's own
 *  module URLs under /src/api/ are never intercepted. Specific handlers below are
 *  end-anchored (`…$`) so they never shadow one another regardless of the order
 *  Playwright resolves them in (most-recently-registered wins): `/api/issues` must
 *  not swallow `/api/issues/1/fix-history`, or ProposedFix gets `{}` and crashes. */
const api = (suffix: string) => new RegExp(`^https?://[^/]+/api/${suffix}`);

/** The canvas background each theme paints on `<body>` (from src/index.css). Used
 *  as the authoritative "the theme actually applied" check at capture time. */
const BODY_BG = { light: 'rgb(245, 245, 247)', dark: 'rgb(22, 22, 24)' } as const;

/** Mean luma of a saved PNG over a coarse grid. A light-theme page is
 *  overwhelmingly bright, a dark-theme one overwhelmingly dark; comparing the two
 *  files of one scenario catches the "both dark" failure without a magic absolute. */
function meanLuma(file: string): number {
  const png = PNG.sync.read(fs.readFileSync(file));
  let sum = 0;
  let n = 0;
  for (let y = 0; y < png.height; y += 24) {
    for (let x = 0; x < png.width; x += 24) {
      const i = (png.width * y + x) * 4;
      sum += 0.299 * png.data[i] + 0.587 * png.data[i + 1] + 0.114 * png.data[i + 2];
      n += 1;
    }
  }
  return sum / n;
}

/** Capture a full-page screenshot after proving the theme really applied, and
 *  return the saved file's mean luma so a caller can compare light vs dark. */
async function shot(page: Page, theme: 'light' | 'dark', name: string): Promise<number> {
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor.trim());
  expect(bg, `body background for ${name} (${theme})`).toBe(BODY_BG[theme]);
  const file = path.join(OUT, `${name}-${theme}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return meanLuma(file);
}

async function initTheme(page: Page, theme: 'light' | 'dark') {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('netadmin_theme', t);
      // Otherwise the first-run tour takes over the page and walks the dashboard.
      localStorage.setItem('netadmin_tour_seen', '1');
    } catch {
      /* ignore */
    }
  }, theme);
}

/* ---- Fixtures ------------------------------------------------------------ */

const AP_ENTITY = {
  entity_id: 1,
  name: 'Back Porch',
  type: 'ap',
  native_id: 'demo-ap-1',
  model: 'U7',
  parent_id: null,
  parent_name: null,
};

type Sev = 'p1' | 'p2' | 'p3';
interface IssueOverrides {
  id: number;
  title: string;
  severity: Sev;
  state?: string;
  detector_key?: string;
  entity?: typeof AP_ENTITY | null;
  suppressed_ts?: number | null;
  suppress_until_ts?: number | null;
  suppressed_severity?: Sev | null;
}

/** An /api/issues row with honest defaults; overrides win. */
function issue(o: IssueOverrides) {
  return {
    id: o.id,
    fingerprint: `fp-${o.id}`,
    detector_key: o.detector_key ?? 'wifi.airtime_saturation',
    entity_id: o.entity === null ? null : (o.entity ?? AP_ENTITY).entity_id,
    entity: o.entity === undefined ? AP_ENTITY : o.entity,
    severity: o.severity,
    state: o.state ?? 'active',
    first_seen_ts: NOW - 7200,
    last_seen_ts: NOW - 60,
    resolved_ts: null,
    clear_streak: 0,
    occurrences: 3,
    ack_ts: null,
    snooze_until_ts: null,
    suppressed_ts: o.suppressed_ts ?? null,
    suppress_until_ts: o.suppress_until_ts ?? null,
    suppressed_severity: o.suppressed_severity ?? null,
    title: o.title,
    evidence: {},
    fix_state: null,
    reopened_from: null,
    impact: null,
    lifecycle: null,
    incident_id: null,
    incident_role: null,
    incident_brief: null,
  };
}

const FIX_HISTORY = {
  issue_id: 0,
  fix_state: null,
  verification: { status: 'not_armed', armed_ts: null, window_end_ts: null, resolved_ts: null },
  changes: [],
};

// The issue detail's related-metric charts fetch a metric window per evidence
// hint; an empty-but-valid window renders a "no data" chart instead of crashing
// SingleMetricChart on `buckets.length`.
const METRIC_WINDOW = {
  entity_id: 1,
  metric: '',
  series_id: 0,
  tier: 'raw',
  start_ts: NOW - 3600,
  end_ts: NOW,
  seconds: 3600,
  points: 0,
  raw_count: 0,
  buckets: [],
};

async function mockShell(page: Page) {
  // Catch-all first (broad, unanchored): every /api/* the specific end-anchored
  // routes below don't claim falls here (health, metrics window, …).
  await page.route(api(''), (r: Route) => r.fulfill({ json: {} }));
  await page.route(api('setup/status$'), (r: Route) =>
    r.fulfill({ json: { configured: true, controller_connected: true } }),
  );
  // The issue-detail sub-fetches (ProposedFix + InvestigationPanel), so a mocked
  // detail page renders its full tree instead of crashing on `{}`.
  await page.route(api('issues/\\d+/fix-history$'), (r: Route) => r.fulfill({ json: FIX_HISTORY }));
  await page.route(api('issues/\\d+/investigations$'), (r: Route) =>
    r.fulfill({ json: { investigations: [], count: 0 } }),
  );
  await page.route(api('issues/investigate/providers$'), (r: Route) =>
    r.fulfill({ json: { providers: [] } }),
  );
  // Metric window carries a query string, so match by prefix (not end-anchored).
  await page.route(api('metrics/window'), (r: Route) => r.fulfill({ json: METRIC_WINDOW }));
}

/* ---- The issues list: Suppressed filter + disclosure --------------------- */

const LIST_ISSUES = [
  issue({ id: 1, title: 'Airtime saturation on Loft', severity: 'p1' }),
  issue({ id: 2, title: 'DNS latency spike', severity: 'p2', detector_key: 'wan.dns' }),
  // Suppressed but still active: muted 3 days ago at its current severity.
  issue({
    id: 3,
    title: 'Roaming churn on Kitchen',
    severity: 'p3',
    detector_key: 'wifi.roaming',
    suppressed_ts: NOW - 3 * 86400,
    suppress_until_ts: null,
    suppressed_severity: 'p3',
  }),
];

test.describe('Issues list — suppressed filter and disclosure', () => {
  async function gotoIssues(page: Page, theme: 'light' | 'dark' = 'light') {
    await initTheme(page, theme);
    await mockShell(page);
    await page.route(api('issues$'), (r: Route) =>
      r.fulfill({ json: { issues: LIST_ISSUES, count: LIST_ISSUES.length } }),
    );
    await page.goto(`${BASE}/issues`);
    // The top bar carries an <h1>Issues</h1> too; the page's own title is the h2.
    await expect(page.getByRole('heading', { level: 2, name: 'Issues' })).toBeVisible({
      timeout: 15000,
    });
  }

  test('Open hides suppressed rows behind a disclosure; Suppressed shows only them', async ({
    page,
  }) => {
    await gotoIssues(page);

    const list = page.getByRole('listbox');
    // Open (default): the two unsuppressed issues show; the suppressed one does not.
    await expect(list.getByText('Airtime saturation on Loft')).toBeVisible();
    await expect(list.getByText('DNS latency spike')).toBeVisible();
    await expect(list.getByText('Roaming churn on Kitchen')).toHaveCount(0);

    // …and its absence is disclosed, never silent.
    const disclosure = page.getByRole('button', { name: /1 suppressed issue is still active/ });
    await expect(disclosure).toBeVisible();
    // The header reconciles the shown count against the total, too.
    await expect(page.getByText('2 shown of 3')).toBeVisible();

    // The Suppressed tab shows the suppressed-but-active issue, with its badge.
    await page.getByRole('button', { name: 'Suppressed', exact: true }).click();
    await expect(list.getByText('Roaming churn on Kitchen')).toBeVisible();
    await expect(list.getByText('Airtime saturation on Loft')).toHaveCount(0);
    // The neutral Suppressed badge rides the row (same badge as everywhere else).
    await expect(list.getByText('Suppressed', { exact: true })).toBeVisible();
  });

  test('the disclosure caption links into the Suppressed view', async ({ page }) => {
    await gotoIssues(page);
    await page.getByRole('button', { name: /1 suppressed issue is still active/ }).click();
    await expect(page.getByRole('listbox').getByText('Roaming churn on Kitchen')).toBeVisible();
  });

  test('screenshots — light and dark', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoIssues(page, 'light');
    await page.getByRole('button', { name: 'Suppressed', exact: true }).click();
    await expect(page.getByRole('listbox').getByText('Roaming churn on Kitchen')).toBeVisible();
    const light = await shot(page, 'light', 'issues-suppressed');

    await gotoIssues(page, 'dark');
    await page.getByRole('button', { name: 'Suppressed', exact: true }).click();
    await expect(page.getByRole('listbox').getByText('Roaming churn on Kitchen')).toBeVisible();
    const dark = await shot(page, 'dark', 'issues-suppressed');

    expect(light, 'light PNG must be markedly brighter than dark').toBeGreaterThan(dark + 60);
  });
});

/* ---- Issue detail: the escalation-void note (D3) ------------------------- */

// Suppressed at P3, then escalated to P1: suppression is void by derivation (no
// event), so the trail must synthesize the row that explains the return.
const ESCALATED_ISSUE = issue({
  id: 201,
  title: 'Sticky client on Garage AP',
  severity: 'p1',
  detector_key: 'wifi.sticky_client',
  suppressed_ts: NOW - 3 * 86400,
  suppress_until_ts: null,
  suppressed_severity: 'p3',
});

const ESCALATED_DETAIL = {
  issue: ESCALATED_ISSUE,
  entity: AP_ENTITY,
  evidence: {},
  evidence_layout: [],
  confounders: [],
  confounder_notes: {},
  events: [
    { id: 1, issue_id: 201, ts: NOW - 3 * 86400, kind: 'detected', detail: { severity: 'p3' } },
    {
      id: 2,
      issue_id: 201,
      ts: NOW - 3 * 86400 + 60,
      kind: 'suppressed',
      detail: { severity: 'p3', source: 'operator' },
    },
    {
      id: 3,
      issue_id: 201,
      ts: NOW - 3600,
      kind: 'escalated',
      detail: { reason: 'm_reached', m: 3, occurrences: 5 },
    },
  ],
  incident: null,
};

test.describe('Issue detail — escalation-void note (D3)', () => {
  async function gotoDetail(page: Page, theme: 'light' | 'dark' = 'light') {
    await initTheme(page, theme);
    await mockShell(page);
    await page.route(api('issues$'), (r: Route) => r.fulfill({ json: { issues: [], count: 0 } }));
    await page.route(api('issues/\\d+$'), (r: Route) => r.fulfill({ json: ESCALATED_DETAIL }));
    await page.goto(`${BASE}/issues/201`);
    await expect(page.getByRole('heading', { name: 'Sticky client on Garage AP' })).toBeVisible({
      timeout: 15000,
    });
  }

  test('the trail synthesizes a "Suppression lifted" row naming the severity rise', async ({
    page,
  }) => {
    await gotoDetail(page);
    await expect(page.getByText('Suppression lifted', { exact: true })).toBeVisible();
    await expect(page.getByText(/Severity rose from Low to Critical/)).toBeVisible();
    // It is genuinely back in the attention surfaces: the muted banner is gone and
    // the action offers Suppress again, not Unsuppress.
    await expect(page.getByRole('button', { name: 'Suppress' })).toBeVisible();
    await expect(page.getByText(/excluded from counts and alerts/)).toHaveCount(0);
  });

  test('screenshots — light and dark', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await gotoDetail(page, 'light');
    await expect(page.getByText('Suppression lifted', { exact: true })).toBeVisible();
    const light = await shot(page, 'light', 'escalation-void');

    await gotoDetail(page, 'dark');
    await expect(page.getByText('Suppression lifted', { exact: true })).toBeVisible();
    const dark = await shot(page, 'dark', 'escalation-void');

    expect(light).toBeGreaterThan(dark + 60);
  });
});

/* ---- Incident detail: bulk suppress (D4) + member badge (D5) ------------- */

function incidentDetail(rootSuppressed: boolean, symptomSuppressed: boolean) {
  const sup = (on: boolean) =>
    on
      ? { suppressed_ts: NOW - 20, suppress_until_ts: null, suppressed_severity: 'p2' as Sev }
      : {};
  const rootIssue = issue({
    id: 101,
    title: 'Weak mesh backhaul on Back Porch',
    severity: 'p2',
    detector_key: 'wifi.mesh_uplink',
    ...sup(rootSuppressed),
  });
  const symptomIssue = issue({
    id: 102,
    title: 'Coverage hole on Back Porch',
    severity: 'p2',
    detector_key: 'net.coverage_hole',
    ...sup(symptomSuppressed),
  });
  return {
    incident: {
      id: 5,
      fingerprint: 'inc-5',
      root_issue_id: 101,
      severity: 'p2',
      state: 'open',
      first_seen_ts: NOW - 7200,
      last_seen_ts: NOW - 60,
      resolved_ts: null,
      title: 'Weak mesh backhaul on Back Porch',
      summary: 'A weak mesh uplink on Back Porch is starving the AP, opening a coverage hole beneath it.',
      member_count: 2,
      symptom_count: 1,
      root: null,
    },
    root: { issue: rootIssue, entity: AP_ENTITY, role: 'root', rule: 'root', rationale: '' },
    symptoms: [
      {
        issue: symptomIssue,
        entity: AP_ENTITY,
        role: 'symptom',
        rule: 'mesh_uplink->coverage_hole',
        rationale: 'Same AP, and the coverage hole opened after the uplink weakened.',
      },
    ],
    recommended_fix: { issue_id: 101, detector_key: 'wifi.mesh_uplink', fix_state: null },
    investigation: { issue_id: 101 },
  };
}

test.describe('Incident detail — bulk suppress (D4) and member badge (D5)', () => {
  /** Wire an incident whose suppression state is mutable, so a POST round-trips
   *  through the same reload path the app uses. */
  async function gotoIncident(
    page: Page,
    opts: { theme?: 'light' | 'dark'; rootSuppressed?: boolean; symptomSuppressed?: boolean } = {},
  ) {
    const state = {
      root: opts.rootSuppressed ?? false,
      symptom: opts.symptomSuppressed ?? false,
    };
    await initTheme(page, opts.theme ?? 'light');
    await mockShell(page);
    await page.route(api('issues$'), (r: Route) => r.fulfill({ json: { issues: [], count: 0 } }));
    // End-anchored, so the detail GET and the two mutation POSTs never shadow each
    // other whatever order Playwright resolves them in.
    await page.route(api('incidents/\\d+$'), (r: Route) =>
      r.fulfill({ json: incidentDetail(state.root, state.symptom) }),
    );
    await page.route(api('incidents/\\d+/suppress$'), (r: Route) => {
      state.root = true;
      state.symptom = true;
      return r.fulfill({ json: { incident_id: 5, count: 2 } });
    });
    await page.route(api('incidents/\\d+/unsuppress$'), (r: Route) => {
      state.root = false;
      state.symptom = false;
      return r.fulfill({ json: { incident_id: 5, count: 2 } });
    });
    await page.goto(`${BASE}/incidents/5`);
    await expect(
      page.getByRole('heading', { name: 'Weak mesh backhaul on Back Porch' }),
    ).toBeVisible({ timeout: 15000 });
  }

  test('Suppress incident parks every member and Unsuppress lifts them (round-trip)', async ({
    page,
  }) => {
    await gotoIncident(page);

    // Nothing suppressed yet: the control offers Suppress, no member badge.
    await expect(page.getByRole('button', { name: 'Suppress incident' })).toBeVisible();
    await expect(page.getByText('Suppressed', { exact: true })).toHaveCount(0);

    // Suppress the whole incident.
    await page.getByRole('button', { name: 'Suppress incident' }).click();
    await page.getByRole('button', { name: 'Until I unsuppress' }).click();

    // Both members now read as suppressed, and the control flips to Unsuppress.
    await expect(page.getByRole('button', { name: 'Unsuppress incident' })).toBeVisible();
    // Two member rows (root + symptom), each carrying the badge.
    await expect(page.getByText('Suppressed', { exact: true })).toHaveCount(2);

    // Lift it: back to the Suppress control, badges gone.
    await page.getByRole('button', { name: 'Unsuppress incident' }).click();
    await expect(page.getByRole('button', { name: 'Suppress incident' })).toBeVisible();
    await expect(page.getByText('Suppressed', { exact: true })).toHaveCount(0);
  });

  test('screenshots — light and dark (control + a suppressed member badge)', async ({ page }) => {
    // Partially suppressed: the root is muted (badge, D5) while the symptom is live,
    // so the Suppress-incident control is still offered (D4) — both in one frame.
    await page.setViewportSize({ width: 1440, height: 1000 });

    await gotoIncident(page, { theme: 'light', rootSuppressed: true, symptomSuppressed: false });
    await expect(page.getByRole('button', { name: 'Suppress incident' })).toBeVisible();
    await expect(page.getByText('Suppressed', { exact: true })).toHaveCount(1);
    const light = await shot(page, 'light', 'incident-bulk-suppress');

    await gotoIncident(page, { theme: 'dark', rootSuppressed: true, symptomSuppressed: false });
    await expect(page.getByRole('button', { name: 'Suppress incident' })).toBeVisible();
    const dark = await shot(page, 'dark', 'incident-bulk-suppress');

    expect(light).toBeGreaterThan(dark + 60);
  });
});

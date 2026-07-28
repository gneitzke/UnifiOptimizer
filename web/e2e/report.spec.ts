import { test, expect, type Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { demoReport } from './fixtures/demoReport';

/**
 * Report page (docs/ARCHITECTURE.md §19). Self-contained: `GET /api/report` and
 * the setup-status gate read are mocked with page.route from the demo fixture, so
 * this needs no backend and no auth. It asserts the page renders the model AS
 * GIVEN and drops screenshots + a print-to-PDF into scratch_validation/report/.
 */

const BASE = 'http://localhost:5173';

/** The demo network, re-shaped to the density a real site produces: long AP names
 *  and a full 2.4/5/6 GHz neighbour scan. Used only by the label-geometry test. */
const denseReport = {
  ...demoReport,
  rf: {
    ...demoReport.rf,
    utilization: [
      { entity_id: 31, ap_name: 'U7 Pro - Kitchen Ceiling', band: '2.4', channel: 6, cu_total: 45, cu_self: 20, cu_non_self: 25 },
      { entity_id: 41, ap_name: 'U7 Pro - Garage Workshop', band: '2.4', channel: 1, cu_total: 74, cu_self: 30, cu_non_self: 44 },
      { entity_id: 51, ap_name: 'U7 Pro XG - Upstairs Landing', band: '2.4', channel: 1, cu_total: 75, cu_self: 31, cu_non_self: 44 },
    ],
    neighbor_density: {
      ...demoReport.rf.neighbor_density,
      by_channel: [
        { band: '2.4', channel: 1, count: 128 }, { band: '2.4', channel: 2, count: 2 },
        { band: '2.4', channel: 3, count: 4 }, { band: '2.4', channel: 4, count: 3 },
        { band: '2.4', channel: 5, count: 3 }, { band: '2.4', channel: 6, count: 165 },
        { band: '2.4', channel: 8, count: 1 }, { band: '2.4', channel: 9, count: 3 },
        { band: '2.4', channel: 11, count: 94 }, { band: '5', channel: 36, count: 3 },
        { band: '5', channel: 40, count: 2 }, { band: '5', channel: 44, count: 6 },
        { band: '5', channel: 48, count: 7 }, { band: '5', channel: 149, count: 2 },
        { band: '5', channel: 153, count: 4 }, { band: '5', channel: 157, count: 3 },
        { band: '5', channel: 161, count: 3 }, { band: '6', channel: 197, count: 1 },
      ],
    },
  },
};
const OUT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../scratch_validation/report',
);

/** Match a backend path anchored at the host root — /api/… — so the app's own
 *  module URLs under /src/api/ are never intercepted. */
const api = (suffix: string) => new RegExp(`^https?://[^/]+/api/${suffix}`);

async function mockApi(page: Page) {
  // Register the catch-all FIRST: Playwright runs the most-recently-registered
  // matching route first, so the specific handlers below must win over this.
  await page.route(api(''), (r) => r.fulfill({ json: {} }));
  await page.route(api('issues'), (r) => r.fulfill({ json: { issues: [], count: 0 } }));
  await page.route(api('report'), (r) => r.fulfill({ json: demoReport }));
  await page.route(api('setup/status'), (r) =>
    r.fulfill({ json: { configured: true, controller_connected: true } }),
  );
}

async function gotoReport(page: Page, theme?: 'dark' | 'light') {
  if (theme) {
    await page.addInitScript((t) => {
      try {
        localStorage.setItem('netadmin_theme', t);
      } catch {
        /* ignore */
      }
    }, theme);
  }
  await mockApi(page);
  await page.goto(`${BASE}/report`);
  await expect(page.getByRole('heading', { name: 'Network Assessment' })).toBeVisible({
    timeout: 15000,
  });
  // Let the SVG charts measure their containers and lay out.
  await page.waitForTimeout(600);
}

test.describe('Report page', () => {
  test('renders the model as given', async ({ page }) => {
    await gotoReport(page);

    // Cover + a field from every section, proving each renders its real data.
    await expect(page.getByText('Report date')).toBeVisible();
    await expect(page.getByRole('heading', { name: /Executive summary/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Scope & methodology/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Network inventory/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Topology/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Health & performance/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /RF environment/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Client analysis/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Detailed findings/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Recommendations/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Appendix/ })).toBeVisible();

    // A finding id and a severity chip from the fixed template.
    await expect(page.getByText('WLAN-01').first()).toBeVisible();
    await expect(page.getByText('Garden AP mesh backhaul is weak and slow').first()).toBeVisible();

    // The export control exists and is marked no-print.
    const exportBtn = page.getByRole('button', { name: /Export \/ Save as PDF/ });
    await expect(exportBtn).toBeVisible();

    await page.screenshot({ path: path.join(OUT, 'report-light-full.png'), fullPage: true });
    await page.screenshot({ path: path.join(OUT, 'report-light-top.png') });
  });

  test('renders in dark mode', async ({ page }) => {
    await gotoReport(page, 'dark');
    await expect(page.getByText('Report date')).toBeVisible();
    await page.screenshot({ path: path.join(OUT, 'report-dark-full.png'), fullPage: true });
    await page.screenshot({ path: path.join(OUT, 'report-dark-top.png') });
  });

  test('prints to a clean multi-page PDF', async ({ page }) => {
    await gotoReport(page);
    // No headerTemplate/footerTemplate: the site + confidential running header is
    // the in-flow `.report-runhead` band, and page numbers come from the @page
    // margin box in print.css (headless Chromium honours it), matching what the
    // shipped Save-as-PDF produces without a duplicated header.
    const pdf = await page.pdf({
      path: path.join(OUT, 'report.pdf'),
      format: 'Letter',
      printBackground: true,
      preferCSSPageSize: true,
    });
    expect(pdf.byteLength).toBeGreaterThan(20_000);
  });

  /**
   * Chart labels must fit the box they are painted in.
   *
   * SVG silently paints text outside its viewBox and lets the clip eat it, and a
   * categorical axis happily paints every label on top of its neighbour. Both
   * shipped: the RF "airtime by radio" rows lost their leading characters
   * ("U7 Pro - Kitchen . ch 6" arrived as "ro - Kitchen . ch 6"), and the
   * service-level axis ran together as "CoverageRoamingCapacityConnectivity...".
   * Neither is visible to a DOM-text assertion -- the text nodes are all present
   * and correct -- so this measures rendered geometry instead.
   */
  test('chart labels are neither clipped nor overlapping', async ({ page }) => {
    // Both defects are width-dependent and invisible at a desktop viewport: the
    // charts get generous slots and everything fits. They appear at the width the
    // PDF actually renders at, so emulate that page box rather than the browser's.
    await page.setViewportSize({ width: 816, height: 1056 }); // US Letter @ 96dpi
    await page.emulateMedia({ media: 'print' });

    // The shared demo fixture is a small, tidily-named network and does not
    // reproduce either defect. Both need what real sites have: device names long
    // enough to overflow the label gutter, and a 2.4/5/6 GHz scan covering enough
    // channels that the categorical axis runs out of room. Registered after
    // mockApi so this denser payload wins.
    await mockApi(page);
    await page.route(api('report'), (r) => r.fulfill({ json: denseReport }));
    await page.goto(`${BASE}/report`);
    await expect(page.getByRole('heading', { name: 'Network Assessment' })).toBeVisible({
      timeout: 15000,
    });
    await page.waitForTimeout(600);
    await expect(page.getByRole('heading', { name: /RF environment/ })).toBeVisible();

    const problems = await page.evaluate(() => {
      const clipped: string[] = [];
      const overlapping: string[] = [];

      for (const svg of Array.from(document.querySelectorAll('figure svg'))) {
        const texts = Array.from(svg.querySelectorAll('text')) as SVGTextElement[];
        const boxes = texts.map((t) => ({ text: t.textContent ?? '', box: t.getBBox() }));

        for (const { text, box } of boxes) {
          // Painted past the left edge of the viewBox: the head of the string is
          // clipped away and the reader sees a different word.
          if (box.x < -0.5) clipped.push(text);
        }

        // Same baseline == same axis row. Sorted by x, each label must start after
        // the previous one ends.
        const rows = new Map<number, { text: string; box: DOMRect }[]>();
        for (const b of boxes) {
          const key = Math.round(b.box.y);
          if (!rows.has(key)) rows.set(key, []);
          rows.get(key)!.push(b as { text: string; box: DOMRect });
        }
        for (const row of rows.values()) {
          const sorted = row.slice().sort((a, b) => a.box.x - b.box.x);
          for (let i = 1; i < sorted.length; i++) {
            const prev = sorted[i - 1];
            const cur = sorted[i];
            if (cur.box.x < prev.box.x + prev.box.width - 0.5) {
              overlapping.push(`${prev.text} / ${cur.text}`);
            }
          }
        }
      }
      return { clipped, overlapping };
    });

    expect(problems.clipped, 'labels painted outside the chart viewBox').toEqual([]);
    expect(problems.overlapping, 'labels painted over their neighbour').toEqual([]);
  });
});

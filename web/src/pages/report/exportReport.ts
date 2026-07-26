/**
 * Export-report action (docs/ARCHITECTURE.md §19; docs/REPORT_SPEC.md §Delivery).
 *
 * The report is delivered as a browser print-to-PDF — there is no server-side PDF
 * engine, which keeps the daemon dependency-light per the "pip install and run"
 * value. These helpers hold the small amount of logic the Export entry points
 * (the dashboard button and the sidebar destination) and the report page's
 * PrintButton share, so every surface triggers the same one action.
 *
 * Contract with the report-page agent:
 *   - The report renders at {@link REPORT_ROUTE}.
 *   - Its print button calls {@link printReport} and shows {@link PRINT_HINT}
 *     beside it; the button is marked `no-print` so it drops out of the PDF.
 *   - The app chrome (sidebar, top bar) is marked `no-print` in the shell, so the
 *     report prints as a standalone document rather than inside the dashboard.
 */

/** Route that renders the print-optimised network assessment report. */
export const REPORT_ROUTE = '/report';

/**
 * One-line guidance shown next to the print button. Browsers label the action
 * "Save as PDF" in their print destination list; background graphics must be on,
 * or the chart fills and severity chips print blank.
 */
export const PRINT_HINT =
  'In the print dialog, pick "Save as PDF" as the destination and turn on ' +
  'background graphics so the charts and severity colours are kept.';

/**
 * Open the browser print dialog for the current page. Kept as a single
 * window.print() wrapper so the report page's button and any future export entry
 * point stay in sync, and so the call is guarded for non-browser contexts (tests,
 * SSR). The `.no-print` CSS (see src/index.css) hides the app chrome and this
 * control in the print output.
 */
export function printReport(): void {
  if (typeof window === 'undefined' || typeof window.print !== 'function') {
    return;
  }
  window.print();
}

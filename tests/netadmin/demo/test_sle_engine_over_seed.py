"""The real SLE MinutesEngine, run over a slice of the seeded demo week.

The demo hand-writes SLE classifier rows (``seed._Seeder.build_sle`` calls
``upsert_sle_minute`` directly), so the real :class:`SleMinutesJob` never ran over
the seeded series. That is exactly why the isp_loss unit-mismatch bug stayed
invisible in the demo: the seed writes a ``wan_drops`` per-interval packet COUNT of
~2-3.5, and the old rule judged that raw count against a percentage threshold, so
every active client in a bucket with more than one dropped packet was branded
isp_loss. Running the hand-written rows never exercised the classifier, so nothing
caught it.

This runs the engine for real over a bounded recent slice and golden-asserts the
WAN classifier distribution -- notably ``isp_loss == 0`` for the healthy window,
because every seeded ``probe.gw_rtt`` poll is OK (0% probe-measured loss). Against
pre-fix code this failed with an isp_loss avalanche. The slice is bounded (a couple
of hours, not the whole week) so the check stays fast, and it uses
``clear_existing`` so the engine's own verdicts -- not the hand-written build_sle
rows -- are what gets inspected.
"""

from __future__ import annotations

import pytest

from netadmin.demo.seed import DEFAULT_NOW, seed_demo
from netadmin.sle.classifiers import CLS_ISP_LOSS, SLE_WAN
from netadmin.sle.minutes import SleMinutesJob
from netadmin.store.repository import Repository

HOUR = 3600


@pytest.fixture(scope="module")
def demo_repo(tmp_path_factory: pytest.TempPathFactory) -> Repository:
    path = tmp_path_factory.mktemp("demo_sle") / "netadmin-demo.db"
    seed_demo(path)
    repo = Repository.open(path)
    yield repo
    repo.close()


def test_real_sle_engine_finds_no_isp_loss_in_healthy_demo_window(demo_repo: Repository) -> None:
    now = DEFAULT_NOW
    # A bounded recent slice (the last ~2 hours). This window overlaps the seed's
    # recent degraded span, where wan_drops is elevated to ~2-3.5 -- precisely the
    # counts the old rule mis-read as loss -- so it is the strongest slice for the
    # regression: pre-fix it produced an isp_loss avalanche here.
    start = now - 2 * HOUR
    job = SleMinutesJob(demo_repo)
    # clear_existing rewrites each bucket, so we inspect the engine's own verdicts,
    # not the hand-written build_sle rows.
    results = job.run_range(start, now, clear_existing=True)

    assert any(r.wan_evaluated for r in results)  # the demo HAS a gateway + probes

    rows = demo_repo.query_sle_minutes(start, now, group_by=("sle", "classifier"))
    wan = {r["classifier"]: r["minutes"] for r in rows if r["sle"] == SLE_WAN}

    # The golden: the healthy demo window carries WAN minutes, and NONE of them are
    # isp_loss (every probe poll is OK -> 0% real loss; the wan_drops COUNT never
    # drives the verdict).
    assert sum(wan.values()) > 0, f"expected the WAN SLE to be evaluated: {wan}"
    assert wan.get(CLS_ISP_LOSS, 0) == 0, f"isp_loss must be zero in a healthy window: {wan}"

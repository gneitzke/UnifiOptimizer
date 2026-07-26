"""Threshold passthrough: settings.thresholds[detector_key] reaches the detector.

End-to-end proof of the section-6 tunable seam: a value set in
``Settings.thresholds`` (the ``netadmin.thresholds`` config block) travels through
:class:`~netadmin.detect.context.DetectorContext` and
``ctx.threshold(key, name, default)`` into a real detector's decision, changing its
verdict. The same wiring the daemon's DetectorEngine uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.config import Settings
from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.wifi import AirtimeSaturationDetector
from netadmin.detect.engine import UNKNOWN
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository

NOW = 1_900_000_200


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


def _seed_radio_at_30pct(repo: Repository) -> int:
    """An AP + radio whose channel utilisation sits at a steady 30 % — below the
    default 50 % degraded floor, above a 25 % override."""
    ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-x", name="ap-x"), ts=NOW
    )
    radio = repo.upsert_entity(
        Entity(entity_type=EntityType.RADIO, native_id="radio-x", parent_id=ap, name="radio-x"),
        ts=NOW,
    )
    from netadmin.store.repository import SampleReading

    repo.record_samples(SampleReading(radio, "cu_total", NOW - 60 * i, 30.0) for i in range(1, 9))
    # fast_device coverage over the last 600 s must clear 0.5 (interval 60 s).
    with repo.transaction():
        for i in range(1, 11):
            repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 60 * i)
    return radio


def _context(repo: Repository, settings: Settings) -> DetectorContext:
    return DetectorContext.for_repository(repo, NOW, settings=settings)


def test_default_threshold_does_not_fire(repo: Repository) -> None:
    _seed_radio_at_30pct(repo)
    settings = Settings(_env_file=None)  # no overrides: degraded_pct default 50
    result = AirtimeSaturationDetector().evaluate(_context(repo, settings))
    assert result is not UNKNOWN  # coverage cleared, it did evaluate
    assert result == []  # 30 % < 50 % default -> clean


def test_override_reaches_detector_and_fires(repo: Repository) -> None:
    _seed_radio_at_30pct(repo)
    # The one line under test: an override in settings.thresholds must reach the
    # detector and flip its verdict.
    settings = Settings(
        _env_file=None,
        thresholds={"wifi.airtime_saturation": {"degraded_pct": 25}},
    )
    result = AirtimeSaturationDetector().evaluate(_context(repo, settings))
    assert result != [] and result is not UNKNOWN
    assert len(result) == 1
    finding = result[0]
    assert finding.detector_key == "wifi.airtime_saturation"
    assert finding.evidence["cu_total_median"] == pytest.approx(30.0)


def test_netadmin_db_path_env_override(monkeypatch, tmp_path):
    """NETADMIN_DB_PATH points the daemon at a different DB (demo/restore-verify)."""
    from pathlib import Path

    from netadmin.config import Settings

    target = tmp_path / "demo.db"
    monkeypatch.setenv("NETADMIN_DB_PATH", str(target))
    assert Settings(_env_file=None).db_path == Path(str(target))

    monkeypatch.delenv("NETADMIN_DB_PATH", raising=False)
    # unset -> falls back to the configured/default path, not the override
    assert Settings(_env_file=None).db_path != target

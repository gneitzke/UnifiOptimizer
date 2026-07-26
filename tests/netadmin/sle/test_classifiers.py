"""Classifier determinism and rule correctness on synthetic inputs.

Every classifier is a pure function: the same inputs must always yield the same
classifier, and each rule must fire on exactly the signal its docstring cites.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from netadmin.domain.types import EntityType
from netadmin.sle.classifiers import (
    CLS_AP_DOWN,
    CLS_ASYMMETRY,
    CLS_BUFFERBLOAT,
    CLS_CLIENT_LOAD,
    CLS_DHCP,
    CLS_GW_DOWN,
    CLS_ISP_LATENCY,
    CLS_ISP_LOSS,
    CLS_NON_WIFI_UTIL,
    CLS_PINGPONG,
    CLS_RESTART_LOOP,
    CLS_SLOW_ROAM,
    CLS_STICKY,
    CLS_SW_DOWN,
    CLS_WAN_DOWN,
    CLS_WEAK_SIGNAL,
    CLS_WIFI_INTERFERENCE,
    SleConfig,
    classify_capacity,
    classify_connect,
    classify_coverage,
    classify_roaming,
    classify_wan,
    exceeds_baseline,
    infra_down_classifier,
)


@dataclass
class _Band:
    mean: float
    std: float
    p50: float = 0.0


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_all_classifiers_are_deterministic() -> None:
    for _ in range(5):
        assert classify_coverage(-80, -95, weak_threshold_dbm=-72, snr_min_db=15) == CLS_WEAK_SIGNAL
        assert (
            classify_capacity(
                70,
                10,
                degraded_pct=50,
                self_share_min=0.6,
                neighbor_present=True,
            )
            == CLS_WIFI_INTERFERENCE
        )
        assert (
            classify_roaming(
                5,
                min_rssi=-60,
                pre_rssi=-60,
                post_rssi=-61,
                pingpong_count=3,
                sticky_rssi_dbm=-75,
                slow_roam_degradation_db=10,
            )
            == CLS_PINGPONG
        )


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #
def test_coverage_weak_below_floor() -> None:
    assert classify_coverage(-73, None, weak_threshold_dbm=-72, snr_min_db=15) == CLS_WEAK_SIGNAL


def test_coverage_ok_at_boundary() -> None:
    # exactly the floor is not "below" -> ok
    assert classify_coverage(-72, -95, weak_threshold_dbm=-72, snr_min_db=15) is None


def test_coverage_asymmetry_on_low_snr() -> None:
    # adequate RSSI but SNR = -60 - (-70) = 10 < 15 -> asymmetry
    assert classify_coverage(-60, -70, weak_threshold_dbm=-72, snr_min_db=15) == CLS_ASYMMETRY


def test_coverage_weak_takes_priority_over_asymmetry() -> None:
    # both weak and low-SNR -> weak_signal wins (mutually exclusive)
    assert classify_coverage(-80, -82, weak_threshold_dbm=-72, snr_min_db=15) == CLS_WEAK_SIGNAL


def test_coverage_no_signal_is_ok() -> None:
    assert classify_coverage(None, None, weak_threshold_dbm=-72, snr_min_db=15) is None


# --------------------------------------------------------------------------- #
# capacity
# --------------------------------------------------------------------------- #
def test_capacity_ok_below_threshold() -> None:
    assert (
        classify_capacity(40, 5, degraded_pct=50, self_share_min=0.6, neighbor_present=True) is None
    )


def test_capacity_client_load_when_self_dominates() -> None:
    assert (
        classify_capacity(80, 60, degraded_pct=50, self_share_min=0.6, neighbor_present=True)
        == CLS_CLIENT_LOAD
    )


def test_capacity_wifi_interference_with_neighbor() -> None:
    assert (
        classify_capacity(80, 10, degraded_pct=50, self_share_min=0.6, neighbor_present=True)
        == CLS_WIFI_INTERFERENCE
    )


def test_capacity_non_wifi_when_unexplained() -> None:
    assert (
        classify_capacity(80, 10, degraded_pct=50, self_share_min=0.6, neighbor_present=False)
        == CLS_NON_WIFI_UTIL
    )


def test_capacity_fires_on_baseline_even_below_absolute() -> None:
    # 30% is below the 50% floor but >2σ over a quiet baseline (mean 10, std 5)
    band = _Band(mean=10, std=5)
    assert (
        classify_capacity(
            30, 2, degraded_pct=50, self_share_min=0.6, neighbor_present=False, band=band, sigmas=2
        )
        == CLS_NON_WIFI_UTIL
    )


# --------------------------------------------------------------------------- #
# roaming
# --------------------------------------------------------------------------- #
def test_roaming_pingpong_on_count() -> None:
    assert (
        classify_roaming(
            3,
            min_rssi=-60,
            pre_rssi=-60,
            post_rssi=-60,
            pingpong_count=3,
            sticky_rssi_dbm=-75,
            slow_roam_degradation_db=10,
        )
        == CLS_PINGPONG
    )


def test_roaming_sticky_on_weak_min_rssi() -> None:
    assert (
        classify_roaming(
            1,
            min_rssi=-80,
            pre_rssi=-80,
            post_rssi=-60,
            pingpong_count=3,
            sticky_rssi_dbm=-75,
            slow_roam_degradation_db=10,
        )
        == CLS_STICKY
    )


def test_roaming_slow_roam_on_degradation() -> None:
    assert (
        classify_roaming(
            1,
            min_rssi=-60,
            pre_rssi=-55,
            post_rssi=-70,
            pingpong_count=3,
            sticky_rssi_dbm=-75,
            slow_roam_degradation_db=10,
        )
        == CLS_SLOW_ROAM
    )


def test_roaming_clean_roam_is_ok() -> None:
    assert (
        classify_roaming(
            1,
            min_rssi=-60,
            pre_rssi=-60,
            post_rssi=-58,
            pingpong_count=3,
            sticky_rssi_dbm=-75,
            slow_roam_degradation_db=10,
        )
        is None
    )


# --------------------------------------------------------------------------- #
# connect
# --------------------------------------------------------------------------- #
def test_connect_dhcp_wins_on_link_local() -> None:
    assert (
        classify_connect(link_local_ip=True, failure_classifier="auth", connected=True) == CLS_DHCP
    )


def test_connect_failure_event() -> None:
    assert (
        classify_connect(link_local_ip=False, failure_classifier="auth", connected=True) == "auth"
    )


def test_connect_clean_is_ok() -> None:
    assert classify_connect(link_local_ip=False, failure_classifier=None, connected=True) is None


# --------------------------------------------------------------------------- #
# wan
# --------------------------------------------------------------------------- #
_WAN_KW = dict(loss_threshold=1.0, latency_abs_ms=100.0, bufferbloat_ms=200.0)


def test_wan_down_when_unreachable() -> None:
    assert (
        classify_wan(
            reachable=False,
            loss=None,
            latency_ms=None,
            rtt_loaded_ms=None,
            rtt_idle_ms=None,
            **_WAN_KW,
        )
        == CLS_WAN_DOWN
    )


def test_wan_loss_beats_latency() -> None:
    assert (
        classify_wan(
            reachable=True, loss=5, latency_ms=500, rtt_loaded_ms=None, rtt_idle_ms=None, **_WAN_KW
        )
        == CLS_ISP_LOSS
    )


def test_wan_latency_absolute() -> None:
    assert (
        classify_wan(
            reachable=True, loss=0, latency_ms=150, rtt_loaded_ms=None, rtt_idle_ms=None, **_WAN_KW
        )
        == CLS_ISP_LATENCY
    )


def test_wan_bufferbloat_on_loaded_minus_idle() -> None:
    assert (
        classify_wan(
            reachable=True, loss=0, latency_ms=10, rtt_loaded_ms=260, rtt_idle_ms=20, **_WAN_KW
        )
        == CLS_BUFFERBLOAT
    )


def test_wan_ok_when_all_healthy() -> None:
    assert (
        classify_wan(
            reachable=True, loss=0, latency_ms=10, rtt_loaded_ms=30, rtt_idle_ms=20, **_WAN_KW
        )
        is None
    )


# --------------------------------------------------------------------------- #
# infra + helpers
# --------------------------------------------------------------------------- #
def test_infra_down_classifier_by_type() -> None:
    assert infra_down_classifier(EntityType.AP, restart_loop=False) == CLS_AP_DOWN
    assert infra_down_classifier(EntityType.SWITCH, restart_loop=False) == CLS_SW_DOWN
    assert infra_down_classifier(EntityType.GATEWAY, restart_loop=False) == CLS_GW_DOWN


def test_infra_restart_loop_overrides_type() -> None:
    assert infra_down_classifier(EntityType.AP, restart_loop=True) == CLS_RESTART_LOOP


def test_exceeds_baseline_none_band_is_false() -> None:
    assert exceeds_baseline(1000, None, 2.0) is False


def test_exceeds_baseline_true_above_two_sigma() -> None:
    assert exceeds_baseline(25, _Band(mean=10, std=5), 2.0) is True
    assert exceeds_baseline(19, _Band(mean=10, std=5), 2.0) is False


# --------------------------------------------------------------------------- #
# config overrides
# --------------------------------------------------------------------------- #
def test_config_defaults() -> None:
    cfg = SleConfig()
    assert cfg.coverage_weak_dbm == -72.0
    assert cfg.activity_metrics == ("rx_bytes", "tx_bytes")


def test_config_override_from_settings() -> None:
    settings = SimpleNamespace(
        thresholds={"sle": {"coverage_weak_dbm": -68, "activity_metrics": ["wifi_tx_attempts"]}}
    )
    cfg = SleConfig.from_settings(settings)
    assert cfg.coverage_weak_dbm == -68
    assert cfg.activity_metrics == ("wifi_tx_attempts",)
    # untouched fields keep their defaults
    assert cfg.sticky_rssi_dbm == -75.0


def test_config_ignores_unknown_and_missing_section() -> None:
    assert SleConfig.from_settings(SimpleNamespace(thresholds={})).coverage_weak_dbm == -72.0
    settings = SimpleNamespace(thresholds={"sle": {"bogus": 1}})
    assert SleConfig.from_settings(settings).coverage_weak_dbm == -72.0

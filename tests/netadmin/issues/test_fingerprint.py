"""Fingerprint = sha1(detector_key | site_id | native_id | sorted(dims)) — section 7."""

from __future__ import annotations

import hashlib

from netadmin.issues.engine import fingerprint


def _expected(detector_key: str, site_id: str, native_id: str, *dim_parts: str) -> str:
    raw = "|".join([detector_key, site_id, native_id, *dim_parts])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def test_fingerprint_matches_spec_formula(make_finding) -> None:
    finding = make_finding(
        "wired.bad_cable",
        native_id="aa:bb:cc:00:00:01",
        dims={"port": "5", "band": "5g"},
    )
    finding.entity.site_id = "default"
    # dims are appended in sorted-key order: band before port.
    assert fingerprint(finding) == _expected(
        "wired.bad_cable", "default", "aa:bb:cc:00:00:01", "band=5g", "port=5"
    )


def test_fingerprint_no_dims(make_finding) -> None:
    finding = make_finding("wan.dns_slow", native_id="gw-mac")
    assert fingerprint(finding) == _expected("wan.dns_slow", "default", "gw-mac")


def test_fingerprint_stable_across_dim_insertion_order(make_finding) -> None:
    a = make_finding("k", native_id="n", dims={"a": "1", "z": "2"})
    b = make_finding("k", native_id="n", dims={"z": "2", "a": "1"})
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_varies_with_each_component(make_finding) -> None:
    base = make_finding("k", native_id="n", dims={"a": "1"})
    base_fp = fingerprint(base)

    assert fingerprint(make_finding("k2", native_id="n", dims={"a": "1"})) != base_fp
    assert fingerprint(make_finding("k", native_id="n2", dims={"a": "1"})) != base_fp
    assert fingerprint(make_finding("k", native_id="n", dims={"a": "2"})) != base_fp

    other_site = make_finding("k", native_id="n", dims={"a": "1"})
    other_site.entity.site_id = "site-b"
    assert fingerprint(other_site) != base_fp

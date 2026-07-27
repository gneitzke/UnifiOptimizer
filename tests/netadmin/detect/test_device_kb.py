"""Locating the device-capability KB (:mod:`netadmin.detect.device_kb`).

The regression these guard: the KB used to be resolved relative to the *source
tree* (``parents[3]/data``), which is ``site-packages/data`` on a wheel install --
a directory that never exists -- so every pip install ran with no device knowledge
base at all, silently. The load path must therefore work with no repo checkout and
no particular working directory, and the baseline copy must live inside the
package so pyproject's package-data glob actually ships it.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path, PurePosixPath

import pytest

from netadmin import config
from netadmin.detect import device_kb

PACKAGE_ROOT = Path(config.__file__).resolve().parent
PYPROJECT = PACKAGE_ROOT.parent / "pyproject.toml"


# --------------------------------------------------------------------------- #
# the packaged baseline
# --------------------------------------------------------------------------- #
def test_packaged_kb_ships_inside_the_netadmin_package() -> None:
    """The baseline must sit under netadmin/ or package-data cannot ship it."""
    assert device_kb.PACKAGED_KB_PATH.is_file()
    assert device_kb.PACKAGED_KB_PATH.is_relative_to(PACKAGE_ROOT)
    assert device_kb.PACKAGED_KB_PATH.parent == PACKAGE_ROOT / "data"
    assert device_kb.PACKAGED_KB_PATH.suffix == ".json"


@pytest.mark.skipif(not PYPROJECT.is_file(), reason="source checkout only")
def test_package_data_glob_actually_covers_the_packaged_kb() -> None:
    """Putting the KB inside the package is only half the fix; it must be declared.

    The original bug was not a missing file -- the repo's ``data/`` directory was
    there all along. What never existed was the packaging declaration, so the
    wheel shipped without it. Asserting only the file's location leaves that half
    unguarded: drop ``data/*.json`` from package-data and every other test here
    still passes while the wheel silently loses the file again.
    """
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    globs = pyproject["tool"]["setuptools"]["package-data"]["netadmin"]
    relative = PurePosixPath(device_kb.PACKAGED_KB_PATH.relative_to(PACKAGE_ROOT).as_posix())
    assert any(
        relative.match(glob) for glob in globs
    ), f"{relative} matches none of {globs}; it will not ship in the wheel"


def test_packaged_kb_is_a_usable_capability_database() -> None:
    """A shipped-but-malformed KB would be as useless as a missing one."""
    kb = device_kb.load_kb(device_kb.PACKAGED_KB_PATH)
    assert kb is not None
    # The sections the two detector call sites actually read.
    assert isinstance(kb.get("known_2.4ghz_only", {}).get("patterns"), list)
    assert kb["known_2.4ghz_only"]["patterns"], "2.4GHz-only list drives both detectors"


# --------------------------------------------------------------------------- #
# resolution order
# --------------------------------------------------------------------------- #
def test_default_path_falls_back_to_packaged_copy_when_data_dir_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    assert device_kb.default_kb_path() == device_kb.PACKAGED_KB_PATH


def test_default_path_prefers_the_operators_copy_in_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/DEVICE_DATABASE.md promises an editable copy; it must win."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    operator_copy = data_dir / device_kb.KB_FILENAME
    operator_copy.write_text(json.dumps({"wifi7_devices": {"patterns": ["my device"]}}))
    monkeypatch.setattr(config, "DATA_DIR", data_dir)

    assert device_kb.default_kb_path() == operator_copy
    assert device_kb.load_kb()["wifi7_devices"]["patterns"] == ["my device"]


def test_resolution_survives_a_data_dir_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wheel-install regression: no checkout, no ./data, still a real KB.

    ``DATA_DIR`` here points at a directory that was never created, which is what
    a daemon started outside its data dir sees. The old resolution returned
    ``site-packages/data/...`` and simply did not exist; this must still land on a
    readable file.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "definitely-absent")

    resolved = device_kb.default_kb_path()
    assert resolved == device_kb.PACKAGED_KB_PATH
    assert resolved.is_file(), "a wheel install must still find a KB"
    assert device_kb.load_kb() is not None


def test_a_directory_at_the_operator_path_is_not_mistaken_for_a_kb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``is_file()`` guards the tier-2 probe, so a stray directory falls through."""
    data_dir = tmp_path / "data"
    (data_dir / device_kb.KB_FILENAME).mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)

    assert device_kb.default_kb_path() == device_kb.PACKAGED_KB_PATH


# --------------------------------------------------------------------------- #
# load_kb failure modes -- None, never a silently-empty dict
# --------------------------------------------------------------------------- #
def test_load_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    assert device_kb.load_kb(tmp_path / "absent.json") is None


def test_load_returns_none_for_unparseable_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert device_kb.load_kb(bad) is None


@pytest.mark.parametrize("payload", ["[1, 2]", '"a string"', "null"])
def test_load_returns_none_when_the_json_is_not_an_object(tmp_path: Path, payload: str) -> None:
    """A JSON array parses fine but has no sections; that is 'no KB', not 'empty KB'."""
    path = tmp_path / "wrong_shape.json"
    path.write_text(payload)
    assert device_kb.load_kb(path) is None


def test_load_distinguishes_no_kb_from_an_empty_one(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    assert device_kb.load_kb(empty) == {}  # a real, empty KB
    assert device_kb.load_kb(tmp_path / "gone.json") is None  # no KB at all


# --------------------------------------------------------------------------- #
# section_patterns -- the KB is hand-edited, so section shape is untrusted
# --------------------------------------------------------------------------- #
def test_section_patterns_reads_the_expected_shape() -> None:
    kb = {"known_2.4ghz_only": {"patterns": ["ESP32", " Tuya "]}}
    assert device_kb.section_patterns(kb, "known_2.4ghz_only") == ("esp32", "tuya")


def test_section_patterns_survives_a_bare_list_section() -> None:
    """The trap that took the detector offline: ``list.get`` raises AttributeError.

    The engine isolates a raising detector and skips it, so this typo used to cost
    the whole of client.known_pathology for the pass.
    """
    kb = {"known_2.4ghz_only": ["esp32", "tuya"]}
    assert device_kb.section_patterns(kb, "known_2.4ghz_only") == ()


def test_section_patterns_rejects_a_bare_string_patterns_value() -> None:
    """The worse trap: a string is iterable, so 'e' would match nearly every name."""
    kb = {"known_2.4ghz_only": {"patterns": "esp32"}}
    assert device_kb.section_patterns(kb, "known_2.4ghz_only") == ()


@pytest.mark.parametrize("kb", [None, {}, {"other_section": {"patterns": ["x"]}}])
def test_section_patterns_returns_empty_for_absent_sections(kb) -> None:
    assert device_kb.section_patterns(kb, "known_2.4ghz_only") == ()


def test_section_patterns_drops_blank_entries_and_de_dupes() -> None:
    kb = {"known_2.4ghz_only": {"patterns": ["esp32", "", "  ", "ESP32", "tuya"]}}
    assert device_kb.section_patterns(kb, "known_2.4ghz_only") == ("esp32", "tuya")

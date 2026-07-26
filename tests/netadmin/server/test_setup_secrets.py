"""Unit tests for the ``secrets.env`` writer (netadmin.config.write_secrets).

First-run setup persists the controller credential + the minted UI token through
this helper (ARCHITECTURE.md 18). These pin the hard contract: the file is created
0600, other keys / comments / ordering survive, an existing key is updated in
place, a new key is appended, and awkward values (spaces, ``#``, quotes) round-trip
through a dotenv reader. All offline, all in pytest's tmp dir -- never the real
data/secrets.env.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from netadmin.config import SecretValueError, Settings, write_secrets


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_write_creates_file_chmod_600(tmp_path: Path) -> None:
    p = tmp_path / "secrets.env"
    assert not p.exists()
    write_secrets({"UNIFI_HOST": "https://unifi.test", "UNIFI_API_KEY": "abc123"}, path=p)
    assert p.exists()
    assert _mode(p) == 0o600
    text = p.read_text(encoding="utf-8")
    assert "UNIFI_HOST=https://unifi.test" in text
    assert "UNIFI_API_KEY=abc123" in text


def test_creates_parent_dir(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "dir" / "secrets.env"
    write_secrets({"NETADMIN_API_TOKEN": "tok"}, path=p)
    assert p.exists()
    assert _mode(p) == 0o600


def test_preserves_other_keys_comments_and_order(tmp_path: Path) -> None:
    p = tmp_path / "secrets.env"
    p.write_text(
        "# my secrets\n"
        "HA_MQTT_HOST=10.0.0.5\n"
        "\n"
        "UNIFI_HOST=https://old.host\n"
        "# trailing note\n",
        encoding="utf-8",
    )
    write_secrets({"UNIFI_HOST": "https://new.host", "UNIFI_API_KEY": "key"}, path=p)
    lines = p.read_text(encoding="utf-8").splitlines()
    # Comment + unrelated key + ordering preserved; UNIFI_HOST updated in place.
    assert lines[0] == "# my secrets"
    assert "HA_MQTT_HOST=10.0.0.5" in lines
    assert "# trailing note" in lines
    assert "UNIFI_HOST=https://new.host" in lines
    assert "UNIFI_HOST=https://old.host" not in lines
    # New key appended after the existing content.
    assert "UNIFI_API_KEY=key" in lines
    # Exactly one UNIFI_HOST line (updated in place, not duplicated).
    assert sum(1 for line in lines if line.startswith("UNIFI_HOST=")) == 1


def test_reasserts_600_on_existing_file(tmp_path: Path) -> None:
    p = tmp_path / "secrets.env"
    p.write_text("UNIFI_HOST=https://h\n", encoding="utf-8")
    p.chmod(0o644)
    write_secrets({"UNIFI_API_KEY": "k"}, path=p)
    assert _mode(p) == 0o600


def test_special_char_value_roundtrips_through_dotenv(tmp_path: Path, tmp_db_path: Path) -> None:
    # A password with a space, a comment marker, and a quote must survive.
    password = 'p a#ss"word\\x'
    p = tmp_path / "secrets.env"
    write_secrets(
        {
            "UNIFI_HOST": "https://unifi.test",
            "UNIFI_USERNAME": "admin",
            "UNIFI_PASSWORD": password,
        },
        path=p,
    )
    loaded = Settings(_env_file=str(p), db_path=tmp_db_path)
    assert loaded.unifi_host == "https://unifi.test"
    assert loaded.unifi_username == "admin"
    assert loaded.unifi_password == password


def test_newline_value_cannot_inject_a_second_key(tmp_path: Path) -> None:
    # The core injection invariant: a value carrying a newline must NEVER be written
    # as a literal line break that a dotenv reader parses back into a second
    # KEY=VALUE assignment. The writer rejects it outright (SecretValueError) at the
    # security boundary rather than emitting a splittable line.
    p = tmp_path / "secrets.env"
    injected = "abc\nNETADMIN_API_TOKEN=attacker"
    with pytest.raises(SecretValueError):
        write_secrets({"UNIFI_API_KEY": injected}, path=p)
    # And nothing was written: no file, so no smuggled second key.
    assert not p.exists()


@pytest.mark.parametrize(
    "bad",
    [
        "abc\nHA_MQTT_HOST=evil",  # newline
        "abc\rHA_MQTT_HOST=evil",  # carriage return
        "abc\x00tail",  # NUL truncation
    ],
)
def test_control_chars_are_rejected(tmp_path: Path, bad: str) -> None:
    p = tmp_path / "secrets.env"
    with pytest.raises(SecretValueError):
        write_secrets({"UNIFI_PASSWORD": bad}, path=p)
    assert not p.exists()


def test_injection_rejected_before_touching_an_existing_file(tmp_path: Path) -> None:
    # A forbidden value in one of several updates must not partially rewrite an
    # existing secrets.env: the whole write is refused and the file is untouched.
    p = tmp_path / "secrets.env"
    p.write_text("UNIFI_HOST=https://old.host\n", encoding="utf-8")
    with pytest.raises(SecretValueError):
        write_secrets({"UNIFI_HOST": "https://new.host", "UNIFI_API_KEY": "k\nEVIL=1"}, path=p)
    # Original content is intact -- no partial update, no injected key.
    text = p.read_text(encoding="utf-8")
    assert text == "UNIFI_HOST=https://old.host\n"
    assert "EVIL" not in text
    assert "new.host" not in text


def test_plain_values_written_bare(tmp_path: Path) -> None:
    # URL-safe values (host, api key, token) stay unquoted so a hand-edited file
    # keeps its style.
    p = tmp_path / "secrets.env"
    write_secrets({"UNIFI_HOST": "https://unifi.test", "NETADMIN_API_TOKEN": "AbC-_123xyz"}, path=p)
    text = p.read_text(encoding="utf-8")
    assert "UNIFI_HOST=https://unifi.test\n" in text
    assert "NETADMIN_API_TOKEN=AbC-_123xyz\n" in text
    assert '"' not in text

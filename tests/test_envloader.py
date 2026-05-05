"""Project-local .env loader tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from multidraw import _envloader


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_load_env_reads_basic_kv(isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated_cwd / ".env").write_text("FOO=bar\nBAZ=\"quoted value\"\n", encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    _envloader.load_env()
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "quoted value"


def test_load_env_respects_existing_environ(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-set env vars must NOT be overwritten by .env (default behavior)."""
    (isolated_cwd / ".env").write_text("FOO=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "fromshell")
    _envloader.load_env()
    assert os.environ["FOO"] == "fromshell"


def test_load_env_override_flag(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_cwd / ".env").write_text("FOO=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "fromshell")
    _envloader.load_env(override=True)
    assert os.environ["FOO"] == "fromfile"


def test_load_env_handles_comments_and_blanks(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_cwd / ".env").write_text(
        "# leading comment\n\nFOO=ok\n   # indented comment\nBAR=also-ok\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    _envloader.load_env()
    assert os.environ["FOO"] == "ok"
    assert os.environ["BAR"] == "also-ok"


def test_load_env_export_prefix_supported(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_cwd / ".env").write_text("export FOO=bar\n", encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    _envloader.load_env()
    assert os.environ["FOO"] == "bar"


def test_load_env_walks_up_to_home(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_cwd / ".env").write_text("FOO=ancestor\n", encoding="utf-8")
    sub = isolated_cwd / "deep" / "nested"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    monkeypatch.delenv("FOO", raising=False)
    _envloader.load_env()
    assert os.environ["FOO"] == "ancestor"


def test_load_env_no_dotenv_does_not_crash(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MULTIDRAW_TESTONLY", raising=False)
    result = _envloader.load_env()
    assert result is None  # no .env in cwd or any ancestor up to fake home


def test_load_env_prepends_npm_global_bin_when_pi_present(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npm_bin = isolated_cwd / ".npm-global" / "bin"
    npm_bin.mkdir(parents=True)
    (npm_bin / "pi").write_text("#!/bin/sh\n")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    _envloader.load_env()
    assert str(npm_bin) in os.environ["PATH"].split(os.pathsep)
    # ordering: npm-global must come BEFORE /usr/bin
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts.index(str(npm_bin)) < parts.index("/usr/bin")


def test_load_env_skips_npm_path_when_pi_absent(
    isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    _envloader.load_env()
    npm_bin = str(isolated_cwd / ".npm-global" / "bin")
    assert npm_bin not in os.environ["PATH"].split(os.pathsep)


def test_load_env_extra_path_var(isolated_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("MULTIDRAW_EXTRA_PATH", "/opt/custom/bin")
    _envloader.load_env()
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == "/opt/custom/bin"

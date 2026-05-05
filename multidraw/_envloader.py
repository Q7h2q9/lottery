"""Project-local environment loader.

We deliberately avoid telling users to ``export FOO=...`` in their shell
startup files — they run multiple Claude Code instances side-by-side and a
key in ``~/.zshrc`` leaks across all of them. Instead, multidraw reads a
gitignored ``.env`` file in the current working directory at startup.

Rules:

- ``.env`` lives in CWD (or any ancestor up to the user's home — handy when
  invoking multidraw from a subdirectory).
- Existing ``os.environ`` values win — anything already set takes precedence
  over ``.env``. This keeps explicit shell overrides working.
- Only this process and its children (agentflow, pi subprocesses) see the
  variables.

We also auto-prepend ``~/.npm-global/bin`` to ``PATH`` when the ``pi`` CLI
lives there, so the npm user-prefix install path works without needing the
user to fix their shell PATH.
"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def _find_env_file(start: Path) -> Path | None:
    home = Path.home().resolve()
    cur = start.resolve()
    while True:
        candidate = cur / ".env"
        if candidate.is_file():
            return candidate
        if cur == home or cur.parent == cur:
            return None
        cur = cur.parent


def _parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _maybe_prepend_pi_path() -> None:
    npm_bin = Path.home() / ".npm-global" / "bin"
    if not (npm_bin / "pi").exists():
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if str(npm_bin) in parts:
        return
    os.environ["PATH"] = os.pathsep.join([str(npm_bin), *parts]) if parts else str(npm_bin)


def load_env(*, override: bool = False) -> Path | None:
    """Load the nearest ``.env`` file (cwd → ancestors). Idempotent.

    Returns the path that was loaded, or ``None`` if no ``.env`` was found.
    """
    global _LOADED
    env_path = _find_env_file(Path.cwd())
    if env_path is not None:
        try:
            entries = _parse_dotenv(env_path.read_text(encoding="utf-8"))
        except OSError:
            entries = {}
        for key, value in entries.items():
            if override or key not in os.environ:
                os.environ[key] = value

    extra = os.environ.get("MULTIDRAW_EXTRA_PATH", "").strip()
    if extra:
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        if extra not in parts:
            os.environ["PATH"] = os.pathsep.join([extra, *parts]) if parts else extra

    _maybe_prepend_pi_path()
    _LOADED = True
    return env_path


def already_loaded() -> bool:
    return _LOADED

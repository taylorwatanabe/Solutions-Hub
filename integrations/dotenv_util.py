"""Load optional `.env` for local development (never commit secrets)."""

from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        _log.warning(
            "python-dotenv is not installed; .env files are ignored. "
            "Run: pip install -r requirements.txt"
        )
        return

    integrations_dir = Path(__file__).resolve().parent
    project_root = integrations_dir.parent
    candidates = [project_root / ".env", Path.cwd() / ".env"]
    seen: set[Path] = set()
    for raw in candidates:
        try:
            p = raw.resolve()
        except OSError:
            continue
        if not p.is_file() or p in seen:
            continue
        seen.add(p)
        load_dotenv(p, override=True)
        _log.info("Loaded environment file: %s", p)

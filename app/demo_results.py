"""
Loader for pre-run saved results stored in data/demo_results/.

File naming conventions (written by scripts/prerun.py):
  Investigation: data/demo_results/investigate_{persona_id}_{url_slug}.json
  Discovery:     data/demo_results/discover_{query_slug}.json

Each file is a JSON object:
  {
    "generated_at": "<ISO 8601>",
    "result": <InvestigationReport | list[CandidateIssue]>
  }

from_cache=True is set on the JobStatus whenever a file is found and served.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEMO_DIR = Path(__file__).parent.parent / "data" / "demo_results"


def _slugify(text: str) -> str:
    """Lowercase, replace non-alphanumeric runs with underscores, strip edges."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _investigate_path(issue_url: str, persona_id: str | None) -> Path:
    url_slug = _slugify(issue_url)
    if persona_id:
        return _DEMO_DIR / f"investigate_{_slugify(persona_id)}_{url_slug}.json"
    return _DEMO_DIR / f"investigate_{url_slug}.json"


def _discover_path(query: str | None) -> Path:
    query_slug = _slugify(query) if query else "profile_based"
    return _DEMO_DIR / f"discover_{query_slug}.json"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        logger.debug("demo_results: no file at %s", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "result" not in data or "generated_at" not in data:
            logger.warning("demo_results: malformed file %s (missing result/generated_at)", path)
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo_results: failed to load %s: %s", path, exc)
        return None


def load_investigate_result(issue_url: str, persona_id: str | None) -> dict[str, Any] | None:
    """Return {result, generated_at} from a saved file, or None if not found."""
    return _load(_investigate_path(issue_url, persona_id))


def load_discover_result(query: str | None) -> dict[str, Any] | None:
    """Return {result, generated_at} from a saved file, or None if not found."""
    return _load(_discover_path(query))

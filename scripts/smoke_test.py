#!/usr/bin/env python3
"""CI / local smoke checks against the Flask app (local JSON store)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force local store before importing app integrations that read env at call time.
os.environ.setdefault("ALLOW_LOCAL_DATA_STORAGE", "1")
if not (os.getenv("SOLUTIONS_HUB_LOCAL_DATA") or "").strip():
    tmp = Path(tempfile.mkdtemp(prefix="solutions-hub-")) / "submissions.json"
    example = ROOT / "data" / "submissions.example.json"
    tmp.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    os.environ["SOLUTIONS_HUB_LOCAL_DATA"] = str(tmp)
# Ensure Sheets is not required
os.environ.pop("SOLUTIONS_HUB_SHEET_ID", None)


def main() -> int:
    from app import app

    client = app.test_client()

    r = client.get("/healthz")
    assert r.status_code == 200, r.data
    assert r.get_json().get("status") == "ok"

    r = client.get("/api/build-info")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body.get("service") == "Solution Hub"
    assert isinstance(body.get("statuses"), list) and len(body["statuses"]) == 5

    r = client.get("/")
    assert r.status_code == 200, r.data
    assert b"Solutions Hub" in r.data

    r = client.get("/api/board")
    assert r.status_code == 200, r.data
    board = r.get_json()
    assert board.get("total", 0) >= 1
    assert "Received" in (board.get("columns") or {})

    payload = {
        "problem": "CI smoke problem",
        "option_a": "A",
        "option_b": "B",
        "option_c": "C",
        "recommendation": "A",
        "submitter_name": "CI Bot",
        "submitter_email": "ci@example.com",
        "department": "Engineering",
    }
    r = client.post("/api/submissions", json=payload)
    assert r.status_code == 201, r.data
    sub = (r.get_json() or {}).get("submission") or {}
    sid = sub.get("id")
    assert sid
    assert sub.get("status") == "Received"

    r = client.patch(
        f"/api/submissions/{sid}",
        json={"status": "In Review", "department": "Engineering"},
    )
    assert r.status_code == 200, r.data
    assert (r.get_json() or {}).get("submission", {}).get("status") == "In Review"

    r = client.post(f"/api/submissions/{sid}/upvote", json={})
    assert r.status_code == 200, r.data
    assert (r.get_json() or {}).get("submission", {}).get("upvotes", 0) >= 1

    print("smoke_test: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"smoke_test: FAIL — {e}", file=sys.stderr)
        raise SystemExit(1)

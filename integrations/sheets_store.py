"""Google Sheets store for Solutions Hub submissions (Kanban source of truth)."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATUSES = [
    "Received",
    "In Review",
    "In Progress / Pilots",
    "Implemented / Wins",
    "NA / Archived",
]

HEADERS = [
    "id",
    "submitted_at",
    "submitter_name",
    "submitter_email",
    "department",
    "problem",
    "option_a",
    "option_b",
    "option_c",
    "recommendation",
    "resources",
    "estimated_value",
    "status",
    "upvotes",
    "score_total",
    "sprint_lead",
    "coaching_notes",
    "roi_tier",
]

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

_lock = threading.Lock()


def _strip(value: Optional[str]) -> str:
    return (value or "").strip()


def _sheet_id() -> str:
    return _strip(os.getenv("SOLUTIONS_HUB_SHEET_ID"))


def _sheet_tab() -> str:
    return _strip(os.getenv("SOLUTIONS_HUB_SHEET_TAB")) or "Submissions"


def _allow_local() -> bool:
    return _strip(os.getenv("ALLOW_LOCAL_DATA_STORAGE")) in ("1", "true", "True", "yes", "YES")


def _local_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    custom = _strip(os.getenv("SOLUTIONS_HUB_LOCAL_DATA"))
    if custom:
        return Path(custom).expanduser()
    return root / "data" / "submissions.json"


def _use_local_store() -> bool:
    if _allow_local() and not _sheet_id():
        return True
    storage = _strip(os.getenv("DATA_STORAGE")).lower()
    return storage == "local" and _allow_local()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_submission(row: List[str], headers: List[str]) -> Dict[str, Any]:
    data = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
    try:
        data["upvotes"] = int(str(data.get("upvotes") or "0").strip() or "0")
    except ValueError:
        data["upvotes"] = 0
    return data


def _submission_to_row(sub: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in HEADERS:
        val = sub.get(key, "")
        if key == "upvotes":
            out.append(str(int(val or 0)))
        else:
            out.append("" if val is None else str(val))
    return out


def _ensure_gcp_credentials() -> None:
    from integrations.aws_gcp_sa_bootstrap import load_gcp_service_account_from_aws_secrets_manager

    load_gcp_service_account_from_aws_secrets_manager()


def _service_account_info() -> Dict[str, Any]:
    _ensure_gcp_credentials()
    raw = _strip(os.getenv("GCP_SERVICE_ACCOUNT_JSON"))
    if raw:
        if raw.startswith("arn:aws:secretsmanager"):
            raise RuntimeError(
                "GCP_SERVICE_ACCOUNT_JSON looks like an AWS ARN. "
                "Use ECS value type 'Secret' on AWS_GCP_SERVICE_ACCOUNT_SECRET_ID "
                "so the container receives the GCP service-account JSON text."
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"GCP_SERVICE_ACCOUNT_JSON is not valid JSON: {e}") from e
        if not isinstance(parsed, dict) or parsed.get("type") != "service_account":
            raise RuntimeError("GCP_SERVICE_ACCOUNT_JSON must be a service_account key object")
        return parsed

    key_file = _strip(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    if key_file:
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise RuntimeError(f"GOOGLE_APPLICATION_CREDENTIALS path not found: {key_file}")
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or parsed.get("type") != "service_account":
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must be a service_account key file")
        return parsed

    raise RuntimeError(
        "No GCP credentials configured. Set GCP_SERVICE_ACCOUNT_JSON, "
        "AWS_GCP_SERVICE_ACCOUNT_SECRET_ID (same secret as QC/CAT-2), "
        "or GOOGLE_APPLICATION_CREDENTIALS. For local demo without Sheets, "
        "set ALLOW_LOCAL_DATA_STORAGE=1 and omit SOLUTIONS_HUB_SHEET_ID."
    )


def _sheets_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = _service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=[SHEETS_SCOPE]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _a1_range(tab: str, end_col: str = "R") -> str:
    # Escape tab name for A1 notation
    safe = tab.replace("'", "''")
    return f"'{safe}'!A:{end_col}"


def _read_all_sheets() -> List[Dict[str, Any]]:
    sheet_id = _sheet_id()
    if not sheet_id:
        raise RuntimeError("SOLUTIONS_HUB_SHEET_ID is not set")
    svc = _sheets_service()
    tab = _sheet_tab()
    result = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=_a1_range(tab))
        .execute()
    )
    values = result.get("values") or []
    if not values:
        return []
    headers = [str(h).strip() for h in values[0]]
    # Normalize to known headers; keep extras if present
    if not headers or headers[0].lower() != "id":
        # Assume missing header row — treat first row as data with HEADERS
        logger.warning("Sheet missing header row; assuming canonical HEADERS order")
        headers = list(HEADERS)
        rows = values
    else:
        rows = values[1:]
    return [_row_to_submission(row, headers) for row in rows if any(str(c).strip() for c in row)]


def _write_all_sheets(subs: List[Dict[str, Any]]) -> None:
    sheet_id = _sheet_id()
    if not sheet_id:
        raise RuntimeError("SOLUTIONS_HUB_SHEET_ID is not set")
    svc = _sheets_service()
    tab = _sheet_tab()
    body = {"values": [HEADERS] + [_submission_to_row(s) for s in subs]}
    svc.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=_a1_range(tab)
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab.replace(chr(39), chr(39)+chr(39))}'!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()


def _append_sheet_row(sub: Dict[str, Any]) -> None:
    sheet_id = _sheet_id()
    if not sheet_id:
        raise RuntimeError("SOLUTIONS_HUB_SHEET_ID is not set")
    svc = _sheets_service()
    tab = _sheet_tab()
    # Ensure header exists
    existing = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=_a1_range(tab, "A"))
        .execute()
        .get("values")
        or []
    )
    if not existing:
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab.replace(chr(39), chr(39)+chr(39))}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
    svc.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=_a1_range(tab),
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [_submission_to_row(sub)]},
    ).execute()


def _read_all_local() -> List[Dict[str, Any]]:
    path = _local_path()
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        merged = {k: item.get(k, "") for k in HEADERS}
        try:
            merged["upvotes"] = int(merged.get("upvotes") or 0)
        except (TypeError, ValueError):
            merged["upvotes"] = 0
        out.append(merged)
    return out


def _write_all_local(subs: List[Dict[str, Any]]) -> None:
    path = _local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(subs, indent=2), encoding="utf-8")


def list_submissions(department: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        if _use_local_store():
            rows = _read_all_local()
        else:
            rows = _read_all_sheets()
    dept = _strip(department)
    if dept:
        rows = [r for r in rows if _strip(r.get("department")).lower() == dept.lower()]
    return rows


def get_board(department: Optional[str] = None) -> Dict[str, Any]:
    rows = list_submissions(department=department)
    columns: Dict[str, List[Dict[str, Any]]] = {s: [] for s in STATUSES}
    unknown: List[Dict[str, Any]] = []
    for row in rows:
        status = _strip(row.get("status")) or "Received"
        if status not in columns:
            # Soft-map common aliases
            lowered = status.lower()
            mapped = None
            for s in STATUSES:
                if s.lower() == lowered:
                    mapped = s
                    break
            if mapped:
                status = mapped
                row = {**row, "status": status}
            else:
                unknown.append(row)
                continue
        columns[status].append(row)

    by_dept: Dict[str, int] = {}
    for row in rows:
        d = _strip(row.get("department")) or "Unspecified"
        by_dept[d] = by_dept.get(d, 0) + 1

    return {
        "statuses": STATUSES,
        "columns": columns,
        "counts": {s: len(columns[s]) for s in STATUSES},
        "department_counts": dict(sorted(by_dept.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unknown": unknown,
        "total": len(rows),
    }


def create_submission(payload: Dict[str, Any]) -> Dict[str, Any]:
    required = ("problem", "option_a", "option_b", "option_c", "recommendation")
    missing = [k for k in required if not _strip(str(payload.get(k, "")))]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    sub: Dict[str, Any] = {
        "id": _strip(str(payload.get("id") or "")) or str(uuid.uuid4()),
        "submitted_at": _strip(str(payload.get("submitted_at") or "")) or _utcnow_iso(),
        "submitter_name": _strip(str(payload.get("submitter_name") or "")),
        "submitter_email": _strip(str(payload.get("submitter_email") or "")),
        "department": _strip(str(payload.get("department") or "")),
        "problem": _strip(str(payload.get("problem") or "")),
        "option_a": _strip(str(payload.get("option_a") or "")),
        "option_b": _strip(str(payload.get("option_b") or "")),
        "option_c": _strip(str(payload.get("option_c") or "")),
        "recommendation": _strip(str(payload.get("recommendation") or "")),
        "resources": _strip(str(payload.get("resources") or "")),
        "estimated_value": _strip(str(payload.get("estimated_value") or "")),
        "status": "Received",
        "upvotes": 0,
        "score_total": "",
        "sprint_lead": "",
        "coaching_notes": "",
        "roi_tier": "",
    }

    with _lock:
        if _use_local_store():
            rows = _read_all_local()
            rows.append(sub)
            _write_all_local(rows)
        else:
            _append_sheet_row(sub)
    return sub


def update_submission(submission_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    sid = _strip(submission_id)
    if not sid:
        raise ValueError("submission id is required")

    allowed = {
        "status",
        "department",
        "coaching_notes",
        "sprint_lead",
        "score_total",
        "roi_tier",
        "submitter_name",
        "submitter_email",
        "problem",
        "option_a",
        "option_b",
        "option_c",
        "recommendation",
        "resources",
        "estimated_value",
    }

    with _lock:
        if _use_local_store():
            rows = _read_all_local()
        else:
            rows = _read_all_sheets()

        idx = next((i for i, r in enumerate(rows) if _strip(r.get("id")) == sid), None)
        if idx is None:
            raise KeyError(f"Submission not found: {sid}")

        current = dict(rows[idx])
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key == "status":
                status = _strip(str(value))
                if status not in STATUSES:
                    raise ValueError(f"Invalid status: {status}")
                current["status"] = status
            else:
                current[key] = _strip(str(value)) if value is not None else ""

        rows[idx] = current
        if _use_local_store():
            _write_all_local(rows)
        else:
            _write_all_sheets(rows)
        return current


def upvote_submission(submission_id: str) -> Dict[str, Any]:
    sid = _strip(submission_id)
    if not sid:
        raise ValueError("submission id is required")

    with _lock:
        if _use_local_store():
            rows = _read_all_local()
        else:
            rows = _read_all_sheets()

        idx = next((i for i, r in enumerate(rows) if _strip(r.get("id")) == sid), None)
        if idx is None:
            raise KeyError(f"Submission not found: {sid}")

        current = dict(rows[idx])
        try:
            current["upvotes"] = int(current.get("upvotes") or 0) + 1
        except (TypeError, ValueError):
            current["upvotes"] = 1
        rows[idx] = current

        if _use_local_store():
            _write_all_local(rows)
        else:
            _write_all_sheets(rows)
        return current


def replace_all_submissions(subs: List[Dict[str, Any]]) -> int:
    """Ops helper for CSV seed — overwrites sheet/local store."""
    normalized: List[Dict[str, Any]] = []
    for item in subs:
        if not isinstance(item, dict):
            continue
        row = {k: item.get(k, "") for k in HEADERS}
        if not _strip(str(row.get("id") or "")):
            row["id"] = str(uuid.uuid4())
        if not _strip(str(row.get("status") or "")):
            row["status"] = "Received"
        try:
            row["upvotes"] = int(row.get("upvotes") or 0)
        except (TypeError, ValueError):
            row["upvotes"] = 0
        if not _strip(str(row.get("submitted_at") or "")):
            row["submitted_at"] = _utcnow_iso()
        normalized.append(row)

    with _lock:
        if _use_local_store():
            _write_all_local(normalized)
        else:
            _write_all_sheets(normalized)
    return len(normalized)

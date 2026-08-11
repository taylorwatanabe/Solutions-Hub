"""Google Sheets store for Solutions Hub submissions (Kanban source of truth)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# Default: Friction-to-Solutions replies sheet, columns AI–AK from row 2.
DEFAULT_SHEET_ID = "1sR1CTeznF4-8Az3WhGwgaAI4eMD6JEfAUhzgVpEvRSs"
DEFAULT_SHEET_GID = "439459761"
DEFAULT_DATA_RANGE = "AI2:AK"
# AI=status, AJ=department, AK=problem (override with SOLUTIONS_HUB_COLUMN_FIELDS)
DEFAULT_COLUMN_FIELDS = "status,department,problem"

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

_lock = threading.Lock()
_tab_cache: Dict[str, str] = {}


def _strip(value: Optional[str]) -> str:
    return (value or "").strip()


def _sheet_id() -> str:
    return _strip(os.getenv("SOLUTIONS_HUB_SHEET_ID")) or DEFAULT_SHEET_ID


def _sheet_gid() -> str:
    return _strip(os.getenv("SOLUTIONS_HUB_SHEET_GID")) or DEFAULT_SHEET_GID


def _sheet_tab_env() -> str:
    return _strip(os.getenv("SOLUTIONS_HUB_SHEET_TAB"))


def _data_range() -> str:
    """A1 range without sheet name, e.g. AI2:AK."""
    return _strip(os.getenv("SOLUTIONS_HUB_DATA_RANGE")) or DEFAULT_DATA_RANGE


def _column_fields() -> List[str]:
    raw = _strip(os.getenv("SOLUTIONS_HUB_COLUMN_FIELDS")) or DEFAULT_COLUMN_FIELDS
    fields = [f.strip().lower() for f in raw.split(",") if f.strip()]
    return fields or ["status", "department", "problem"]


def _compact_mode() -> bool:
    """True when reading a fixed column slice (e.g. AI2:AK) instead of full sheet."""
    return bool(_data_range())


def _allow_local() -> bool:
    return _strip(os.getenv("ALLOW_LOCAL_DATA_STORAGE")) in ("1", "true", "True", "yes", "YES")


def _local_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    custom = _strip(os.getenv("SOLUTIONS_HUB_LOCAL_DATA"))
    if custom:
        return Path(custom).expanduser()
    return root / "data" / "submissions.json"


def _use_local_store() -> bool:
    if _strip(os.getenv("SOLUTIONS_HUB_FORCE_SHEETS")) in ("1", "true", "True", "yes"):
        return False
    storage = _strip(os.getenv("DATA_STORAGE")).lower()
    if storage == "local" and _allow_local():
        return True
    has_creds = bool(
        _strip(os.getenv("GCP_SERVICE_ACCOUNT_JSON"))
        or _strip(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        or _strip(os.getenv("AWS_GCP_SERVICE_ACCOUNT_SECRET_ID"))
    )
    if not has_creds and _allow_local():
        return True
    return False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _col_letters_to_index(col: str) -> int:
    col = col.upper()
    n = 0
    for ch in col:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Invalid column letters: {col}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _index_to_col_letters(index: int) -> str:
    """1-based column index → letters."""
    letters = []
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord("A") + rem))
    return "".join(reversed(letters))


def _parse_data_range(a1: str) -> Tuple[str, int, str, Optional[int]]:
    """
    Parse 'AI2:AK' or 'AI2:AK500' → (start_col, start_row, end_col, end_row|None).
    """
    m = re.fullmatch(r"([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)?", a1.replace(" ", ""))
    if not m:
        raise ValueError(
            f"SOLUTIONS_HUB_DATA_RANGE must look like AI2:AK (got {a1!r})"
        )
    start_col, start_row, end_col, end_row = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    return start_col.upper(), start_row, end_col.upper(), int(end_row) if end_row else None


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
        "set ALLOW_LOCAL_DATA_STORAGE=1 and omit GCP credentials."
    )


def _sheets_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = _service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=[SHEETS_SCOPE]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _escape_tab(tab: str) -> str:
    return tab.replace("'", "''")


def _resolve_tab_title(svc, spreadsheet_id: str) -> str:
    env_tab = _sheet_tab_env()
    if env_tab:
        return env_tab

    gid = _sheet_gid()
    cache_key = f"{spreadsheet_id}:{gid}"
    if cache_key in _tab_cache:
        return _tab_cache[cache_key]

    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = meta.get("sheets") or []
    if gid:
        for sh in sheets:
            props = sh.get("properties") or {}
            if str(props.get("sheetId")) == str(gid):
                title = str(props.get("title") or "Sheet1")
                _tab_cache[cache_key] = title
                logger.info("Resolved sheet gid %s → tab %r", gid, title)
                return title
        logger.warning("Sheet gid %s not found; falling back to first tab", gid)

    if sheets:
        title = str((sheets[0].get("properties") or {}).get("title") or "Sheet1")
        _tab_cache[cache_key] = title
        return title
    return "Sheet1"


def _normalize_status(raw: str) -> str:
    status = _strip(raw)
    if not status:
        return "Received"
    if status in STATUSES:
        return status
    lowered = status.lower()
    for s in STATUSES:
        if s.lower() == lowered:
            return s
    aliases = {
        "received": "Received",
        "new": "Received",
        "submitted": "Received",
        "in review": "In Review",
        "review": "In Review",
        "in progress": "In Progress / Pilots",
        "in progress / pilots": "In Progress / Pilots",
        "pilot": "In Progress / Pilots",
        "pilots": "In Progress / Pilots",
        "implemented": "Implemented / Wins",
        "implemented / wins": "Implemented / Wins",
        "wins": "Implemented / Wins",
        "win": "Implemented / Wins",
        "na": "NA / Archived",
        "n/a": "NA / Archived",
        "archived": "NA / Archived",
        "archive": "NA / Archived",
        "invalid": "NA / Archived",
    }
    return aliases.get(lowered, status)


def _empty_submission() -> Dict[str, Any]:
    return {k: (0 if k == "upvotes" else "") for k in HEADERS}


def _compact_row_to_submission(
    row: List[str], fields: List[str], sheet_row: int
) -> Dict[str, Any]:
    sub = _empty_submission()
    sub["id"] = f"row-{sheet_row}"
    sub["_sheet_row"] = sheet_row
    for i, field in enumerate(fields):
        val = row[i] if i < len(row) else ""
        if field == "upvotes":
            try:
                sub["upvotes"] = int(str(val).strip() or "0")
            except ValueError:
                sub["upvotes"] = 0
        elif field == "status":
            sub["status"] = _normalize_status(str(val))
        elif field in HEADERS:
            sub[field] = _strip(str(val))
        else:
            # Unknown field name — stash on problem if empty
            if not sub.get("problem"):
                sub["problem"] = _strip(str(val))
    if not sub.get("status"):
        sub["status"] = "Received"
    return sub


def _read_compact_sheets() -> List[Dict[str, Any]]:
    sheet_id = _sheet_id()
    svc = _sheets_service()
    tab = _resolve_tab_title(svc, sheet_id)
    start_col, start_row, end_col, end_row = _parse_data_range(_data_range())
    fields = _column_fields()

    expected_width = _col_letters_to_index(end_col) - _col_letters_to_index(start_col) + 1
    if len(fields) != expected_width:
        logger.warning(
            "SOLUTIONS_HUB_COLUMN_FIELDS has %d names but range %s spans %d columns",
            len(fields),
            _data_range(),
            expected_width,
        )

    a1 = f"'{_escape_tab(tab)}'!{start_col}{start_row}:{end_col}"
    if end_row:
        a1 = f"'{_escape_tab(tab)}'!{start_col}{start_row}:{end_col}{end_row}"

    result = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=a1)
        .execute()
    )
    values = result.get("values") or []
    out: List[Dict[str, Any]] = []
    for offset, row in enumerate(values):
        if not any(_strip(str(c)) for c in row):
            continue
        sheet_row = start_row + offset
        out.append(_compact_row_to_submission(row, fields, sheet_row))
    logger.info("Loaded %d rows from %s (%s)", len(out), a1, fields)
    return out


def _a1_full_tab(tab: str, end_col: str = "R") -> str:
    return f"'{_escape_tab(tab)}'!A:{end_col}"


def _row_to_submission(row: List[str], headers: List[str]) -> Dict[str, Any]:
    data = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
    try:
        data["upvotes"] = int(str(data.get("upvotes") or "0").strip() or "0")
    except ValueError:
        data["upvotes"] = 0
    if "status" in data:
        data["status"] = _normalize_status(str(data.get("status") or ""))
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


def _read_full_sheets() -> List[Dict[str, Any]]:
    sheet_id = _sheet_id()
    svc = _sheets_service()
    tab = _resolve_tab_title(svc, sheet_id)
    result = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=_a1_full_tab(tab))
        .execute()
    )
    values = result.get("values") or []
    if not values:
        return []
    headers = [str(h).strip() for h in values[0]]
    if not headers or headers[0].lower() != "id":
        logger.warning("Sheet missing header row; assuming canonical HEADERS order")
        headers = list(HEADERS)
        rows = values
    else:
        rows = values[1:]
    return [
        _row_to_submission(row, headers)
        for row in rows
        if any(str(c).strip() for c in row)
    ]


def _read_all_sheets() -> List[Dict[str, Any]]:
    if _compact_mode():
        return _read_compact_sheets()
    return _read_full_sheets()


def _update_compact_row(sub: Dict[str, Any]) -> None:
    """Write only the mapped AI–AK (etc.) cells for one sheet row."""
    sheet_row = sub.get("_sheet_row")
    if not sheet_row:
        m = re.fullmatch(r"row-(\d+)", _strip(str(sub.get("id") or "")))
        if not m:
            raise ValueError("Compact-mode update requires id like row-12")
        sheet_row = int(m.group(1))

    sheet_id = _sheet_id()
    svc = _sheets_service()
    tab = _resolve_tab_title(svc, sheet_id)
    start_col, _, end_col, _ = _parse_data_range(_data_range())
    fields = _column_fields()

    values = []
    for field in fields:
        if field == "upvotes":
            values.append(str(int(sub.get("upvotes") or 0)))
        else:
            values.append(str(sub.get(field) or ""))

    a1 = f"'{_escape_tab(tab)}'!{start_col}{sheet_row}:{end_col}{sheet_row}"
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=a1,
        valueInputOption="RAW",
        body={"values": [values]},
    ).execute()


def _append_compact_row(sub: Dict[str, Any]) -> Dict[str, Any]:
    sheet_id = _sheet_id()
    svc = _sheets_service()
    tab = _resolve_tab_title(svc, sheet_id)
    start_col, start_row, end_col, _ = _parse_data_range(_data_range())
    fields = _column_fields()

    # Find next empty row in the range columns
    a1 = f"'{_escape_tab(tab)}'!{start_col}{start_row}:{end_col}"
    existing = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=a1)
        .execute()
        .get("values")
        or []
    )
    next_row = start_row + len(existing)

    values = []
    for field in fields:
        if field == "status":
            values.append(sub.get("status") or "Received")
        elif field == "upvotes":
            values.append(str(int(sub.get("upvotes") or 0)))
        else:
            values.append(str(sub.get(field) or ""))

    write_a1 = f"'{_escape_tab(tab)}'!{start_col}{next_row}:{end_col}{next_row}"
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=write_a1,
        valueInputOption="RAW",
        body={"values": [values]},
    ).execute()
    sub["id"] = f"row-{next_row}"
    sub["_sheet_row"] = next_row
    return sub


def _write_all_sheets(subs: List[Dict[str, Any]]) -> None:
    if _compact_mode():
        raise RuntimeError(
            "Refusing full-sheet overwrite in compact range mode "
            f"({_data_range()}). Updates must target individual rows."
        )
    sheet_id = _sheet_id()
    svc = _sheets_service()
    tab = _resolve_tab_title(svc, sheet_id)
    body = {"values": [HEADERS] + [_submission_to_row(s) for s in subs]}
    svc.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=_a1_full_tab(tab)
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{_escape_tab(tab)}'!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()


def _append_sheet_row(sub: Dict[str, Any]) -> Dict[str, Any]:
    if _compact_mode():
        return _append_compact_row(sub)
    sheet_id = _sheet_id()
    svc = _sheets_service()
    tab = _resolve_tab_title(svc, sheet_id)
    existing = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=_a1_full_tab(tab, "A"))
        .execute()
        .get("values")
        or []
    )
    if not existing:
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{_escape_tab(tab)}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
    svc.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=_a1_full_tab(tab),
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [_submission_to_row(sub)]},
    ).execute()
    return sub


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
        merged["status"] = _normalize_status(str(merged.get("status") or "Received"))
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
        status = _normalize_status(str(row.get("status") or "Received"))
        row = {**row, "status": status}
        if status not in columns:
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
        "source": {
            "sheet_id": _sheet_id() if not _use_local_store() else None,
            "range": _data_range() if not _use_local_store() and _compact_mode() else None,
            "fields": _column_fields() if not _use_local_store() and _compact_mode() else None,
            "mode": "local" if _use_local_store() else ("compact" if _compact_mode() else "full"),
        },
    }


def create_submission(payload: Dict[str, Any]) -> Dict[str, Any]:
    if _compact_mode() and not _use_local_store():
        # Form-response sheet: only mapped columns exist; require problem at minimum.
        if not _strip(str(payload.get("problem") or "")):
            raise ValueError("Missing required field: problem")
        sub = _empty_submission()
        sub.update(
            {
                "problem": _strip(str(payload.get("problem") or "")),
                "department": _strip(str(payload.get("department") or "")),
                "submitter_name": _strip(str(payload.get("submitter_name") or "")),
                "submitter_email": _strip(str(payload.get("submitter_email") or "")),
                "option_a": _strip(str(payload.get("option_a") or "")),
                "option_b": _strip(str(payload.get("option_b") or "")),
                "option_c": _strip(str(payload.get("option_c") or "")),
                "recommendation": _strip(str(payload.get("recommendation") or "")),
                "resources": _strip(str(payload.get("resources") or "")),
                "estimated_value": _strip(str(payload.get("estimated_value") or "")),
                "status": "Received",
                "upvotes": 0,
                "submitted_at": _utcnow_iso(),
            }
        )
        with _lock:
            return _append_sheet_row(sub)

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
                status = _normalize_status(str(value))
                if status not in STATUSES:
                    raise ValueError(f"Invalid status: {status}")
                current["status"] = status
            else:
                current[key] = _strip(str(value)) if value is not None else ""

        rows[idx] = current
        if _use_local_store():
            _write_all_local(rows)
        elif _compact_mode():
            _update_compact_row(current)
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
        elif _compact_mode():
            if "upvotes" in _column_fields():
                _update_compact_row(current)
            else:
                logger.info(
                    "Upvote for %s not persisted — upvotes is not in SOLUTIONS_HUB_COLUMN_FIELDS",
                    sid,
                )
        else:
            _write_all_sheets(rows)
        return current


def replace_all_submissions(subs: List[Dict[str, Any]]) -> int:
    """Ops helper for CSV seed — overwrites full sheet/local store (not compact mode)."""
    if _compact_mode() and not _use_local_store():
        raise RuntimeError(
            "seed/replace is disabled for compact range mode to protect the form sheet. "
            "Clear SOLUTIONS_HUB_DATA_RANGE or use local storage."
        )
    normalized: List[Dict[str, Any]] = []
    for item in subs:
        if not isinstance(item, dict):
            continue
        row = {k: item.get(k, "") for k in HEADERS}
        if not _strip(str(row.get("id") or "")):
            row["id"] = str(uuid.uuid4())
        row["status"] = _normalize_status(str(row.get("status") or "Received"))
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

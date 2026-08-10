"""Notification hooks (SLA receipts / status alerts). Current owns production email later."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SLA_DAYS = 30


def send_sla_receipt(
    submitter_email: str,
    submission: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Stub for 30-day SLA confirmation email.

    Production refinement is expected via Sunrun Current. This logs intent so the
    intake path stays wired without sending mail yet.
    """
    email = (submitter_email or "").strip()
    sub_id = ""
    if submission:
        sub_id = str(submission.get("id") or "")
    payload = {
        "channel": "stub",
        "to": email,
        "template": "sla_receipt",
        "sla_days": SLA_DAYS,
        "submission_id": sub_id,
        "sent": False,
        "message": (
            f"SLA receipt queued (stub): commit to {SLA_DAYS}-day initial review "
            f"for submission {sub_id or '(unknown)'} → {email or '(no email)'}"
        ),
    }
    logger.info(payload["message"])
    return payload

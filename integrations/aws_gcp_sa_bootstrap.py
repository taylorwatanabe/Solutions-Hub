"""
Load GCP service account key JSON into ``GCP_SERVICE_ACCOUNT_JSON``.

Ported from QC_Dashboard — reuse the same AWS Secrets Manager secret as the
QC / CAT-2 environment (``AWS_GCP_SERVICE_ACCOUNT_SECRET_ID``).

Two supported uses of ``AWS_GCP_SERVICE_ACCOUNT_SECRET_ID``:

1. **ECS Express Mode secret injection** — The task maps an ASM secret into this
   env var **name**; the **value** at runtime is the decrypted secret string
   (full GCP service-account JSON). Detected as JSON and copied to
   ``GCP_SERVICE_ACCOUNT_JSON`` without boto3.

2. **Runtime fetch** — The env var holds the secret **name** or ARN; the app
   calls ``GetSecretValue``. Requires IAM ``secretsmanager:GetSecretValue``.

Skipped when ``GCP_SERVICE_ACCOUNT_JSON`` is already set or
``AWS_GCP_SERVICE_ACCOUNT_SECRET_ID`` is unset.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def _secret_id_for_logs(secret_id: str) -> str:
    s = secret_id.strip()
    if s.startswith("{"):
        return f"<inline JSON, {len(s)} chars>"
    if len(s) > 96:
        return f"{s[:48]}…({len(s)} chars)"
    return s


def _apply_inlined_gcp_service_account_json(value: str) -> bool:
    v = (value or "").replace("\r\n", "\n").strip()
    if v.startswith("\ufeff"):
        v = v[1:].lstrip()
    if not v.startswith("{"):
        return False
    try:
        parsed = json.loads(v)
    except json.JSONDecodeError:
        logger.warning(
            "AWS_GCP_SERVICE_ACCOUNT_SECRET_ID looks like JSON but failed to parse; "
            "if this should be a secret name/ARN, fix the task definition."
        )
        return False
    if not isinstance(parsed, dict) or parsed.get("type") != "service_account":
        logger.warning(
            "AWS_GCP_SERVICE_ACCOUNT_SECRET_ID JSON is not a GCP service_account key "
            "(expected type=service_account)"
        )
        return False
    normalized = json.dumps(parsed, separators=(",", ":"))
    os.environ["GCP_SERVICE_ACCOUNT_JSON"] = normalized
    logger.info(
        "Set GCP_SERVICE_ACCOUNT_JSON from inlined AWS_GCP_SERVICE_ACCOUNT_SECRET_ID (%d chars)",
        len(normalized),
    )
    return True


def load_gcp_service_account_from_aws_secrets_manager() -> None:
    if (os.getenv("GCP_SERVICE_ACCOUNT_JSON") or "").strip():
        logger.debug("Skipping AWS SM GCP SA fetch: GCP_SERVICE_ACCOUNT_JSON already set")
        return

    secret_id = (os.getenv("AWS_GCP_SERVICE_ACCOUNT_SECRET_ID") or "").strip()
    if not secret_id:
        return

    if secret_id.startswith("arn:aws:secretsmanager") and not secret_id.strip().startswith("{"):
        # Name/ARN path — fetch below. Warn if someone mistakenly left an ARN
        # where plaintext JSON was expected without task-role GetSecretValue.
        pass

    if _apply_inlined_gcp_service_account_json(secret_id):
        return

    region = (
        (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
        or None
    )

    try:
        import boto3
    except ImportError as e:
        logger.error("boto3 is required to read AWS Secrets Manager: %s", e)
        raise

    client_kw = {}
    if region:
        client_kw["region_name"] = region

    logger.info(
        "Fetching GCP service account JSON from AWS Secrets Manager (secret_id=%s, region=%r)",
        _secret_id_for_logs(secret_id),
        region or "default session",
    )

    client = boto3.client("secretsmanager", **client_kw)
    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except Exception as e:
        logger.error(
            "AWS GetSecretValue failed for %s: %s",
            _secret_id_for_logs(secret_id),
            e,
        )
        raise

    payload = resp.get("SecretString")
    if not payload or not str(payload).strip():
        raise ValueError(
            f"AWS secret {secret_id!r} has no SecretString (use a text/JSON secret type)"
        )

    raw = str(payload).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            "AWS secret must contain raw JSON (GCP service account key). "
            f"Parse error: {e}"
        ) from e

    if not isinstance(parsed, dict) or parsed.get("type") != "service_account":
        logger.warning(
            "AWS secret JSON does not look like a GCP service_account key (missing type/service_account)"
        )

    os.environ["GCP_SERVICE_ACCOUNT_JSON"] = raw
    logger.info(
        "Set GCP_SERVICE_ACCOUNT_JSON from AWS secret (%d chars)",
        len(raw),
    )

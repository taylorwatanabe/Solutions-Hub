"""Solutions Hub — Flask API + static Kanban / intake UI."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from integrations.dotenv_util import load_dotenv_files
from integrations import notifications, sheets_store

load_dotenv_files()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("solutions_hub")

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

app = Flask(__name__, static_folder=None)

if os.getenv("TRUST_PROXY_HEADERS", "1").strip() in ("1", "true", "True", "yes"):
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]


def _build_id() -> str:
    return (
        os.getenv("SOLUTIONS_HUB_BUILD_ID")
        or os.getenv("CAT2_DASHBOARD_BUILD_ID")
        or "unknown"
    ).strip()


@app.get("/healthz")
@app.get("/health")
@app.get("/ping")
@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/build-info")
def build_info():
    return jsonify(
        {
            "service": "Solution Hub",
            "build_id": _build_id(),
            "statuses": sheets_store.STATUSES,
        }
    )


@app.get("/api/board")
def api_board():
    department = (request.args.get("department") or "").strip() or None
    try:
        board = sheets_store.get_board(department=department)
        return jsonify(board)
    except Exception as e:
        logger.exception("GET /api/board failed")
        return jsonify({"error": str(e)}), 500


@app.get("/api/submissions")
def api_list_submissions():
    department = (request.args.get("department") or "").strip() or None
    try:
        return jsonify({"submissions": sheets_store.list_submissions(department=department)})
    except Exception as e:
        logger.exception("GET /api/submissions failed")
        return jsonify({"error": str(e)}), 500


@app.post("/api/submissions")
def api_create_submission():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    try:
        sub = sheets_store.create_submission(payload)
        sla = notifications.send_sla_receipt(
            str(payload.get("submitter_email") or sub.get("submitter_email") or ""),
            submission=sub,
        )
        return jsonify({"submission": sub, "sla": sla}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("POST /api/submissions failed")
        return jsonify({"error": str(e)}), 500


@app.patch("/api/submissions/<submission_id>")
def api_patch_submission(submission_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    try:
        sub = sheets_store.update_submission(submission_id, payload)
        return jsonify({"submission": sub})
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("PATCH /api/submissions/%s failed", submission_id)
        return jsonify({"error": str(e)}), 500


@app.post("/api/submissions/<submission_id>/upvote")
def api_upvote(submission_id: str):
    try:
        sub = sheets_store.upvote_submission(submission_id)
        return jsonify({"submission": sub})
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception("POST upvote failed for %s", submission_id)
        return jsonify({"error": str(e)}), 500


@app.get("/")
def index():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/css/<path:filename>")
def css(filename: str):
    return send_from_directory(PUBLIC / "css", filename)


@app.get("/js/<path:filename>")
def js(filename: str):
    return send_from_directory(PUBLIC / "js", filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")

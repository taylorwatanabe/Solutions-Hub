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
    logger.info("GET /api/board start department=%r", department)
    try:
        board = sheets_store.get_board(department=department)
        logger.info(
            "GET /api/board ok total=%s months=%s",
            board.get("total"),
            board.get("months"),
        )
        return jsonify(board)
    except Exception as e:
        logger.exception("GET /api/board failed")
        msg = str(e)
        lowered = msg.lower()
        if "timed out" in lowered or "timeout" in lowered:
            return jsonify(
                {
                    "error": "Google Sheets timed out. Click Refresh to try again.",
                }
            ), 504
        return jsonify({"error": msg}), 500


@app.get("/api/diag/sheets")
def api_diag_sheets():
    """Quick connectivity check for Google Sheets (no full board build)."""
    import time as _time

    started = _time.time()
    try:
        tab = sheets_store._resolve_tab_title_fast(sheets_store._sheet_id())
        token_preview = sheets_store._google_access_token()[:12] + "…"
        return jsonify(
            {
                "ok": True,
                "tab": tab,
                "token_prefix": token_preview,
                "elapsed_ms": int((_time.time() - started) * 1000),
                "sheet_id": sheets_store._sheet_id(),
                "gid": sheets_store._sheet_gid(),
            }
        )
    except Exception as e:
        logger.exception("GET /api/diag/sheets failed")
        return jsonify(
            {
                "ok": False,
                "error": str(e),
                "elapsed_ms": int((_time.time() - started) * 1000),
            }
        ), 500


@app.get("/api/submissions")
def api_list_submissions():
    department = (request.args.get("department") or "").strip() or None
    try:
        return jsonify({"submissions": sheets_store.list_submissions(department=department)})
    except Exception as e:
        logger.exception("GET /api/submissions failed")
        return jsonify({"error": str(e)}), 500


@app.patch("/api/submissions/<submission_id>")
def api_patch_submission(submission_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    try:
        sub = sheets_store.update_submission(submission_id, payload)
        return jsonify({"submission": sub})
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("PATCH /api/submissions/%s failed", submission_id)
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
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("POST /api/submissions failed")
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


@app.get("/favicon.ico")
def favicon_ico():
    return send_from_directory(PUBLIC, "favicon.ico")


@app.get("/favicon.png")
def favicon_png():
    return send_from_directory(PUBLIC, "favicon.png")


@app.get("/favicon-32.png")
def favicon_32():
    return send_from_directory(PUBLIC, "favicon-32.png")


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return send_from_directory(PUBLIC, "apple-touch-icon.png")


@app.get("/img/<path:filename>")
def img(filename: str):
    return send_from_directory(PUBLIC / "img", filename)


@app.get("/css/<path:filename>")
def css(filename: str):
    return send_from_directory(PUBLIC / "css", filename)


@app.get("/js/<path:filename>")
def js(filename: str):
    return send_from_directory(PUBLIC / "js", filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
else:
    # Warm Sheets client/cache in the background so the first board request
    # is less likely to hit the gateway 504.
    def _warm_sheets_cache() -> None:
        import sys
        import time as _time

        print("sheets-warm: starting", file=sys.stderr, flush=True)
        for attempt in range(1, 4):
            try:
                print(f"sheets-warm: attempt {attempt}", file=sys.stderr, flush=True)
                sheets_store.get_board()
                print(f"sheets-warm: success on attempt {attempt}", file=sys.stderr, flush=True)
                logger.info("Warmed Sheets board cache (attempt %d)", attempt)
                return
            except Exception as e:
                print(f"sheets-warm: failed attempt {attempt}: {e}", file=sys.stderr, flush=True)
                logger.exception("Sheets warm-up failed (attempt %d/3)", attempt)
                _time.sleep(min(5, attempt * 2))

    import threading

    threading.Thread(target=_warm_sheets_cache, name="sheets-warm", daemon=True).start()

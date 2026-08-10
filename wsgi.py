"""
WSGI entry for Gunicorn on ECS Express Mode / ALB.

Health-check paths are answered here without importing ``app.py``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, List, Optional, Tuple

_HEALTH_PATHS = frozenset({"/healthz", "/health", "/ping"})
_OK_JSON = json.dumps({"status": "ok"}).encode("utf-8")

_flask_app: Optional[Any] = None

StartResponse = Callable[[str, List[Tuple[str, str]]], Any]
WSGIApp = Callable[[dict, StartResponse], Iterable[bytes]]


def _path(environ: dict) -> str:
    raw = (environ.get("PATH_INFO") or "/").strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw != "/" and raw.endswith("/"):
        raw = raw.rstrip("/")
    return raw or "/"


def _is_lb_health_probe(environ: dict) -> bool:
    ua = (environ.get("HTTP_USER_AGENT") or "").strip().lower()
    if not ua:
        return True
    return (
        "elb-healthchecker" in ua
        or "amazon-route53-healthcheck" in ua
        or "amazon-route53-health-check" in ua
        or ("amazon" in ua and "route 53" in ua and "health" in ua)
        or "kube-probe/" in ua
        or "googlehc/" in ua
    )


def _is_plain_api_health(environ: dict) -> bool:
    qs = (environ.get("QUERY_STRING") or "").lower()
    return "diagnostics=1" not in qs and "diagnostics=true" not in qs and "diagnostics=yes" not in qs


def _should_answer_health_immediately(environ: dict) -> bool:
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    if method not in ("GET", "HEAD"):
        return False
    path = _path(environ)
    if path in _HEALTH_PATHS:
        return True
    if path == "/api/health" and _is_plain_api_health(environ):
        return True
    if path == "/" and _is_lb_health_probe(environ):
        return True
    return False


def _health_response(environ: dict, start_response: StartResponse) -> List[bytes]:
    start_response("200 OK", [("Content-Type", "application/json")])
    if (environ.get("REQUEST_METHOD") or "GET").upper() == "HEAD":
        return []
    return [_OK_JSON]


def _flask_app_loaded() -> WSGIApp:
    global _flask_app
    if _flask_app is None:
        from app import app as flask_application

        _flask_app = flask_application
    return _flask_app


def application(environ: dict, start_response: StartResponse) -> Iterable[bytes]:
    if _should_answer_health_immediately(environ):
        return _health_response(environ, start_response)
    return _flask_app_loaded()(environ, start_response)

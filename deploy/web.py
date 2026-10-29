"""Production-only entry point. Local app.py behaviour stays intact."""
from collections import defaultdict, deque
import os
import threading
import time
from urllib.parse import urlsplit

from flask import jsonify, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

import app as cm
from deploy.client import JudgeClient


def configure(application, client, origin):
    address = urlsplit(origin)
    if address.scheme != "https" or not address.hostname or address.path not in ("", "/"):
        raise ValueError("CMASTER_ORIGIN must be the HTTPS public origin")
    application.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_NAME="__Host-cmaster",
        MAX_CONTENT_LENGTH=1024 * 1024, PREFERRED_URL_SCHEME="https")
    # The web port is only available behind Caddy on the private Compose network.
    application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1)
    cm.judge = client
    hits, lock = defaultdict(deque), threading.Lock()
    cache = {"at": 0, "info": {"ok": False}}

    @application.context_processor
    def cloud_context():
        return {"cloud_mode": True}

    @application.before_request
    def public_boundary():
        if request.path not in ("/healthz", "/readyz") and request.host != address.netloc:
            return jsonify(ok=False, error="访问地址无效。"), 400
        if request.path in ("/admin", "/ops") or request.path.startswith(("/api/admin", "/api/ops")):
            return jsonify(ok=False, error="线上服务由服务器管理员维护。"), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("Origin")
            if (supplied and supplied.rstrip("/") != origin.rstrip("/")) or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(ok=False, error="请在本站页面中重试。"), 403
        if request.path == "/api/run" and not cm.current_user():
            return jsonify(ok=False, error="请先登录再运行代码。"), 401
        if request.path == "/login" and request.method == "POST":
            if len(request.form.get("username", "")) > 64 or len(request.form.get("password", "")) > 1024:
                return jsonify(ok=False, error="用户名或密码过长。"), 400
            # Avoid external redirects such as //example.com after login.
            target = request.form.get("next", "")
            if target.startswith("//") or "\\" in target or any(ord(c) < 32 for c in target):
                return jsonify(ok=False, error="登录后的跳转地址无效。"), 400
        limited = request.method == "POST" and request.path in ("/login", "/api/run", "/api/submit")
        if limited:
            now = time.monotonic()
            bucket = "login" if request.path == "/login" else "judge"
            identity = (request.remote_addr, bucket)
            with lock:
                for key in list(hits):
                    if not hits[key] or hits[key][-1] <= now - 60:
                        del hits[key]
                queue = hits[identity]
                while queue and queue[0] <= now - 60:
                    queue.popleft()
                if len(queue) >= (10 if bucket == "login" else 12):
                    return jsonify(ok=False, error="操作太频繁，请稍后再试。"), 429, {"Retry-After": "60"}
                queue.append(now)

    @application.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if session or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def readiness():
        now = time.monotonic()
        if now - cache["at"] > 30:
            cache.update(at=now, info=client.compiler_info())
        writable = False
        try:
            import tempfile
            with tempfile.TemporaryFile(dir=cm.DATA_DIR) as probe:
                probe.write(b"ok")
            writable = True
        except OSError:
            pass
        ready = bool(cache["info"].get("ok")) and writable
        return jsonify(ok=ready, checks={"isolated_judge": bool(cache["info"].get("ok")),
                                        "persistent_data": writable}), 200 if ready else 503

    application.view_functions["readyz"] = readiness
    return application


def create_app():
    return configure(cm.app, JudgeClient(), os.environ["CMASTER_ORIGIN"])

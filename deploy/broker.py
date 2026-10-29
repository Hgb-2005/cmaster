"""Internal Unix socket broker. Only this service can access the Docker daemon.

Each request starts a fresh container without volumes, networking, credentials
or website data. The caller cannot supply images, mounts, environment or flags.
"""
import json
import os
from pathlib import Path
import socketserver
import subprocess
import threading
import uuid

from deploy.client import MAX_MESSAGE, system_error

IMAGE = "cmaster-judge:online"
SLOTS = threading.BoundedSemaphore(1)


def command(name):
    return ["docker", "run", "--rm", "-i", "--name", name,
            "--network", "none", "--read-only", "--user", "10001:10001",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1.0",
            "--pids-limit", "64", "--ulimit", "nofile=128:128",
            "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=128m,mode=1777",
            "--log-driver", "none", IMAGE]


def validate(payload):
    if not isinstance(payload, dict) or payload.get("action") not in ("info", "run", "judge"):
        return False
    if payload["action"] == "info":
        return set(payload) == {"action"}
    if not isinstance(payload.get("code"), str) or len(payload["code"]) > 100_000:
        return False
    if payload["action"] == "run":
        return isinstance(payload.get("stdin", ""), str) and len(payload.get("stdin", "")) <= 100_000
    question = payload.get("question")
    return isinstance(question, dict) and isinstance(question.get("cases"), list) and len(question["cases"]) <= 16


def execute(payload):
    if not validate(payload):
        return system_error("判题请求格式无效。")
    if not SLOTS.acquire(blocking=False):
        return system_error("判题机正忙，请稍后重试。")
    name = "cmaster-job-" + uuid.uuid4().hex
    try:
        completed = subprocess.run(command(name), input=json.dumps(payload).encode(),
                                   capture_output=True, timeout=60)
        if completed.returncode != 0 or len(completed.stdout) > MAX_MESSAGE:
            return system_error("判题容器未正常完成，请稍后重试。")
        result = json.loads(completed.stdout)
        return result if isinstance(result, dict) else system_error("判题结果无效。")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return system_error("判题容器启动失败或执行超时，请稍后重试。")
    finally:
        try:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        SLOTS.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        try:
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                return
            result = execute(json.loads(raw))
        except (OSError, ValueError):
            result = system_error("判题请求格式无效。")
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")


def main():
    if not hasattr(socketserver, "UnixStreamServer"):
        raise SystemExit("The deployment broker requires Linux")
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
    path = Path("/run/cmaster/judge.sock")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
        os.chown(path, 10001, 10001)
        os.chmod(path, 0o600)
        print("CMaster isolated judge broker ready", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()

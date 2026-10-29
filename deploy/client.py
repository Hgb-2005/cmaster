"""Small, bounded protocol between the website and the local job broker."""
import json
import os
import socket

MAX_MESSAGE = 1024 * 1024


def system_error(message):
    return {"status": "SE", "owner": "system", "message": message,
            "ok": False, "cases": [], "total": 0, "passed": 0}


class JudgeClient:
    def __init__(self, path=None):
        self.path = path or os.environ.get("CMASTER_JUDGE_SOCKET", "/run/cmaster/judge.sock")

    def request(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(data) > MAX_MESSAGE:
            return system_error("请求内容过大，请缩短代码或标准输入。")
        if not hasattr(socket, "AF_UNIX"):
            return system_error("在线判题服务需要 Linux 部署环境。")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(70)
                connection.connect(self.path)
                connection.sendall(data)
                with connection.makefile("rb") as stream:
                    response = stream.readline(MAX_MESSAGE + 1)
            if len(response) > MAX_MESSAGE or not response.endswith(b"\n"):
                raise ValueError("invalid response size")
            result = json.loads(response)
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            return result
        except (OSError, ValueError):
            return system_error("判题服务暂时不可用，代码已保留，请稍后重试。")

    def compiler_info(self):
        return self.request({"action": "info"})

    def find_compiler(self):
        return "isolated-gcc" if self.compiler_info().get("ok") else None

    def run_freeform(self, code, stdin_data=""):
        return self.request({"action": "run", "code": code, "stdin": stdin_data})

    def judge(self, question, code):
        return self.request({"action": "judge", "question": question, "code": code})

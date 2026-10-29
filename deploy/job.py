"""A single compiler job. This image contains public engine code only."""
import json
from pathlib import Path
import sys

import judge
import sandbox
from deploy.client import MAX_MESSAGE, system_error
from deploy.job_sandbox import run


def main():
    if not Path("/.dockerenv").exists():
        raise SystemExit("Run this entry point only in the isolated job container")
    raw = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
    if len(raw) > MAX_MESSAGE:
        raise SystemExit("request too large")
    payload = json.loads(raw)
    sandbox.run = run
    if payload["action"] == "info":
        result = judge.compiler_info()
    elif payload["action"] == "run":
        result = judge.run_freeform(payload["code"], payload.get("stdin", ""))
    elif payload["action"] == "judge":
        result = judge.judge(payload["question"], payload["code"])
    else:
        result = system_error("未知判题操作。")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

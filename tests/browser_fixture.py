"""浏览器回归专用服务：临时账号、可控判题结果，不连接真实学习数据。"""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temporary = tempfile.TemporaryDirectory(prefix="cmaster-browser-")
os.environ["CMASTER_DATA"] = temporary.name
import app as cm


def fixture_judge(question, code):
    status = "AC" if "QA_ACCEPT" in code else "SE" if "QA_SYSTEM" in code else "WA"
    return {"status": status, "owner": cm.verdicts.owner(status),
            "total": 1, "passed": int(status == "AC"), "time_ms": 1,
            "message": "浏览器回归测试结果", "cases": []}


cm.judge.judge = fixture_judge
cm.app.run(host="127.0.0.1", port=int(os.environ.get("CMASTER_TEST_PORT", "8792")),
           threaded=True, debug=False)

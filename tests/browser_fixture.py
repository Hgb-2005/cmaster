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
    cases = []
    if status != "SE":
        for index, case in enumerate(question.get("cases", []), 1):
            cases.append({
                "idx": index,
                "name": case.get("name") or "用例 %d" % index,
                "input": case.get("input", ""),
                "expected": case.get("output", ""),
                "actual": case.get("output", "") if status == "AC" else "fixture-wrong",
                "status": status,
                "stderr": "",
                "time_ms": 1,
                "cpu_ms": 1,
            })
    return {"status": status, "owner": cm.verdicts.owner(status),
            "total": len(cases), "passed": len(cases) if status == "AC" else 0,
            "time_ms": 1, "message": "浏览器回归测试结果", "cases": cases}


cm.judge.judge = fixture_judge
cm.app.run(host="127.0.0.1", port=int(os.environ.get("CMASTER_TEST_PORT", "8792")),
           threaded=True, debug=False)

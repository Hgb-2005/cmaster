"""题库门禁：每道题都拿参考答案去真编译、真运行、比对全部用例。

只读，不写任何进度。任何一题不是 AC 就退出码非 0。
跑法：
    ./runtime/venv/Scripts/python.exe tools/gate.py
可选：
    ./runtime/venv/Scripts/python.exe tools/gate.py c004 c012   # 只验指定题
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    import app
    import judge

    only = sys.argv[1:]
    qids = only or list(app.QMAP)

    info = judge.compiler_info()
    if not info.get("ok"):
        print("编译器不可用：%s" % info.get("hint", ""))
        return 2
    print("编译器：%s\n" % info.get("version", "?"))

    fail = []
    for i, qid in enumerate(qids, 1):
        q = app.QMAP[qid]
        code = app.ANSWERS.get(qid, "")
        if not code.strip():
            fail.append((qid, "无参考答案", ""))
            print("%3d/%d  %s  缺参考答案" % (i, len(qids), qid))
            continue
        r = judge.judge(q, code)
        cases = len(q.get("cases") or [])
        if r["status"] == "AC":
            print("%3d/%d  %s  [%s] %s  %d/%d 用例"
                  % (i, len(qids), qid, q["difficulty"], q["title"],
                     r.get("passed", 0), cases))
        else:
            fail.append((qid, r["status"], (r.get("message") or "")[:200]))
            print("%3d/%d  %s  [%s] %s  <- %s %s"
                  % (i, len(qids), qid, q["difficulty"], q["title"],
                     r["status"], (r.get("message") or "")[:120]))

    print("\n通过 %d / %d" % (len(qids) - len(fail), len(qids)))
    if fail:
        print("失败明细：")
        for qid, st, msg in fail:
            print("  %s  %s  %s" % (qid, st, msg))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())

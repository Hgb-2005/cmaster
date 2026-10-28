#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容装配 + 题库门禁。

把 tools/content_*.py 里的 Python 数据编译成运行时读的 JSON：

    data/courses.json        —— 12 章 / 48 个知识点，md 已转成 HTML
    data/question_bank.json  —— 60 道题，含测试用例

同时执行**门禁**：把每道题的参考答案丢进判题引擎跑它自己的用例，
任何一题不是 AC 就整体失败、不写文件。

为什么要有门禁
--------------
手写测试用例的期望输出，人一定会算错几道。上线后的表现是最恶劣的那种：
学生写对了，系统判他错，他还以为自己错了。所以每次改完题库都必须
用参考答案把全部用例跑一遍——红灯先怀疑用例，再怀疑答案。

用法
----
    python tools/build_content.py            # 装配 + 门禁
    python tools/build_content.py --no-gate  # 只装配，跳过判题（调试用）
    python tools/build_content.py --gate-only # 只跑门禁
"""

import argparse
import json
import os
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(TOOLS_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")

sys.path.insert(0, TOOLS_DIR)
sys.path.insert(0, BASE_DIR)

from md2html import md2html                       # noqa: E402
from content_ch_a import CHAPTERS_A               # noqa: E402
from content_ch_b import CHAPTERS_B               # noqa: E402
from content_qb_a import QUESTIONS_A              # noqa: E402
from content_qb_b import QUESTIONS_B              # noqa: E402


# ---------------------------------------------------------------- 装配

def build_courses():
    """章节 → 课程 JSON。知识点正文由 md 转 HTML，前端直接渲染。"""
    chapters = list(CHAPTERS_A) + list(CHAPTERS_B)
    out = []
    for ch in chapters:
        kps = []
        for kp in ch["knowledge_points"]:
            kps.append({
                "title": kp["title"],
                "kind": kp.get("kind", "concept"),
                "html": md2html(kp["md"]),
                # 保留原文：便于以后做全文搜索 / 导出，也方便排查渲染问题
                "md": kp["md"],
            })
        out.append({
            "id": ch["id"],
            "title": ch["title"],
            "icon": ch.get("icon", ""),
            "stage": ch["stage"],
            "stage_description": ch.get("stage_description", ""),
            "description": ch.get("description", ""),
            "knowledge_points": kps,
        })
    return out


def build_questions():
    """题库 JSON。answer 只给门禁和"查看答案"用，判题时不下发给前端。"""
    qs = list(QUESTIONS_A) + list(QUESTIONS_B)
    out = []
    for q in qs:
        out.append({
            "id": q["id"],
            "chapter": q["chapter"],
            "difficulty": q.get("difficulty", "easy"),
            "tags": q.get("tags", []),
            "title": q["title"],
            "description_html": md2html(q["description"]),
            "description": q["description"],
            "hint": q.get("hint", ""),
            "template": q.get("template", ""),
            "answer": q.get("answer", ""),
            "cases": [{"input": c["input"], "output": c["output"]}
                      for c in q["cases"]],
        })
    return out


# ---------------------------------------------------------------- 门禁

def gate(questions, verbose=True):
    """用参考答案跑自己的用例，全 AC 才放行。返回失败清单。"""
    import judge

    info = judge.compiler_info()
    if verbose:
        print("  编译器: %s" % info.get("version", "?").splitlines()[0])
        print("  路径  : %s" % info.get("path", "?"))

    failures = []
    t0 = time.time()

    for i, q in enumerate(questions, 1):
        result = judge.judge(q, q["answer"])
        status = result.get("status")

        if status == "AC":
            if verbose:
                print("  [%2d/%d] %s AC  %d/%d 用例  %sms"
                      % (i, len(questions), q["id"], result.get("passed", 0),
                         result.get("total", 0), result.get("time_ms", 0)))
            continue

        failures.append({"id": q["id"], "title": q["title"],
                         "result": result})
        if verbose:
            print("  [%2d/%d] %s %s  <-- 失败" % (i, len(questions), q["id"], status))
            _dump_failure(q, result)

    if verbose:
        print("  门禁耗时 %.1fs" % (time.time() - t0))
    return failures


def _dump_failure(q, result):
    """把失败详情打清楚，省得再去手跑一遍。"""
    print("        题目: %s" % q["title"])
    print("        裁决: %s  通过 %s/%s"
          % (result.get("status"), result.get("passed"), result.get("total")))

    ce = result.get("compile_msg")
    if ce:
        print("        编译错误:")
        for line in ce.strip().splitlines()[:10]:
            print("          | %s" % line)
        return

    # 逐用例找第一个不通过的，把输入/期望/实际三件套摆出来
    for c in result.get("cases") or []:
        if c.get("status") == "AC":
            continue
        print("        %s: %s" % (c.get("name", "用例"), c.get("status")))
        print("          输入  : %r" % c.get("input", ""))
        print("          期望  : %r" % c.get("expected", ""))
        print("          实际  : %r" % c.get("actual", ""))
        if c.get("stderr"):
            print("          stderr: %s" % str(c["stderr"])[:300])
        break


# ---------------------------------------------------------------- 主流程

def write_json(name, payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)          # 原子替换，避免半截文件被读到
    size = os.path.getsize(path) / 1024
    print("  -> %s (%.0f KB)" % (path, size))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gate", action="store_true", help="跳过判题门禁")
    ap.add_argument("--gate-only", action="store_true", help="只跑门禁")
    args = ap.parse_args()

    courses = build_courses()
    questions = build_questions()

    n_kp = sum(len(c["knowledge_points"]) for c in courses)
    print("装配结果: %d 章 / %d 个知识点 / %d 道题"
          % (len(courses), n_kp, len(questions)))

    if not args.gate_only:
        write_json("courses.json", {
            "stages": _group_stages(courses),
            "chapters": courses,
        })
        # 门禁用的题目和线上题库分开存：answers.json 只留在服务端
        write_json("question_bank.json", [
            {k: v for k, v in q.items() if k != "answer"} for q in questions
        ])
        write_json("answers.json", {q["id"]: q["answer"] for q in questions})
        from build_training import build_document
        write_json("training.json", build_document())

    if args.no_gate:
        print("跳过门禁（--no-gate）")
        return 0

    print("门禁: 用参考答案跑全部用例 ...")
    failures = gate(questions)

    if failures:
        print()
        print("!! 门禁失败: %d 道题没跑通" % len(failures))
        print("!! 这些题现在上线会误判学生代码，已中止。")
        for f in failures:
            print("!!   %s %s" % (f["id"], f["title"]))
        return 1

    print("门禁通过: %d 道题参考答案全部 AC" % len(questions))
    return 0


def _group_stages(courses):
    """按 stage 分组，前端侧边栏按阶段折叠用。"""
    order, groups = [], {}
    for c in courses:
        s = c["stage"]
        if s not in groups:
            order.append(s)
            groups[s] = {"name": s,
                         "description": c["stage_description"],
                         "chapters": []}
        groups[s]["chapters"].append({"id": c["id"], "title": c["title"],
                                      "icon": c["icon"]})
    return [groups[s] for s in order]


if __name__ == "__main__":
    sys.exit(main())

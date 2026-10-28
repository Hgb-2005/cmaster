# -*- coding: utf-8 -*-
"""生成 data/training.json —— /training 训练模式的卡片。

为什么卡片是算出来的，而不是手写的
----------------------------------
三种操练需要的原料，仓库里已经全有了：

    顺藤摸瓜（trace）   answers.json 的参考代码 + question_bank.json 的用例
    填空补洞（cloze）   同一份参考代码，按规则挖掉关键 token
    概念快答（flash）   courses.json 的知识点标题与正文

手写一套卡片会立刻带来两个问题：一是 60 道题改一次答案，卡片就悄悄过期
（页面上看着正常，只是标准答案错了——最恶劣的那种错）；二是手写 100 多张
卡片的「挖空位置」几乎必然前后不一致：同一类 token 有的挖有的不挖，学生
就学不到「该盯哪里」。所以这里做成一条构建流水线，和 build_content.py 一样
是「改内容必须重跑」的东西。

挖空挖的是什么（这是本文件唯一需要动脑的地方）
----------------------------------------------
不是随机挖。C 的初学者栽跟头的地方高度集中，挖空就盯这几类，按优先级：

    p1  ->            结构体指针成员访问，逗号 vs 箭头的分水岭
    p2  %d %s %f ...  printf/scanf 的格式符，错一个就是未定义行为
    p3  == != <= >= && ||   条件运算，= 和 == 的混用是经典事故
    p4  sizeof        不是函数、算的是字节数，两个误解各占一半
    p5  NULL          空指针的写法（不是 null、不是 Null）
    p6  printf/scanf/malloc/free/strlen/strcpy/...  该调谁
    p7  &             取地址
    p8  *             解引用

优先级靠前的先挖，且**每行最多先挖一个**——挖空要分布在这段代码的整个
流程上，不能把三行里的 `%d` 全挖了，剩下二十行一个洞没有。

`#include` / `#define` 行整行不挖：那两行本来就是抄的，挖了只剩挫败感。

可复现
------
词库的乱序用的是以卡片 id 为种子的自实现 PRNG，不用 random.shuffle。
同样的输入重复构建必须产出逐字节相同的文件，否则每次 build 都进 diff，
"这行改动是我写的还是构建抖出来的"就说不清了。

用法：
    python tools/build_training.py            # 重新生成
    python tools/build_training.py --check    # 只校验，不写文件
    python tools/build_training.py --stat     # 看看每种模式各多少张、挖了哪些洞
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "training.json")

# 一张卡最多挖几个空。8 个已经是「这段代码我得逐行读」的量；
# 再多就变成体力活，训练模式要的是「一眼扫过去哪里有坑」。
MAX_BLANKS = 8
MIN_BLANKS = 3

# 顺藤摸瓜的可用上限：要靠脑子跑出来的输出，不能太长。
# 6 行 / 200 字符大致是「能手算」和「必须真跑一遍」的分界。
TRACE_MAX_LINES = 6
TRACE_MAX_CHARS = 200

# 挖空优先级表。顺序即优先级，[] 内是「挖哪一个片段」的分组。
BLANK_RULES = [
    # (优先级, 名字, 正则, 取第几组)
    ("p1", "箭头", re.compile(r"->"), 0),
    # 格式符要连长度修饰一起吃掉。写成 [diouxXeEfgGcspl] 会把 "%lf" 切成
    # "%l"——前端画出来是 `%l` + `f` 两截，答案永远对不上，而校验也通过了。
    ("p2", "格式符", re.compile(
        r"%[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|L|z|j|t)?[diouxXeEfgGcs]"), 0),
    ("p3", "比较", re.compile(r"==|!=|<=|>=|&&|\|\|"), 0),
    ("p4", "sizeof", re.compile(r"\bsizeof\b"), 0),
    ("p5", "NULL", re.compile(r"\bNULL\b"), 0),
    ("p6", "标准库", re.compile(
        r"\b(?:printf|scanf|malloc|calloc|realloc|free|strlen|strcpy|strcmp|"
        r"strcat|strncpy|fopen|fclose|fgets|fputs|fprintf|sprintf|snprintf|"
        r"memcpy|memset|putchar|getchar)\b"), 0),
    ("p7", "取地址", re.compile(r"(?<![\w)\]&])(?<![+-])&(?![&=])"), 0),
    ("p8", "解引用", re.compile(r"(?<=[=(,\s])\*(?=\s*[A-Za-z_])"), 0),
]

# 干扰项池：词库里除了真答案，还得有几个「看着像」的。
# 全是正确答案的话，词库就退化成「照着填」，练不到判断。
DISTRACTORS = [
    "->", "==", "!=", ">=", "&&", "||", "sizeof", "NULL", "&", "*",
    "%d", "%s", "%c", "%f", "%lf", "printf", "scanf", "malloc", "free",
    "strlen", "strcpy", "fgets", "fopen",
]

# 正文里不该当成「要点」的句子：纯过渡、纯指令
NOISE = re.compile(r"^(?:注意|记住|试试|下面|接下来|以上|所以|因此|例如|比如)[，,：:]?$")


# ---------------------------------------------------------------- 小工具

def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def pnr(seed):
    """确定性伪随机：以字符串为种子，返回 (0,1) 里的下一个数。

    只用来打乱词库顺序。不能用 random —— 每次构建顺序都会变，
    产物就不可复现了，diff 里会多出一堆假改动。
    """
    state = 0x811C9DC5
    for ch in seed:
        state = ((state ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF

    def nxt():
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF
    return nxt


def shuffled(items, seed):
    out = list(items)
    rnd = pnr(seed)
    for i in range(len(out) - 1, 0, -1):
        j = int(rnd() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


def norm_answer(s):
    """判定用的归一化：只去掉首尾空白、把内部连续空白压成一个空格。

    不做大小写折叠——`Null` 不是 `NULL`，`sIzeof` 也不是 `sizeof`，
    这种错本来就该被判错，折叠掉反而是在教错东西。
    """
    return re.sub(r"\s+", " ", (s or "").strip())


# ---------------------------------------------------------------- 顺藤摸瓜

def build_trace(questions, answers):
    """给代码 + 一组输入，问「输出是什么」。

    这道题型考查的是「在脑子里跑一遍」，所以只有输出足够短的用例才配成卡：
    一个循环打成 20 行的三角，谁也跑不出来，那是判题器该干的事。

    每题只出一张卡，挑最短的那个用例。同一道题出三张卡看着热闹，
    实际是同一段逻辑考三遍，还会把 60 道题变成 180 张的同质堆。
    """
    cards = []
    for q in questions:
        code = answers.get(q["id"])
        if not code:
            continue
        best = None
        for idx, c in enumerate(q.get("cases") or []):
            out = c.get("output", "")
            lines = [ln for ln in out.split("\n") if ln.strip()]
            if not lines:
                continue        # 输出为空：答 "什么都不输出" 太容易蒙对
            if len(lines) > TRACE_MAX_LINES or len(out) > TRACE_MAX_CHARS:
                continue
            if best is None or len(out) < len(best[1]["output"]):
                best = (idx, c)
        if not best:
            continue

        idx, case = best
        cards.append({
            "id": "trace-" + q["id"],
            "qid": q["id"],
            "title": q["title"],
            "chapter": q["chapter"],
            "difficulty": q.get("difficulty", "easy"),
            "tags": q.get("tags") or [],
            "code": code,
            "stdin": case.get("input", ""),
            "expect": case["output"],
            "case_index": idx,
            "hint": q.get("hint", ""),
        })
    return cards


# ---------------------------------------------------------------- 填空补洞

def collect_candidates(code):
    """挑出这段代码里「值得挖」的 token。

    返回 [(行号, 起, 止, 文本, 优先级)]，起止是**整段代码里的绝对偏移**。
    这里踩过一次：先按行拿到 m.start() 就当成全局偏移去切片，结果是每一行
    的偏移都从 0 开始算，切出来的分段当然拼不回原代码——而且因为每行的
    「偏移 7」听起来都合理，这个错只有在拼回去校验时才会露头。

    跳过 # 开头的行、注释行。`printf("Hello, C\\n")` 里挖掉 `Hello`
    没有意义，模型也永远猜不到，所以只挖格式符那类有明确规则的东西。
    """
    out = []
    base = 0
    for ln_no, line in enumerate(code.split("\n")):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            base += len(line) + 1
            continue
        # 注释尾巴不挖：`/* 在这里写代码 */` 挖出去毫无信息量
        cut = line.find("//")
        body = line if cut < 0 else line[:cut]

        for prio, _name, rx, grp in BLANK_RULES:
            for m in rx.finditer(body):
                text = m.group(grp)
                if not text:
                    continue
                out.append((ln_no, base + m.start(grp), base + m.end(grp),
                            text, prio))
        base += len(line) + 1

    # 同一位置被多条规则命中时留优先级高的那条（例如 "->" 不会被 p7 抢走）
    out.sort(key=lambda t: (t[1], t[2], t[4]))
    picked = []
    for c in out:
        if picked and c[1] < picked[-1][2]:
            continue
        picked.append(c)
    return picked


def choose_blanks(cands):
    """按优先级挑洞，但先保证每行最多一个，把洞摊开。

    还有一条不写在优先级表里、但同样重要的规则：**同一个 token 最多挖两次**。
    不设这个上限的话，一道三行都 printf 的题会挖出三个 `printf` 洞——
    词库里躺着三个一样的词，学生点三次就完事，这一题等于没考。
    上限设成两次是因为 `%d` 这类确实会连着出现，一次都不许又太苛刻。
    """
    by_prio = sorted(cands, key=lambda t: (t[4], t[0], t[1]))
    lines_used = set()
    text_used = {}
    chosen = []

    def take(limit_lines=True):
        for c in by_prio:
            if c in chosen:
                continue
            if limit_lines and c[0] in lines_used:
                continue
            if text_used.get(c[3], 0) >= 2:
                continue
            chosen.append(c)
            lines_used.add(c[0])
            text_used[c[3]] = text_used.get(c[3], 0) + 1
            if len(chosen) >= MAX_BLANKS:
                return

    # 第一轮：一行一个
    take(limit_lines=True)
    # 第二轮：行数不够就往已有行里加，仍然受「同一 token 最多两次」约束
    if len(chosen) < MIN_BLANKS:
        take(limit_lines=False)
    # 第三轮：还是不够，说明这段代码本身就重复（三行一样的 printf），
    # 放宽 token 上限也要凑够 MIN_BLANKS，否则这题出不成卡。
    if len(chosen) < MIN_BLANKS:
        for c in by_prio:
            if c in chosen:
                continue
            chosen.append(c)
            if len(chosen) >= MIN_BLANKS:
                break

    # 按绝对偏移排，不按行号：切分段用的是偏移，两个顺序一旦不一致，
    # 挖出来的洞就会套到别的 token 上。
    return sorted(chosen, key=lambda t: t[1])


def build_cloze(questions, answers):
    """把参考代码挖成「带洞的代码 + 词库」。

    产出的是分段结构而不是「带占位符的字符串」：
        segments: [{t:"text", v:"for (int i = 0; i < n; i++) {"},
                   {t:"blank", i:0, answer:"printf", hint:"printf"}]
    前端只按段画，不做任何字符串替换。用 __BLANK_0__ 这种占位符再 split
    的写法，一旦代码里真出现类似文本就炸，而且没法给每个洞单独挂状态。
    """
    cards = []
    for q in questions:
        code = answers.get(q["id"])
        if not code:
            continue
        cands = collect_candidates(code)
        if len(cands) < MIN_BLANKS:
            continue
        chosen = choose_blanks(cands)
        if len(chosen) < MIN_BLANKS:
            continue

        # 三个洞全是同一个词，这题就没有判断成分了（词库里点三下同一个
        # chip 就通关）。宁可少一张卡，也不出一张只能练手速的卡。
        answers_list = [c[3] for c in chosen]
        if len(set(answers_list)) < 2:
            continue

        segments = []
        cursor = 0
        for bi, (ln_no, start, end, text, _prio) in enumerate(chosen):
            if start > cursor:
                segments.append({"t": "text", "v": code[cursor:start]})
            segments.append({"t": "blank", "i": bi, "answer": text})
            cursor = end
        if cursor < len(code):
            segments.append({"t": "text", "v": code[cursor:]})
        bank = list(dict.fromkeys(answers_list))
        pool = [d for d in DISTRACTORS if d not in bank]
        # 干扰项按卡片 id 取固定的一小撮，别把 23 个都堆上去——
        # 词库比洞多三倍，就成了「找不同」而不是「回忆」。
        pool = shuffled(pool, "pool:" + q["id"])[:max(2, len(bank))]
        bank = shuffled(bank + pool, "bank:" + q["id"])

        cards.append({
            "id": "cloze-" + q["id"],
            "qid": q["id"],
            "title": q["title"],
            "chapter": q["chapter"],
            "difficulty": q.get("difficulty", "easy"),
            "tags": q.get("tags") or [],
            "code": code,
            "segments": segments,
            "blanks": answers_list,
            "bank": bank,
            "hint": q.get("hint", ""),
        })
    return cards


# ---------------------------------------------------------------- 概念快答

def md_to_points(md, limit=4):
    """从知识点正文里抽几条「说人话」的要点。

    不抽代码块（翻卡背面放代码就成了另一种题），不抽标题，
    不抽长度不足 8 的碎片（那是「注意：」「例如：」这类残句）。
    """
    lines = []
    in_fence = False
    for raw in md.split("\n"):
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line:
            continue
        if line.startswith("#") or line.startswith(">") or line.startswith("|"):
            continue
        if line.startswith(("- ", "* ")) or re.match(r"^\d+\.\s", line):
            line = re.sub(r"^(?:- |\* |\d+\.\s+)", "", line)
        # 行内代码标记去掉，翻卡上不需要等宽强调
        line = line.replace("`", "")
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        for sent in re.split(r"(?<=[。！？])", line):
            sent = sent.strip()
            if len(sent) < 8 or NOISE.match(sent):
                continue
            if sent not in lines:
                lines.append(sent)
        if len(lines) >= limit * 3:
            break

    points = []
    total = 0
    for s in lines:
        # 一条要点最多 96 字：翻卡背面是「扫一眼记住」，不是「读一段文章」
        if len(s) > 96:
            s = s[:94] + "…"
        points.append(s)
        total += len(s)
        if len(points) >= limit:
            break
    return points


def build_flash(courses):
    """知识点 → 正反面卡片。

    正面只放标题（「1.3 变量不是箱子」），背面放正文提炼的要点。
    为什么不自动编一个问题句：编出来的多半是「X 是什么？」，
    和标题重复，翻过来还是那句标题，等于没问。
    """
    cards = []
    for ch in courses["chapters"]:
        for kp in ch["knowledge_points"]:
            points = md_to_points(kp.get("md", ""))
            if len(points) < 2:
                continue
            cards.append({
                "id": "flash-%s-%d" % (ch["id"], len(cards)),
                "chapter": ch["id"],
                "chapter_title": ch["title"],
                "title": kp["title"],
                "kind": kp.get("kind", "concept"),
                "points": points,
            })
    return cards


# ---------------------------------------------------------------- 校验

def validate(doc):
    """构建期自检。

    这几条都属于「不校验也不报错，只是页面看着正常、答案其实是错的」，
    必须在这里拦住。
    """
    problems = []
    seen = set()

    for mode in ("trace", "cloze", "flash"):
        cards = doc["cards"][mode]
        if not cards:
            problems.append("模式 %s 一张卡都没有" % mode)
        for c in cards:
            if c["id"] in seen:
                problems.append("卡片 id 重复：%s" % c["id"])
            seen.add(c["id"])
            if not (1 <= c["chapter"] <= 12):
                problems.append("%s 章号越界：%s" % (c["id"], c["chapter"]))

    for c in doc["cards"]["trace"]:
        if not c["expect"].strip():
            problems.append("%s 的期望输出是空的" % c["id"])
        if "\t" in c["expect"]:
            problems.append("%s 的期望输出含制表符，前端没法对齐" % c["id"])

    for c in doc["cards"]["cloze"]:
        if not (MIN_BLANKS <= len(c["blanks"]) <= MAX_BLANKS):
            problems.append("%s 挖了 %d 个洞，超出 %d–%d"
                            % (c["id"], len(c["blanks"]), MIN_BLANKS, MAX_BLANKS))
        # 分段拼回去必须等于原代码：拼不回去就说明挖空的位置算错了
        joined = "".join(s["v"] if s["t"] == "text" else s["answer"]
                         for s in c["segments"])
        if joined != c["code"]:
            problems.append("%s 的分段拼不回原代码" % c["id"])
        n_blank_seg = sum(1 for s in c["segments"] if s["t"] == "blank")
        if n_blank_seg != len(c["blanks"]):
            problems.append("%s 的洞数(%d)与 blanks 长度(%d)不一致"
                            % (c["id"], n_blank_seg, len(c["blanks"])))
        # 词库必须覆盖全部答案，不然这题无解
        for a in c["blanks"]:
            if a not in c["bank"]:
                problems.append("%s 的词库缺答案 %r" % (c["id"], a))
        # 一个洞都没挖在循环/条件里，说明这段代码太素，出成卡没意义
        if not any(ch in c["code"] for ch in "{}"):
            problems.append("%s 的代码里没有代码块，不像一段真程序" % c["id"])

    for c in doc["cards"]["flash"]:
        if len(c["points"]) < 2:
            problems.append("%s 的要点少于 2 条" % c["id"])
        if any(len(p) < 8 for p in c["points"]):
            problems.append("%s 有残句要点" % c["id"])

    return problems


# ---------------------------------------------------------------- 入口

def build_document():
    """返回经过校验的卡片内容，供构建和首次启动共用。"""
    questions = load("question_bank.json")
    answers = load("answers.json")
    courses = load("courses.json")

    trace = build_trace(questions, answers)
    cloze = build_cloze(questions, answers)
    flash = build_flash(courses)

    doc = {
        "modes": [
            {"id": "trace", "name": "顺藤摸瓜", "short": "读代码说输出",
             "desc": "给一段能跑的代码和它的输入，把输出写出来。"
                     "能算对，说明你真的在脑子里跑起来了。"},
            {"id": "cloze", "name": "填空补洞", "short": "补关键 token",
             "desc": "参考代码挖掉了几处关键写法，从词库里挑回来。"
                     "挖的都是最容易写错的地方：箭头、格式符、比较、sizeof。"},
            {"id": "flash", "name": "概念快答", "short": "翻卡自评",
             "desc": "正面是知识点标题，背面是正文提炼的要点。"
                     "翻完老实说一句「记得 / 模糊 / 忘了」，忘了的会再回来。"},
        ],
        "cards": {"trace": trace, "cloze": cloze, "flash": flash},
    }

    problems = validate(doc)
    if problems:
        raise ValueError("训练卡片校验失败：" + "; ".join(problems[:24]))
    return doc


def main():
    try:
        doc = build_document()
    except ValueError as exc:
        print(str(exc))
        return 1

    trace, cloze, flash = (doc["cards"][mode] for mode in ("trace", "cloze", "flash"))

    stat = "--stat" in sys.argv
    if stat:
        for m in doc["modes"]:
            cards = doc["cards"][m["id"]]
            print("%-6s %-10s %2d 张" % (m["id"], m["name"], len(cards)))
        print()
        for c in doc["cards"]["cloze"][:6]:
            print("  %-12s %d 洞  %s" % (c["id"], len(c["blanks"]),
                                         " ".join(repr(b) for b in c["blanks"])))
        return 0

    if "--check" in sys.argv:
        print("校验通过：%d 张卡（trace %d / cloze %d / flash %d）"
              % (len(trace) + len(cloze) + len(flash),
                 len(trace), len(cloze), len(flash)))
        return 0

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("已写入 %s" % os.path.relpath(OUT, ROOT))
    print("  trace %2d 张 / cloze %2d 张 / flash %2d 张，共 %d 张，%d 字节"
          % (len(trace), len(cloze), len(flash),
             len(trace) + len(cloze) + len(flash), os.path.getsize(OUT)))
    blanks = sum(len(c["blanks"]) for c in cloze)
    print("  挖空合计 %d 处，平均每题 %.1f 处"
          % (blanks, blanks / max(1, len(cloze))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

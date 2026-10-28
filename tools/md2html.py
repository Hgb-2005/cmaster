# -*- coding: utf-8 -*-
"""
极简 Markdown -> HTML 转换器 + C 语法高亮

为什么不用现成库：
  1. 项目要求离线可用（不能依赖 CDN）；
  2. 只需要教学文档用到的那几种语法，多引一个依赖不值得。

支持：# 标题 / 段落 / 列表 / 引用 / 分隔线 / 围栏代码块 / 行内代码 / 粗体 / 链接
"""

import html
import re

# C 关键字（C11）
_C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "restrict", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "union",
    "unsigned", "void", "volatile", "while", "_Bool", "_Complex",
    "NULL", "true", "false", "bool",
}

# 这些严格说是关键字，但视觉上属于「类型」，单独上蓝色。
# 不拆开的话 int 是红的、size_t 是蓝的，同一个概念两种颜色，看着别扭。
_C_TYPE_KEYWORDS = {
    "void", "char", "short", "int", "long", "float", "double",
    "signed", "unsigned", "struct", "union", "enum", "typedef",
    "const", "static", "volatile", "register", "restrict", "inline",
    "extern", "sizeof",
}

_C_TYPES = {
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "size_t", "ssize_t", "FILE", "va_list", "time_t",
}


def highlight_c(code):
    """把 C 代码切成 token 再上色，避免正则互相覆盖。"""
    out = []
    i, n = 0, len(code)

    def wrap(cls, text):
        return '<span class="tok-%s">%s</span>' % (cls, html.escape(text, quote=False))

    while i < n:
        ch = code[i]

        # 预处理指令（整行）
        if ch == "#" and (i == 0 or code[i - 1] == "\n"):
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(wrap("pre", code[i:j]))
            i = j
            continue

        # 行注释
        if code.startswith("//", i):
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(wrap("com", code[i:j]))
            i = j
            continue

        # 块注释
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(wrap("com", code[i:j]))
            i = j
            continue

        # 字符串 / 字符字面量
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(wrap("str", code[i:j]))
            i = j
            continue

        # 数字
        if ch.isascii() and ch.isdigit():
            m = re.match(r"\d[\d.]*([eE][+-]?\d+)?[uUlLfF]*", code[i:])
            tok = m.group(0) if m else ch
            out.append(wrap("num", tok))
            i += len(tok)
            continue

        # 标识符（只认 ASCII，中文等非 ASCII 字符按普通文本输出）
        if ch.isascii() and (ch.isalpha() or ch == "_"):
            m = re.match(r"[A-Za-z_]\w*", code[i:])
            tok = m.group(0) if m else ch
            if tok in _C_TYPES or tok in _C_TYPE_KEYWORDS:
                out.append(wrap("type", tok))
            elif tok in _C_KEYWORDS:
                out.append(wrap("kw", tok))
            else:
                out.append(html.escape(tok, quote=False))
            i += len(tok)
            continue

        out.append(html.escape(ch, quote=False))
        i += 1

    return "".join(out)


def _inline(text):
    """处理行内语法。先占位行内代码，防止内部被二次处理。"""
    slots = []

    def stash(m):
        slots.append('<code class="md-inline">%s</code>'
                     % html.escape(m.group(1), quote=False))
        return "\x00%d\x00" % (len(slots) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank">\1</a>', text)

    def unstash(m):
        return slots[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", unstash, text)


def md2html(md):
    """把一段教学 Markdown 转成 HTML 片段。"""
    if not md:
        return ""
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)
    list_stack = []

    def close_lists(to=0):
        while len(list_stack) > to:
            out.append("</%s>" % list_stack.pop())

    while i < n:
        line = lines[i]

        # 围栏代码块
        m = re.match(r"^```(\w*)\s*$", line)
        if m:
            # 没标语言的块多半是示意图 / 终端输出 / 内存布局，
            # 强行按 C 高亮会把中文标识符和箭头符号搞乱，所以只有
            # 明确写了 c/cpp/h 才上色。
            lang = m.group(1).lower()
            i += 1
            buf = []
            while i < n and not re.match(r"^```\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            code = "\n".join(buf)
            body = highlight_c(code) if lang in ("c", "cpp", "h") else html.escape(code, quote=False)
            out.append('<pre class="md-code" data-lang="%s"><code>%s</code></pre>'
                       % (lang or "text", body))
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2).strip()), level))
            i += 1
            continue

        # 分隔线
        if re.match(r"^---+$", line.strip()):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # 引用
        m = re.match(r"^>\s?(.*)$", line)
        if m:
            close_lists()
            buf = []
            while i < n and re.match(r"^>\s?", lines[i]):
                buf.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % md2html("\n".join(buf)))
            continue

        # 无序列表
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                out.append("<ul>")
                list_stack.append("ul")
            out.append("<li>%s</li>" % _inline(m.group(2)))
            i += 1
            continue

        # 有序列表
        m = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if m:
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                out.append("<ol>")
                list_stack.append("ol")
            out.append("<li>%s</li>" % _inline(m.group(2)))
            i += 1
            continue

        # 空行
        if not line.strip():
            close_lists()
            i += 1
            continue

        # 普通段落（连续非空行合并）
        close_lists()
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|```|>\s?|\s*[-*+]\s|\s*\d+\.\s|---+$)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % _inline("\n".join(buf)))

    close_lists()
    return "\n".join(out)


if __name__ == "__main__":
    demo = """# 标题

这是一段**粗体**和 `代码` 的文字。

```c
#include <stdio.h>
int main(void) {
    printf("hi\\n");  // 注释
    return 0;
}
```

- 列表一
- 列表二
"""
    print(md2html(demo))

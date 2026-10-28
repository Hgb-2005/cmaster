# -*- coding: utf-8 -*-
"""生成 data/memory_demos.json —— /memory 页的内存演示场景。

为什么用脚本算，而不是手写 JSON
------------------------------
每个格子的十六进制字节、每个成员的偏移、结构体里的填充，全都是算出来的。
手写七个场景要敲几百个 hex 字节，改一处类型就得从头重算一遍，而且极容易
写出「结构体里偏移 4 的 int 只占 2 个格子」这种自相矛盾的数据——这种错在
页面上的表现是「数字对不上但看着没毛病」，最难发现。

所以这里把它当一个小编译器用：给类型和值，偏移、填充、字节全自动。

这个脚本刻意不复刻真实编译器的栈布局
------------------------------------
gcc 给局部变量分配地址时会复用、会重排、会为了对齐插空洞，具体取决于
优化等级和变量声明顺序。复刻那套算法只会给人一种「这就是标准」的错觉。
演示需要成立的只有三件事，脚本严格保证这三件：

  1. 栈向低地址长（每个变量都比前一个的地址小）
  2. 每个变量按自己的对齐要求落位（int 不会落在 %4 余 2 的地址上）
  3. 结构体内部按成员对齐插填充，总大小向上取整到最大对齐

前端只负责画，不再做任何布局推算——两处算同一件事，早晚会算出不一样的数。

用法：
    python tools/build_demos.py            # 重新生成
    python tools/build_demos.py --check    # 只校验，不写文件
"""

import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "memory_demos.json")

# 演示统一按 x86-64 算：指针 8 字节。注意 long 在 Windows 上是 4 字节、
# 在 Linux 上是 8 字节，两边不一致的东西不进演示——一个「C 语言学习网站」
# 在同一个页面上给出两种 sizeof(long)，比不给还糟。
WORD = 8
STACK_TOP = 0x7FFD5C2A4F00
FRAME_GAP = 0x40

LAYOUT = {
    "char": (1, 1),
    "int": (4, 4),
    "float": (4, 4),
    "double": (8, 8),
    "ptr": (8, 8),
}

STRUCTS = {
    # 中间填充：c 占 0，i 要求 4 对齐，所以 1..3 是洞
    "struct P": [("c", "char"), ("i", "int")],
    # 末尾填充：i 占 0..3，c 占 4，总大小要取整到 4，所以 5..7 是洞
    "struct Q": [("i", "int"), ("c", "char")],
    # 最夸张的一个：a 占 0，d 要求 8 对齐所以跳到 8，c 在 16，
    # 整体取整到 8 → 24 字节。三个成员实际只用 10 字节。
    "struct R": [("a", "char"), ("b", "double"), ("c", "char")],
}


# ---------------------------------------------------------------- 类型

def _struct_layout(name):
    """按 C 的规则算结构体布局。

    返回 (size, align, fields)，fields 是 [(名字, 偏移, 大小, 对齐)]。
    总大小向上取整到最大成员的对齐——这就是「末尾填充」的来源，
    也是 sizeof(struct Q) == 8 而不是 5 的原因。
    """
    off = 0
    max_align = 1
    fields = []
    for fname, ftype in STRUCTS[name]:
        fsize, falign = type_info(ftype)
        off = (off + falign - 1) & ~(falign - 1)
        fields.append((fname, off, fsize, falign))
        off += fsize
        max_align = max(max_align, falign)
    size = (off + max_align - 1) & ~(max_align - 1)
    return size, max_align, fields


_TYPE_CACHE = {}


def type_info(t):
    """返回 (size, align)。看不懂的类型直接抛错——生成脚本宁可炸，
    也不要在 JSON 里塞一个 size=0 的格子，那会在页面上画成一条线。"""
    if t in _TYPE_CACHE:
        return _TYPE_CACHE[t]
    if t in LAYOUT:
        r = LAYOUT[t]
    elif t.endswith("*"):
        r = (WORD, WORD)
    elif t in STRUCTS:
        size, align, _ = _struct_layout(t)
        r = (size, align)
    elif t.endswith("]"):
        base, _, n = t[:-1].partition("[")
        bsize, balign = type_info(base)
        r = (bsize * int(n), balign)
    else:
        raise SystemExit("不认识的类型：%r（请在 LAYOUT / STRUCTS 里补上）" % t)
    _TYPE_CACHE[t] = r
    return r


def is_array(t):
    return t.endswith("]") and "[" in t


def array_parts(t):
    base, _, n = t[:-1].partition("[")
    return base, int(n)


# ---------------------------------------------------------------- 编码

class Ref(object):
    """指针变量的初值：指向某个格子的某个偏移。

    真实值是「目标地址 + 偏移」，而偏移往往落在某个数组元素或结构体成员
    上（`char *pc = (char *)a;` 之后 `pc++` 指向 a[0] 的第二个字节）。
    所以这里记的是 (格子名, 字节偏移)，定完地址之后再算具体数值。
    """

    def __init__(self, cell, off=0):
        self.cell = cell
        self.off = off


class Null(object):
    """空指针。单独一个类而不是用 None——None 也可以表示「这个场景没给
    初值」，两者在画面上差别很大：空指针要画成 0x0 并且标红。"""


def hex_bytes(bs):
    return ["%02x" % b for b in bs]


def encode_scalar(t, v):
    if t == "char":
        if isinstance(v, str):
            b = v.encode("latin-1")
        else:
            b = bytes([int(v) & 0xFF])
    elif t == "int":
        b = struct.pack("<i", int(v))
    elif t == "float":
        b = struct.pack("<f", float(v))
    elif t == "double":
        b = struct.pack("<d", float(v))
    else:
        raise SystemExit("encode_scalar 收到非标量类型 %r" % t)
    return list(b)


def show_scalar(t, v):
    if t == "char":
        if isinstance(v, str):
            return "'%s'" % (v if v != "\0" else "\\0")
        return "'%s'" % (chr(v) if v else "\\0")
    if t == "int":
        return str(int(v))
    if t in ("float", "double"):
        f = float(v)
        return ("%g" % f)
    return str(v)


def show_addr(a):
    return "0x%x" % a


# ---------------------------------------------------------------- 帧

# 堆上的地址。栈和堆必须看起来是两个互不相干的地方，所以地址段也造得
# 离栈顶很远——0x55… 是典型的 PIE 可执行文件堆区，0x7ffd… 是栈。
HEAP_BASE = 0x55A012C0A2A0

# 帧外面已有的地址（目前只有堆）。指针可以指向它，但帧布局不知道它，
# 所以单独一张表。每个场景开头清空，避免上一个场景的堆地址漏进来。
EXTERN = {}


def build_frame(name, decls, tip, base, freed=False):
    """把一组声明摆成一份帧布局。

    decls 是 [(变量名, 类型, 初值)]，初值可以是标量、list（数组）、
    dict（结构体）、Ref（指针）、Null。
    """
    # ---- 第一遍：只算地址。地址跟初值无关，所以先算完，指针才好取值
    cursor = base
    placed = []
    for dname, dtype, dval in decls:
        size, align = type_info(dtype)
        cursor = (cursor - size) & ~(align - 1)
        placed.append((dname, dtype, dval, cursor, size))

    addrs = dict((p[0], p[3]) for p in placed)
    # (格子名, 偏移) -> 片段 key。指针指向数组中间那个字节时要靠它定位
    piece_keys = {}
    for ename, (eaddr, ekeys) in EXTERN.items():
        addrs[ename] = eaddr
        for off, k in ekeys.items():
            piece_keys[(ename, off)] = k

    cells = []
    for dname, dtype, dval, addr, size in placed:
        cell = {"id": dname, "name": dname, "type": dtype,
                "size": size, "addr": addr, "state": ""}

        if is_array(dtype):
            base_t, n = array_parts(dtype)
            esize, _ = type_info(base_t)
            pieces = []
            vals = dval if isinstance(dval, (list, str)) else []
            for i in range(n):
                if i < len(vals):
                    raw = vals[i]
                else:
                    raw = "\0" if base_t == "char" else 0
                bs = encode_scalar(base_t, raw)
                key = "%s#%d" % (dname, i)
                piece_keys[(dname, i * esize)] = key
                pieces.append({
                    "key": key, "label": "[%d]" % i, "off": i * esize,
                    "size": esize, "bytes": hex_bytes(bs),
                    "value": show_scalar(base_t, raw),
                    "note": "\\0" if (base_t == "char" and raw == "\0") else "",
                })
            cell["pieces"] = pieces
            if base_t == "char":
                shown = "".join(
                    p["value"].strip("'").replace("\\0", "") for p in pieces)
                cell["value"] = '"%s"' % shown if shown else '""'
            else:
                cell["value"] = "{%s}" % ", ".join(
                    p["value"] for p in pieces)

        elif dtype in STRUCTS:
            _, _, fields = _struct_layout(dtype)
            dict_val = dval if isinstance(dval, dict) else {}
            pieces = []
            used = 0
            for fname, foff, fsize, _ in fields:
                # 成员之间露出来的洞就是填充。按偏移顺序补，才能保证
                # 画出来的片段首尾相接、加起来正好等于 sizeof
                if foff > used:
                    pieces.append({
                        "key": "%s#pad%d" % (dname, used), "label": "",
                        "off": used, "size": foff - used,
                        "bytes": ["--"] * (foff - used),
                        "value": "", "pad": True,
                    })
                ftype = dict(STRUCTS[dtype])[fname]
                raw = dict_val.get(fname, 0)
                bs = encode_scalar(ftype, raw)
                key = "%s#%s" % (dname, fname)
                piece_keys[(dname, foff)] = key
                pieces.append({
                    "key": key, "label": fname, "off": foff,
                    "size": fsize, "bytes": hex_bytes(bs),
                    "value": show_scalar(ftype, raw),
                })
                used = foff + fsize
            if size > used:
                pieces.append({
                    "key": "%s#pad%d" % (dname, used), "label": "",
                    "off": used, "size": size - used,
                    "bytes": ["--"] * (size - used),
                    "value": "", "pad": True,
                })
            cell["pieces"] = pieces
            cell["value"] = "{%s}" % ", ".join(
                "%s=%s" % (p["label"], p["value"])
                for p in pieces if not p.get("pad"))

        else:
            cell["_raw"] = (dtype, dval)
        cells.append(cell)

    # ---- 第二遍：指针/标量的字节。这时所有地址都知道了
    for cell in cells:
        if "pieces" in cell:
            continue
        dtype, dval = cell.pop("_raw")
        if isinstance(dval, Ref):
            taddr = addrs[dval.cell] + dval.off
            cell["bytes"] = hex_bytes(int(taddr).to_bytes(WORD, "little"))
            cell["value"] = show_addr(taddr)
            # 指向的格子可能不在本帧（堆）。key 必须带所属块前缀，
            # 否则递归那种「每层都有一个 n」的场景，箭头会指向第一层。
            owner = dval.cell if dval.cell in EXTERN else name
            cell["ptr_to"] = "%s:%s" % (owner, piece_keys.get(
                (dval.cell, dval.off), dval.cell))
        elif isinstance(dval, Null):
            cell["bytes"] = hex_bytes(b"\x00" * WORD)
            cell["value"] = "0x0"
            cell["ptr_to"] = ""
            cell["state"] = "bad"
        else:
            bs = encode_scalar(dtype, dval)
            cell["bytes"] = hex_bytes(bs)
            cell["value"] = show_scalar(dtype, dval)

    used_bytes = sum(c["size"] for c in cells)
    return {
        "id": name,
        "name": "%s()" % name,
        "tip": tip,
        "base": base,
        "size": used_bytes,
        "freed": freed,
        "cells": cells,
    }


# ---------------------------------------------------------------- 场景

class Scene(object):
    def __init__(self, sid, title, tag, level, intro, code):
        self.sid = sid
        self.title = title
        self.tag = tag
        self.level = level
        self.intro = intro
        self.code = code
        self.steps = []
        self._memo = {}
        # 帧名 -> 基址。基址只跟帧的身份有关，跟它这一步有哪些变量无关。
        self._base_of = {}
        # 每个场景从空的「外部地址表」开始。不清的话，堆场景生成的 h#0
        # 会被后面的场景继承，指针校验就会指向一个画不出来的格子。
        EXTERN.clear()

    def heap_ref(self, count, elem=4):
        """登记一块堆内存，让帧里的指针可以指向它。

        必须在建帧之前调用——帧构建时要去 EXTERN 里查地址。
        """
        EXTERN["h"] = (HEAP_BASE, dict(
            (i * elem, "h#%d" % i) for i in range(count)))
        return HEAP_BASE

    def frame(self, name, decls, tip="", freed=False):
        """同一个帧在不同步骤里要拿到同一个地址，否则「下一步」的时候
        整块格子会平移，看起来像变量被搬走了。

        基址按「帧的名字」分配，不能按记忆化条目的序号：同一个 main
        在每步的 decls 都不一样（变量是一个一个加上去的），
        按序号分配会让 main 每步整体下移 0x40——
        结果就是「c 的地址」每步都在变，而这一步根本没碰过 c。
        这个 bug 曾经真的存在，而且页面上看着一切正常，
        只有把三步的地址抄下来对比才发现。"""
        if name not in self._base_of:
            self._base_of[name] = STACK_TOP - FRAME_GAP * len(self._base_of)
        base = self._base_of[name]

        key = (name, repr(decls), tip, freed)
        if key not in self._memo:
            self._memo[key] = build_frame(name, decls, tip, base, freed)
        return self._memo[key]

    def step(self, line, note, frames, heap=None, mark=None, focus=None,
             ghost=""):
        """加一步。

        mark 是 {格子名: 状态}，状态只有四种：
          new  这一步刚出现的（入场动画）
          chg  这一步被改写的（闪一下）
          read 这一步被读取/被指向的（描边高亮）
          bad  有问题的（越界、没有 \0、野指针）
        focus 是 [格子名]，只给「这一步主角是谁」用，不影响状态。

        ghost 是「这一步内存里什么都没有」时右侧那句话。不给的话
        右侧就是一块空白面板，看起来像加载失败——而空白本身
        恰恰是这一步要讲的事（帧划出来了但还没变量 / 帧刚被回收）。

        格子名可以写成 "帧名:格子名" 精确指定。递归场景每层都有一个 n，
        光写 "n" 会同时标亮四层——那种「到处都在闪」的效果等于没标。
        """
        blocks = list(frames) + list(heap or [])

        def qual(key):
            """把短名补成全名。已经带冒号的照原样返回。"""
            if ":" in key:
                return key
            for b in blocks:
                for c in b["cells"]:
                    if c["id"] == key:
                        return "%s:%s" % (b["id"], key)
                    for p in c.get("pieces", []):
                        if p["key"] == key:
                            return "%s:%s" % (b["id"], key)
                if b["id"] == key:
                    return key
            return key

        mark_q = dict((qual(k), v) for k, v in (mark or {}).items())

        def cp(f):
            """复制一份，再贴状态、补全 key。

            不复制的话同一个帧对象会被后面的步骤改掉——memo 缓存的是
            同一个 dict，改一处全变。
            """
            out = dict(f)
            out["cells"] = []
            for c in f["cells"]:
                nc = dict(c)
                if "pieces" in c:
                    nc["pieces"] = []
                    for p in c["pieces"]:
                        np = dict(p)
                        np["key"] = "%s:%s" % (f["id"], p["key"])
                        nc["pieces"].append(np)
                nc["id"] = "%s:%s" % (f["id"], c["id"])
                nc["state"] = mark_q.get(nc["id"], mark_q.get(c["id"],
                                                              c["state"]))
                out["cells"].append(nc)
            # 帧里的空间标记（栈帧 / 堆块）用同一个 id 前缀，
            # 前端就靠这个把「块」和「格子」排到一起去
            return out

        out = {
            "line": line,
            "note": note,
            "frames": [cp(f) for f in frames],
            "heap": [cp(f) for f in (heap or [])],
            "focus": [qual(k) for k in (focus or [])],
        }
        if ghost:
            out["ghost"] = ghost
        self.steps.append(out)

    def to_json(self):
        return {
            "id": self.sid,
            "title": self.title,
            "tag": self.tag,
            "level": self.level,
            "intro": self.intro,
            "code": self.code,
            "steps": self.steps,
        }


# ---------------------------------------------------------------- 七个场景

def scene_stack():
    s = Scene(
        "stack-vars", "局部变量在栈上排开", "栈", 1,
        "同一个函数里的局部变量住在同一块栈帧里。栈向下长，"
        "所以后声明的变量地址更小；每个变量按自己的对齐要求落位，"
        "char 只占 1 字节，int 占 4，double 占 8。",
        [
            "int main(void) {",
            "    char   c = 'A';",
            "    int    i = 42;",
            "    double d = 3.5;",
            "    return 0;",
            "}",
        ])

    s.step(0, "刚进 main，栈帧已经划出来了，但里面还是空的。"
              "栈帧的大小在编译期就定死了，跟走哪条分支无关。", [],
           ghost="main 的栈帧已经划出来了，还没有变量落位")
    s.step(1, "c 是一个 char，只占 1 字节。注意它的地址——"
              "它落在 0x……eff 这种奇数地址上，因为 char 的对齐要求是 1。",
           [s.frame("main", [("c", "char", "A")])],
           mark={"c": "new"}, focus=["c"])
    s.step(2, "i 是 int，要求 4 字节对齐，所以它不会紧贴在 c 下面（那样会落在"
              "0x……efb）。编译器往下取到最近的 4 的倍数 0x……ef8，"
              "中间那 3 个字节就这么空着。",
           [s.frame("main", [("c", "char", "A"), ("i", "int", 42)])],
           mark={"i": "new"}, focus=["i"])
    s.step(3, "d 是 double，要求 8 字节对齐，继续往下落到 0x……ef0。"
              "地址一个比一个小，这就是「栈向下长」。",
           [s.frame("main", [("c", "char", "A"), ("i", "int", 42),
                             ("d", "double", 3.5)])],
           mark={"d": "new"}, focus=["d"])
    # 最后一步不把帧删掉，而是标成「已回收」并把原来的字节留在原处：
    # 这一步要讲的恰恰是「字节还在，只是不归你了」。直接清空画面
    # 会把话说反——看起来像是内存被擦干净了。
    s.step(4, "main 返回，整块帧一起回收——不是逐个变量释放。"
              "这也是为什么返回一个指向局部变量的指针是错的："
              "那 4 个字节还在，但已经不归你了。",
           [s.frame("main", [("c", "char", "A"), ("i", "int", 42),
                             ("d", "double", 3.5)],
                    tip="main 已返回，帧已回收", freed=True)])
    return s


def scene_array():
    s = Scene(
        "array-stride", "数组是一整块连续的", "数组", 1,
        "int arr[5] 不是五个分散的变量，是一整块 20 字节。"
        "元素之间地址正好差 4，一个不多一个不少，这是下标运算能"
        "直接算地址的前提：arr[i] 就是 *(arr + i)。",
        [
            "int main(void) {",
            "    int arr[5] = {10, 20, 30, 40, 50};",
            "    int *p = arr;",
            "    printf(\"%d\\n\", p[3]);",
            "    return 0;",
            "}",
        ])

    s.step(1, "arr 一次占了 20 个字节。下面标的是字节，"
              "每个 int 4 个字节、小端序——所以 10 写成 0a 00 00 00。",
           [s.frame("main", [("arr", "int[5]", [10, 20, 30, 40, 50])])],
           mark={"arr": "new"}, focus=["arr"])
    s.step(2, "p 里存的是 arr 的起始地址。数组名在大多数表达式里会自动"
              "退化成指向首元素的指针，所以 `int *p = arr;` 不用取地址符。",
           [s.frame("main", [("arr", "int[5]", [10, 20, 30, 40, 50]),
                             ("p", "int*", Ref("arr", 0))])],
           mark={"p": "new", "arr#0": "read"}, focus=["p"])
    s.step(3, "p[3] 不查表、不循环，就是「起始地址 + 3 × 4」。"
              "编译器把它算成一次地址加法，然后读那 4 个字节。",
           [s.frame("main", [("arr", "int[5]", [10, 20, 30, 40, 50]),
                             ("p", "int*", Ref("arr", 0))])],
           mark={"arr#3": "read"}, focus=["arr#3"])
    return s


def scene_ptr():
    s = Scene(
        "ptr-basic", "指针只是地址", "指针", 1,
        "指针没有魔法。它是一个 8 字节的变量，里面存的是一串数字——"
        "那个数字是另一个变量的地址。& 是「取出地址」，* 是「按地址访问」。",
        [
            "int main(void) {",
            "    int  x = 42;",
            "    int *p = &x;",
            "    *p = 7;",
            "    return 0;",
            "}",
        ])

    s.step(1, "x 是一个普通的 int，地址 0x……ee8，里面是 42。",
           [s.frame("main", [("x", "int", 42)])],
           mark={"x": "new"}, focus=["x"])
    s.step(2, "p 是 int*，占 8 字节——指针的大小跟它指向什么无关，"
              "只跟机器有关，64 位机器上永远是 8。它存的值就是 x 的地址。",
           [s.frame("main", [("x", "int", 42),
                             ("p", "int*", Ref("x"))])],
           mark={"p": "new", "x": "read"}, focus=["p"])
    s.step(3, "*p = 7 分两步：先读出 p 里的地址，再往那个地址写 7。"
              "改的是 x，p 自己的值一点没变。",
           [s.frame("main", [("x", "int", 42),
                             ("p", "int*", Ref("x"))])],
           mark={"x": "chg"}, focus=["x"])
    s.step(4, "所以 printf 打出来的 x 是 7。指针只是一个「指向」的关系，"
              "它不复制数据，也不拥有数据。",
           [s.frame("main", [("x", "int", 7),
                             ("p", "int*", Ref("x"))])],
           focus=["x"])
    return s


def scene_arith():
    s = Scene(
        "ptr-arith", "指针加一，加的是 sizeof", "指针", 2,
        "同一个地址交给两种类型的指针，加一之后走出的距离完全不同。"
        "p+1 不是「地址加 1」，是「地址加 sizeof(*p)」。这就是为什么"
        "指针必须带类型——不带类型，编译器不知道一步该迈多远。",
        [
            "int main(void) {",
            "    int   a[4] = {1, 2, 3, 4};",
            "    int  *p  = a;",
            "    char *pc = (char *)a;",
            "    p++;      /* 地址 +4 */",
            "    pc++;     /* 地址 +1 */",
            "    return 0;",
            "}",
        ])

    s.step(1, "准备一块 16 字节的 int 数组。",
           [s.frame("main", [("a", "int[4]", [1, 2, 3, 4])])],
           mark={"a": "new"}, focus=["a"])
    s.step(2, "两个指针现在存的是同一个地址——a 的起始位置。"
              "它们唯一的区别是类型：一个说「我指向 int」，一个说「我指向 char」。",
           [s.frame("main", [("a", "int[4]", [1, 2, 3, 4]),
                             ("p", "int*", Ref("a", 0)),
                             ("pc", "char*", Ref("a", 0))])],
           mark={"p": "new", "pc": "new"}, focus=["p", "pc"])
    s.step(3, "p++ 之后地址加 4，正好落在 a[1] 上——它跳过了整个元素。",
           [s.frame("main", [("a", "int[4]", [1, 2, 3, 4]),
                             ("p", "int*", Ref("a", 4)),
                             ("pc", "char*", Ref("a", 0))])],
           mark={"p": "chg"}, focus=["p"])
    s.step(4, "pc++ 之后地址只加 1，落在 a[0] 的第二个字节上。"
              "那个位置没有「一个 char 变量」，它只是 4 个字节里的一块。"
              "从这里读出来的值，取决于 1 这个小端整数怎么切——"
              "这就是类型双关（type punning）为什么容易出错。",
           [s.frame("main", [("a", "int[4]", [1, 2, 3, 4]),
                             ("p", "int*", Ref("a", 4)),
                             ("pc", "char*", Ref("a", 1))])],
           mark={"pc": "chg", "a#1": "read"}, focus=["pc"])
    s.step(5, "回到开头看：两个指针从同一个地方出发，走了一步，"
              "一个到了下一格，一个还在原来那格里。差别全在类型上。",
           [s.frame("main", [("a", "int[4]", [1, 2, 3, 4]),
                             ("p", "int*", Ref("a", 4)),
                             ("pc", "char*", Ref("a", 1))])],
           focus=["p", "pc"])
    return s


def scene_string():
    s = Scene(
        "string-nul", "字符串末尾那个 \\0", "字符串", 2,
        "C 没有字符串类型，只有 char 数组。所有字符串函数都靠末尾那个 0 "
        "判断「到哪算完」。少写一个 0，它们就会一路读下去。",
        [
            "int main(void) {",
            "    char s[4] = \"cat\";",
            "    char t[3] = {'d', 'o', 'g'};",
            "    printf(\"%s\\n\", s);   /* cat */",
            "    printf(\"%s\\n\", t);   /* 灾难 */",
            "    return 0;",
            "}",
        ])

    s.step(1, "写 \"cat\" 三个字符，但 s 是 char[4]——第 4 个字节是编译器"
              "自动补的 '\\0'。不从 1 开始数的原因就在这里："
              "数组长度要留出这个位置。",
           [s.frame("main", [("s", "char[4]", "cat\0")])],
           mark={"s#3": "new"}, focus=["s#3"])
    s.step(2, "t 是手动初始化的 char[3]，三个字符塞满，没有 '\\0'。"
              "它本身没毛病——它只是个 3 字节的数组，不是字符串。",
           [s.frame("main", [("s", "char[4]", "cat\0"),
                             ("t", "char[3]", "dog")])],
           mark={"t": "new"}, focus=["t"])
    s.step(3, "printf(\"%s\") 从 t 的起始位置开始，一个字节一个字节往后读，"
              "直到遇见 0。但 t 后面是什么？不知道——可能是别的变量、"
              "可能是填充、可能是越界的地址。",
           [s.frame("main", [("s", "char[4]", "cat\0"),
                             ("t", "char[3]", "dog")])],
           mark={"t": "bad"}, focus=["t"])
    s.step(4, "所以在 C 里，「这个数组是不是字符串」不看它装了什么字符，"
              "只看末尾有没有 0。strlen／strcpy／printf 全都按这条规则办事。",
           [s.frame("main", [("s", "char[4]", "cat\0"),
                             ("t", "char[3]", "dog")])],
           mark={"t": "bad", "s": "read"}, focus=["s", "t"])
    return s


def scene_struct():
    s = Scene(
        "struct-pad", "结构体为什么变大了", "结构体", 3,
        "sizeof 出来的数往往比成员加起来大。多出来的不是浪费，"
        "是为了让每个成员都落在自己对齐要求的位置上——"
        "CPU 读一个 4 字节的 int，从 4 的倍数地址读是一次，"
        "跨了边界就要读两次再拼起来。",
        [
            "struct P { char c; int  i; };  /* sizeof = 8 */",
            "struct Q { int  i; char c; };  /* sizeof = 8 */",
            "struct R { char a; double b; char c; }; /* 24 */",
            "",
            "int main(void) {",
            "    struct P p = {'A', 7};",
            "    struct Q q = {7, 'A'};",
            "    struct R r = {'A', 2.5, 'B'};",
            "    return 0;",
            "}",
        ])

    s.step(5, "struct P：c 占 1 字节，i 要求 4 对齐，所以 1、2、3 这三格"
              "空出来当填充。总大小 8，实际用了 5。",
           [s.frame("main", [("p", "struct P", {"c": "A", "i": 7})])],
           mark={"p": "new"}, focus=["p"])
    s.step(6, "struct Q 一模一样的东西换个顺序：i 占 0..3，c 落在 4。"
              "这次洞在末尾——因为整个结构体的大小要取整到最大对齐（4），"
              "5 向上取整就是 8。",
           [s.frame("main", [("p", "struct P", {"c": "A", "i": 7}),
                             ("q", "struct Q", {"i": 7, "c": "A"})])],
           mark={"q": "new"}, focus=["q"])
    s.step(7, "struct R 更明显：a 在 0，b 是 double 要求 8 对齐所以跳到 8，"
              "c 在 16，末尾再补到 24。三个成员只用了 10 字节，sizeof 是 24。",
           [s.frame("main", [("p", "struct P", {"c": "A", "i": 7}),
                             ("q", "struct Q", {"i": 7, "c": "A"}),
                             ("r", "struct R", {"a": "A", "b": 2.5,
                                                "c": "B"})])],
           mark={"r": "new"}, focus=["r"])
    s.step(8, "所以调结构体顺序能省内存：把小的放后面、大的放前面，"
              "填充自然就少了。这也是为什么内核里到处按大小降序排成员。"
              "要精确控制就加 #pragma pack(1)，代价是访问变慢。",
           [s.frame("main", [("p", "struct P", {"c": "A", "i": 7}),
                             ("q", "struct Q", {"i": 7, "c": "A"}),
                             ("r", "struct R", {"a": "A", "b": 2.5,
                                                "c": "B"})])],
           focus=["p", "q", "r"])
    return s


def scene_recursion():
    s = Scene(
        "recursion", "递归的栈帧", "函数", 3,
        "每次递归调用都会压一个新的栈帧，参数、局部变量各自独立。"
        "递归写错最常见的原因不是逻辑，是忘了栈有上限——"
        "帧越压越多，最后撞到 guard page 就是段错误。",
        [
            "int fact(int n) {",
            "    if (n <= 1) return 1;",
            "    return n * fact(n - 1);",
            "}",
            "",
            "int main(void) {",
            "    int r = fact(3);",
            "    return 0;",
            "}",
        ])

    s.step(6, "main 里准备调用 fact(3)。这时栈上只有一个帧。",
           [s.frame("main", [("r", "int", 0)], tip="调用者")],
           mark={"r": "new"}, focus=["r"])
    s.step(1, "进入 fact(3)。新的帧压在 main 下面，n = 3。"
              "n > 1，所以要走 fact(n-1)。",
           [s.frame("main", [("r", "int", 0)], tip="调用者"),
            s.frame("fact3", [("n", "int", 3)], tip="fact(3)")],
           mark={"fact3:n": "new"}, focus=["fact3:n"])
    s.step(1, "fact(2) 又是一个新帧。它的 n 跟上一层那个 n 是两个不同的变量，"
              "地址都不一样——这是栈帧最容易被误解的地方。",
           [s.frame("main", [("r", "int", 0)], tip="调用者"),
            s.frame("fact3", [("n", "int", 3)], tip="fact(3)"),
            s.frame("fact2", [("n", "int", 2)], tip="fact(2)")],
           mark={"fact2:n": "new"}, focus=["fact2:n"])
    s.step(1, "fact(1) 命中 n <= 1，直接返回 1。这是递归的出口，"
              "没有它就永远压栈，直到栈溢出。",
           [s.frame("main", [("r", "int", 0)], tip="调用者"),
            s.frame("fact3", [("n", "int", 3)], tip="fact(3)"),
            s.frame("fact2", [("n", "int", 2)], tip="fact(2)"),
            s.frame("fact1", [("n", "int", 1)], tip="fact(1) → return 1")],
           mark={"fact1:n": "read"}, focus=["fact1:n"])
    s.step(2, "开始往回算：fact(1) 的帧销毁，fact(2) 用它的返回值算出 2 × 1 = 2。",
           [s.frame("main", [("r", "int", 0)], tip="调用者"),
            s.frame("fact3", [("n", "int", 3)], tip="fact(3)"),
            s.frame("fact2", [("n", "int", 2), ("ret", "int", 2)],
                    tip="fact(2) → return 2")],
           mark={"fact2:ret": "new"}, focus=["fact2:ret"])
    s.step(2, "再往上：fact(3) 拿到 2，算出 3 × 2 = 6。",
           [s.frame("main", [("r", "int", 0)], tip="调用者"),
            s.frame("fact3", [("n", "int", 3), ("ret", "int", 6)],
                    tip="fact(3) → return 6")],
           mark={"fact3:ret": "new"}, focus=["fact3:ret"])
    s.step(6, "最后回到 main，6 写进 r。所有中间帧都已经销毁，"
              "栈回到刚进 main 时的样子。递归的「深度」就是这一刻栈上有几层。",
           [s.frame("main", [("r", "int", 6)], tip="调用者")],
           mark={"r": "chg"}, focus=["r"])
    return s


def scene_heap():
    s = Scene(
        "heap-malloc", "堆和栈不是一个地方", "内存管理", 4,
        "malloc 要来的内存不在栈上，在堆上。它不随函数返回自动回收，"
        "所以必须自己 free——这是 C 程序内存泄漏的唯一来源。",
        [
            "#include <stdlib.h>",
            "",
            "int main(void) {",
            "    int *p = malloc(3 * sizeof(int));",
            "    p[0] = 1; p[1] = 2; p[2] = 3;",
            "    printf(\"%d\\n\", p[2]);",
            "    free(p);",
            "    return 0;",
            "}",
        ])

    # 先登记堆，再建帧：帧里的 p 要指向 h#0，而建帧时会去 EXTERN 查地址
    s.heap_ref(3)

    s.step(3, "栈上只有一个 8 字节的 p，堆上什么都还没有。",
           [s.frame("main", [("p", "int*", Null())], tip="栈")],
           mark={"p": "new"}, focus=["p"])
    s.step(3, "malloc(12) 在堆上划出 12 个字节，把首地址写进 p。"
              "箭头跨过了两个区域——它指的不是栈上的东西。",
           [s.frame("main", [("p", "int*", Ref("h", 0))], tip="栈")],
           heap=[heap_block("h", 12, [0, 0, 0], "int[3] 未初始化")],
           mark={"p": "new"}, focus=["p"])
    s.step(4, "往堆里写三个 int。注意 p 这个变量本身还在栈上，"
              "变的是它指向的那块堆内存。",
           [s.frame("main", [("p", "int*", Ref("h", 0))], tip="栈")],
           heap=[heap_block("h", 12, [1, 2, 3], "int[3]")],
           mark={"h#0": "new", "h#1": "new", "h#2": "new"}, focus=["h"])
    s.step(6, "free(p) 之后那块内存还归系统，但已经不归你了。"
              "p 里的地址没变——C 不会帮你清零，这就是「悬空指针」。"
              "再 *p 读一次，行为是未定义的。",
           [s.frame("main", [("p", "int*", Null())], tip="栈")],
           heap=[heap_block("h", 12, [1, 2, 3], "已释放", freed=True)],
           mark={"p": "bad"}, focus=["p"])
    return s


def heap_block(bid, size, vals, tip, freed=False):
    """堆上一块 int 数组。故意跟 build_frame 分开写——堆没有对齐填充、
    没有帧边界，混在一个函数里两边都会长出用不到的分支。"""
    pieces = []
    for i, v in enumerate(vals):
        pieces.append({
            "key": "%s#%d" % (bid, i), "label": "[%d]" % i,
            "off": i * 4, "size": 4,
            "bytes": hex_bytes(struct.pack("<i", v)),
            "value": str(v),
        })
    return {
        "id": bid, "name": "堆", "tip": tip, "size": size,
        # 字段名跟栈帧保持一致（base），不要写成 addr：前端按同一个
        # 名字读两种帧的起始地址，两个名字会让堆场景整片空白。
        "base": HEAP_BASE, "freed": freed, "cells": [
            {"id": bid, "name": "*p", "type": "int[3]", "size": size,
             "addr": HEAP_BASE, "state": "bad" if freed else "",
             "value": "{%s}" % ", ".join(str(v) for v in vals),
             "pieces": pieces},
        ],
    }


# ---------------------------------------------------------------- 校验

def validate(doc):
    """生成完立刻自检。这里查的都是「画出来会自相矛盾」的错，
    不是业务错——比如片段加起来不等于 sizeof、地址重叠、指针指向不存在
    的格子。这些错在页面上表现为「看着挺正常但数字对不上」。"""
    problems = []
    ids = set()
    for sc in doc["scenarios"]:
        if sc["id"] in ids:
            problems.append("%s: 场景 id 重复" % sc["id"])
        ids.add(sc["id"])

        # 同一个帧名在同一场景的每一步里必须是同一个基址。
        # 这条也是补上来的：基址曾经按「记忆化序号」分配，于是 main
        # 每加一个变量就整体下移 0x40，c 的地址跟着每步都变——
        # 而每一步单独看都自洽，所以在页面上完全看不出来，
        # 只有把三步的地址并排抄下来才能发现。
        base_of = {}
        for st in sc["steps"]:
            for fr in st["frames"]:
                prev = base_of.setdefault(fr["id"], fr["base"])
                if prev != fr["base"]:
                    problems.append("%s: 帧 %s 的基址从 0x%x 变成 0x%x"
                                    % (sc["id"], fr["id"], prev, fr["base"]))

        ncode = len(sc["code"])
        for si, st in enumerate(sc["steps"]):
            where = "%s/step%d" % (sc["id"], si)
            if not (0 <= st["line"] < ncode):
                problems.append("%s: line=%d 超出代码行数 %d"
                                % (where, st["line"], ncode))
            if not st["note"].strip():
                problems.append("%s: 没有说明文字" % where)

            # 一步什么都不画，右侧就是一块空面板。空白有时候正是要讲的
            # 内容（帧划出来了但还没变量），但必须配一句话，否则看起来
            # 像加载失败。
            if not st["frames"] and not st["heap"] and not st.get("ghost"):
                problems.append("%s: 没有任何帧，也没给 ghost 文案" % where)

            keys = set()
            spans = []

            # 帧本身也要查。这条是补上来的：堆帧当初写的是 addr 而不是
            # base，前端读 base 拿到 undefined，整个堆场景直接空白——
            # 而当时的校验只遍历格子、不看帧，一路全绿就把错的数据写盘了。
            ranges = []
            for fr in st["frames"] + st["heap"]:
                base = fr.get("base")
                if not isinstance(base, int):
                    problems.append("%s: 帧 %s 没有数值型 base（%r）"
                                    % (where, fr["id"], base))
                    continue
                if not fr["cells"]:
                    problems.append("%s: 帧 %s 里一个格子都没有"
                                    % (where, fr["id"]))
                ranges.append((base, fr["size"], fr["id"]))
            ranges.sort()
            for (b1, s1, n1), (b2, s2, n2) in zip(ranges, ranges[1:]):
                if b1 + s1 > b2:
                    problems.append("%s: 帧 %s 与 %s 地址重叠"
                                    % (where, n1, n2))

            for fr in st["frames"] + st["heap"]:
                for c in fr["cells"]:
                    if c["id"] in keys:
                        problems.append("%s: 格子 id 重复 %s"
                                        % (where, c["id"]))
                    keys.add(c["id"])
                    spans.append((c["addr"], c["size"], c["id"]))

                    if "pieces" in c:
                        total = 0
                        for p in c["pieces"]:
                            if p["off"] != total:
                                problems.append(
                                    "%s: %s 的片段 %s 偏移 %d，应该是 %d"
                                    % (where, c["id"], p["key"], p["off"],
                                       total))
                            total += p["size"]
                            keys.add(p["key"])
                        if total != c["size"]:
                            problems.append(
                                "%s: %s 片段合计 %d ≠ sizeof %d"
                                % (where, c["id"], total, c["size"]))
                    # 有片段（数组、结构体）的格子字节挂在片段上，
                    # 只有标量格子自己带 bytes
                    if "pieces" not in c and len(c["bytes"]) != c["size"]:
                        problems.append(
                            "%s: %s 字节数 %d ≠ sizeof %d"
                            % (where, c["id"], len(c["bytes"]), c["size"]))

            # 地址重叠：同一个帧里两个格子不能覆盖同一段字节
            spans.sort()
            for (a1, s1, n1), (a2, s2, n2) in zip(spans, spans[1:]):
                if a1 + s1 > a2 and n1 != n2:
                    problems.append("%s: %s 与 %s 地址重叠"
                                    % (where, n1, n2))

            for fr in st["frames"] + st["heap"]:
                for c in fr["cells"]:
                    tgt = c.get("ptr_to")
                    if tgt and tgt not in keys:
                        problems.append("%s: %s 指向不存在的格子 %r"
                                        % (where, c["id"], tgt))
    return problems


# ---------------------------------------------------------------- 入口

def main():
    scenes = [scene_stack(), scene_array(), scene_ptr(), scene_arith(),
              scene_string(), scene_struct(), scene_recursion(),
              scene_heap()]
    doc = {
        "word": WORD,
        "stack_top": STACK_TOP,
        "scenarios": [s.to_json() for s in scenes],
    }
    problems = validate(doc)
    if problems:
        print("校验失败，共 %d 处：" % len(problems))
        for p in problems[:20]:
            print("  -", p)
        return 1

    total_steps = sum(len(s["steps"]) for s in doc["scenarios"])
    if "--check" in sys.argv:
        print("校验通过：%d 个场景 / %d 步"
              % (len(doc["scenarios"]), total_steps))
        return 0

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("已写入 %s" % os.path.relpath(OUT, ROOT))
    print("  %d 个场景 / %d 步 / %d 字节"
          % (len(doc["scenarios"]), total_steps, os.path.getsize(OUT)))
    for s in doc["scenarios"]:
        print("    %-14s %-2d 步  %s" % (s["id"], len(s["steps"]), s["title"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

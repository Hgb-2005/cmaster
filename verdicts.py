# -*- coding: utf-8 -*-
"""裁决码表：把「AC / WA / TLE」从散落的字符串提升成一处定义的数据。

为什么值得单开一个模块（借 judge0 的做法）：
    judge0 的 API 里每个状态有一个**数字 id**，并且把 Internal Error(13)
    和 Accepted(3) 平级地放在同一张表里。这个安排不是排版问题——它逼着
    实现方回答一个问题：**这次失败是谁的错？**
    WA / TLE / RE / CE 是学生写错了；SE 是我们这台机器坏了。
    混在一个饼图里算「失败率」，等于让判题机自己的故障淹进用户错误里，
    而面板的第一职责恰恰是把这两类分开。学生写错不该让可用性掉数字，
    判题机坏了也不该算成「今天学生水平差」。

现状的问题（这模块存在的直接原因）：
    `"AC"` / `"WA"` 这些字面量在 judge.py、metrics.py、app.py、模板里
    各写了一遍，加一个状态要改六处，漏一处就是「面板上有个饼图扇区
    没有名字」。更要紧的是谁都说不清「哪个状态算系统故障」——只有
    judge.py 里那两处 `status="SE"` 的注释知道。

编号约定：
    数字借自 judge0 的公开状态码（Accepted=3、Wrong Answer=4、
    Time Limit Exceeded=5、Compilation Error=6、Runtime Error=11、
    Internal Error=13），只覆盖本站用得到的这几个。**不是**在实现
    judge0 的协议，只是让它对得上别人的心智模型——面试里说「我的
    裁决码和 judge0 对齐」，比说「我自己编了六个字母」可信。

owner 的三种取值：
    "user"    学生侧原因，站点可用性不该因此下降
    "system"  判题机侧原因，**每一条都要能指向一个待修的部件**
    "none"    没有正确性语义（游乐场的 OK）
"""

# 顺序就是饼图扇区的顺序，也是「越靠前越常见」的近似排序。
ALL = ("AC", "WA", "TLE", "RE", "CE", "SE", "OK")

TABLE = {
    "AC": {
        "code": 3, "name": "通过", "owner": "user", "level": "ok",
        "desc": "全部用例输出与预期一致。",
    },
    "WA": {
        "code": 4, "name": "答案错误", "owner": "user", "level": "warn",
        "desc": "程序正常结束，但输出和预期不符。",
        # 给学生看的一句话，problem 页和错题本共用；写在这里而不是模板里，
        # 是因为同一个状态在三处出现（结果卡、错题本、/ops 的图例）。
        "hint": "先拿题面上的样例手算一遍，再看那个没过用例的输入。",
    },
    "TLE": {
        "code": 5, "name": "超时", "owner": "user", "level": "warn",
        "desc": "到时限还没结束，被强制终止。",
        "hint": "看是「算得慢」还是「在等」——两种超时的改法完全不同。",
    },
    "RE": {
        "code": 11, "name": "运行错误", "owner": "user", "level": "warn",
        "desc": "程序异常退出：返回码非 0，或被信号杀死。",
        "hint": "看退出码翻译出来的那句话，它通常直接指向 bug 类型。",
    },
    "CE": {
        "code": 6, "name": "编译错误", "owner": "user", "level": "warn",
        "desc": "编译器没通过，说明代码还没跑到运行阶段。",
        "hint": "从第一条 error 开始改。后面的报错常常是第一条的连锁反应。",
    },
    "SE": {
        "code": 13, "name": "系统错误", "owner": "system", "level": "bad",
        "desc": "判题机自身的问题，用户代码可能完全正确。",
        # 这一句是给运维看的，不是给学生看的：SE 必须能被定位到具体部件。
        "hint": "沙箱未就绪 / 题目未配用例 / 编译器丢失——去 /admin 看是哪一项。",
    },
    "OK": {
        "code": 3, "name": "运行完成", "owner": "none", "level": "ok",
        "desc": "游乐场跑通，没有正确性含义（和 AC 同一个数字但不该混算）。",
    },
}

# 超时的三种归因。counts 只在 /ops 上统计，不进裁决码——它们都是 TLE，
# 分成三条只是为了让「超时占 15%」这句话能追下去：是题目太难（算得慢），
# 还是题面没讲清输入格式、学生用 scanf 死等（在等）？
#
# 名字是给运维看的，note 是给学生看的。写成两个字段，是因为这两句话
# 如果合成一句，就一定会变成对谁都不痛不痒的那种话。
TLE_KINDS = {
    "cpu": {
        "name": "算得慢",
        "note": "是算法复杂度的问题，不是卡住了。看看有没有多重循环、"
                "重复计算同一个值，或者该用数组前缀和的地方用了两重遍历。",
    },
    "wall": {
        "name": "算等参半",
        "note": "CPU 用掉了一部分、剩下的在等：算了一会儿，中途卡在什么地方。"
                "先看有没有反复的输入输出、加锁、或者读大文件；都没有的话，"
                "按「算得慢」处理。",
    },
    "wait": {
        "name": "在等",
        "note": "CPU 几乎没怎么用，时间花在等待上：可能 scanf 在等一个"
                "永远不会来的输入，可能死锁，也可能调了 sleep。",
    },
    "": {
        "name": "未归因",
        "note": "这次执行没有可信的 CPU 时间（降级路径），不做归因。",
    },
}


def get(status):
    """取一个状态的定义。认不出来返回 SE 的定义——不是猜，是因为
    「未知状态」在运维语义上和「系统错误」同类：需要有人去看一眼。"""
    return TABLE.get(status) or TABLE["SE"]


def name(status):
    return get(status)["name"]


def code(status):
    return get(status)["code"]


def owner(status):
    """返回 "user" / "system" / "none"。metrics 靠这个字段拆分可用性。"""
    return get(status)["owner"]


def is_system(status):
    """这次失败是不是判题机自己的锅。

    注意这里只认 SE。**CE 不算**——编译器在不在是运维的事，但代码写得
    编不过不是；这两件事都表现为「编译阶段失败」，但归属相反。
    把它们混起来的后果是可观测性面板上最坏的一种：一个真实的故障
    （编译器被误删）被淹没在「今天编译失败率 60%，用户真菜」里。
    """
    return owner(status) == "system"


def level(status):
    return get(status)["level"]


def tle_kind(kind):
    """超时归因的展示文案。认不出来按「未归因」处理。"""
    return TLE_KINDS.get(kind or "", TLE_KINDS[""])


def summary():
    """给文档和 /ops 图例用的一张表。顺序稳定，方便写进 README。"""
    return [dict(status=s, **TABLE[s]) for s in ALL]

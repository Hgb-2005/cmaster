# -*- coding: utf-8 -*-
"""题库 1-30 题"""

TPL = r"""#include <stdio.h>

int main(void) {
    /* 在这里写代码 */


    return 0;
}
"""

QUESTIONS_A = [
    # ---------------- 第 1 章：认识 C ----------------
    {
        "id": "c001", "chapter": 1, "difficulty": "easy", "tags": ["输出"],
        "title": "Hello, C",
        "description": "输出一行文本：\n\n```\nHello, C\n```\n\n这是你和编译器打的第一声招呼。注意大小写和逗号后的空格都要一致。",
        "hint": "printf 里别忘了 \\n，它表示换行。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    printf("Hello, C\n");
    return 0;
}""",
        "cases": [{"input": "", "output": "Hello, C"}],
    },
    {
        "id": "c002", "chapter": 1, "difficulty": "easy", "tags": ["输出"],
        "title": "三行自我介绍",
        "description": "按顺序输出三行，内容完全一致：\n\n```\nname: cmaster\nlang: C\nlevel: 1\n```",
        "hint": "三次 printf，或者一次 printf 里放三个 \\n。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    printf("name: cmaster\n");
    printf("lang: C\n");
    printf("level: 1\n");
    return 0;
}""",
        "cases": [{"input": "", "output": "name: cmaster\nlang: C\nlevel: 1"}],
    },
    {
        "id": "c003", "chapter": 1, "difficulty": "easy", "tags": ["输出", "循环"],
        "title": "星号三角形",
        "description": "读入一个整数 n（1≤n≤20），输出一个 n 行的直角三角形，第 i 行有 i 个 `*`。\n\n输入 `4` 时输出：\n\n```\n*\n**\n***\n****\n```",
        "hint": "外层循环控制行，内层循环控制每行的星号数量。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    for (int i = 1; i <= n; i++) {
        for (int j = 0; j < i; j++) putchar('*');
        putchar('\n');
    }
    return 0;
}""",
        "cases": [
            {"input": "4\n", "output": "*\n**\n***\n****"},
            {"input": "1\n", "output": "*"},
            {"input": "3\n", "output": "*\n**\n***"},
        ],
    },
    # ---------------- 第 2 章：类型与 IO ----------------
    {
        "id": "c004", "chapter": 2, "difficulty": "easy", "tags": ["输入输出", "整数"],
        "title": "A + B",
        "description": "读入两个整数 a 和 b，输出它们的和。\n\n输入保证 |a|, |b| ≤ 10^9，和不会超出 `int` 范围。",
        "hint": "`scanf(\"%d %d\", &a, &b)` 一次读两个。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int a, b;
    scanf("%d %d", &a, &b);
    printf("%d\n", a + b);
    return 0;
}""",
        "cases": [
            {"input": "1 2\n", "output": "3"},
            {"input": "-5 3\n", "output": "-2"},
            {"input": "1000000000 1000000000\n", "output": "2000000000"},
        ],
    },
    {
        "id": "c005", "chapter": 2, "difficulty": "easy", "tags": ["浮点", "格式化"],
        "title": "摄氏转华氏",
        "description": "读入一个摄氏温度（整数或小数），输出对应的华氏温度，保留 2 位小数。\n\n公式：`F = C × 9 / 5 + 32`\n\n例如输入 `100`，输出 `212.00`。",
        "hint": "注意 `9 / 5` 是整数除法得 1，要写成 `9.0 / 5` 或 `c * 9 / 5.0`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    double c;
    scanf("%lf", &c);
    printf("%.2f\n", c * 9.0 / 5.0 + 32.0);
    return 0;
}""",
        "cases": [
            {"input": "100\n", "output": "212.00"},
            {"input": "0\n", "output": "32.00"},
            {"input": "-40\n", "output": "-40.00"},
        ],
    },
    {
        "id": "c006", "chapter": 2, "difficulty": "easy", "tags": ["浮点", "常量"],
        "title": "圆的面积与周长",
        "description": "读入半径 r（实数），输出面积和周长，各保留 3 位小数，用空格分隔。\n\nπ 取 `3.1415926`。\n\n输入 `1` 时输出 `3.142 6.283`。",
        "hint": "别用 `scanf(\"%f\")` 读 double。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    const double PI = 3.1415926;
    double r;
    scanf("%lf", &r);
    printf("%.3f %.3f\n", PI * r * r, 2 * PI * r);
    return 0;
}""",
        "cases": [
            {"input": "1\n", "output": "3.142 6.283"},
            {"input": "2\n", "output": "12.566 12.566"},
        ],
    },
    {
        "id": "c007", "chapter": 2, "difficulty": "easy", "tags": ["变量", "交换"],
        "title": "交换两个整数",
        "description": "读入两个整数 a、b，交换它们的值后输出，用空格分隔。\n\n输入 `3 5`，输出 `5 3`。\n\n**要求：必须使用第三个临时变量完成交换。**",
        "hint": "`int t = a; a = b; b = t;`",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int a, b, t;
    scanf("%d %d", &a, &b);
    t = a; a = b; b = t;
    printf("%d %d\n", a, b);
    return 0;
}""",
        "cases": [
            {"input": "3 5\n", "output": "5 3"},
            {"input": "-1 0\n", "output": "0 -1"},
        ],
    },
    {
        "id": "c008", "chapter": 2, "difficulty": "medium", "tags": ["整数除法", "陷阱"],
        "title": "整数与浮点除法",
        "description": "读入两个整数 a、b（b ≠ 0），输出三行：\n\n1. `a / b` 的整数结果\n2. `a % b` 的余数\n3. `a / b` 的浮点结果，保留 4 位小数\n\n输入 `7 2` 输出：\n\n```\n3\n1\n3.5000\n```",
        "hint": "第三行要先把 a 或 b 转成 double，否则会先做整数除法。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int a, b;
    scanf("%d %d", &a, &b);
    printf("%d\n", a / b);
    printf("%d\n", a % b);
    printf("%.4f\n", (double)a / b);
    return 0;
}""",
        "cases": [
            {"input": "7 2\n", "output": "3\n1\n3.5000"},
            {"input": "10 5\n", "output": "2\n0\n2.0000"},
            {"input": "-7 2\n", "output": "-3\n-1\n-3.5000"},
        ],
    },
    {
        "id": "c009", "chapter": 2, "difficulty": "medium", "tags": ["取模", "数字拆分"],
        "title": "各位数字之和",
        "description": "读入一个非负整数 n（n < 10^9），输出它各位数字之和。\n\n输入 `1234`，输出 `10`。\n\n**不要用字符串，用取模运算。**",
        "hint": "`n % 10` 拿到个位，`n / 10` 去掉个位，循环直到 n 为 0。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    long long n;
    scanf("%lld", &n);
    int sum = 0;
    while (n > 0) {
        sum += (int)(n % 10);
        n /= 10;
    }
    printf("%d\n", sum);
    return 0;
}""",
        "cases": [
            {"input": "1234\n", "output": "10"},
            {"input": "0\n", "output": "0"},
            {"input": "999999999\n", "output": "81"},
        ],
    },
    # ---------------- 第 3 章：运算符 ----------------
    {
        "id": "c010", "chapter": 3, "difficulty": "medium", "tags": ["逻辑运算", "分支"],
        "title": "闰年判断",
        "description": "读入年份 y，是闰年输出 `yes`，否则输出 `no`。\n\n规则：能被 4 整除但不能被 100 整除，或者能被 400 整除。",
        "hint": "`(y % 4 == 0 && y % 100 != 0) || y % 400 == 0`",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int y;
    scanf("%d", &y);
    if ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)
        printf("yes\n");
    else
        printf("no\n");
    return 0;
}""",
        "cases": [
            {"input": "2024\n", "output": "yes"},
            {"input": "1900\n", "output": "no"},
            {"input": "2000\n", "output": "yes"},
        ],
    },
    {
        "id": "c011", "chapter": 3, "difficulty": "easy", "tags": ["三元运算符"],
        "title": "三个数的最大值",
        "description": "读入三个整数，输出其中最大的一个。\n\n**要求用三元运算符或 if 实现，不许用数组排序。**",
        "hint": "先求前两个的最大值，再和第三个比。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int a, b, c, m;
    scanf("%d %d %d", &a, &b, &c);
    m = a > b ? a : b;
    m = m > c ? m : c;
    printf("%d\n", m);
    return 0;
}""",
        "cases": [
            {"input": "3 9 5\n", "output": "9"},
            {"input": "-1 -5 -3\n", "output": "-1"},
        ],
    },
    {
        "id": "c012", "chapter": 3, "difficulty": "easy", "tags": ["位运算"],
        "title": "奇偶判断（位运算）",
        "description": "读入整数 n，奇数输出 `odd`，偶数输出 `even`。\n\n**要求用位运算 `&` 判断，不许用 `%`。**",
        "hint": "任何整数的二进制最低位为 1 就是奇数：`n & 1`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    printf("%s\n", (n & 1) ? "odd" : "even");
    return 0;
}""",
        "cases": [
            {"input": "7\n", "output": "odd"},
            {"input": "8\n", "output": "even"},
            {"input": "-3\n", "output": "odd"},
        ],
    },
    {
        "id": "c013", "chapter": 3, "difficulty": "medium", "tags": ["位运算"],
        "title": "判断 2 的幂",
        "description": "读入正整数 n，如果它是 2 的整数次幂输出 `yes`，否则 `no`。\n\n**要求用位运算，时间复杂度 O(1)。**\n\n提示：2 的幂的二进制只有一个 1。",
        "hint": "`n & (n - 1)` 会消掉最低位的 1，结果为 0 就是 2 的幂。别忘了 n > 0。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    unsigned int n;
    scanf("%u", &n);
    printf("%s\n", (n > 0 && (n & (n - 1)) == 0) ? "yes" : "no");
    return 0;
}""",
        "cases": [
            {"input": "1\n", "output": "yes"},
            {"input": "1024\n", "output": "yes"},
            {"input": "6\n", "output": "no"},
            {"input": "0\n", "output": "no"},
        ],
    },
    {
        "id": "c014", "chapter": 3, "difficulty": "easy", "tags": ["取模", "换算"],
        "title": "秒数转时分秒",
        "description": "读入总秒数 t（0 ≤ t < 10^9），输出 `时:分:秒` 格式（不补零）。\n\n输入 `3661` 输出 `1:1:1`。",
        "hint": "先 `t / 3600` 得小时，再对剩下的取模。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    long long t;
    scanf("%lld", &t);
    long long h = t / 3600;
    long long m = t % 3600 / 60;
    long long s = t % 60;
    printf("%lld:%lld:%lld\n", h, m, s);
    return 0;
}""",
        "cases": [
            {"input": "3661\n", "output": "1:1:1"},
            {"input": "0\n", "output": "0:0:0"},
            {"input": "86399\n", "output": "23:59:59"},
        ],
    },
    {
        "id": "c015", "chapter": 3, "difficulty": "easy", "tags": ["三元运算符"],
        "title": "绝对值",
        "description": "读入一个整数，输出它的绝对值。\n\n**不许调用 abs 函数。**",
        "hint": "`x < 0 ? -x : x`",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int x;
    scanf("%d", &x);
    printf("%d\n", x < 0 ? -x : x);
    return 0;
}""",
        "cases": [
            {"input": "-42\n", "output": "42"},
            {"input": "7\n", "output": "7"},
            {"input": "0\n", "output": "0"},
        ],
    },
    # ---------------- 第 4 章：分支与循环 ----------------
    {
        "id": "c016", "chapter": 4, "difficulty": "easy", "tags": ["分支"],
        "title": "成绩等级",
        "description": "读入分数 s（0 ≤ s ≤ 100），按下列规则输出等级：\n\n- s ≥ 90 → `A`\n- 80 ≤ s < 90 → `B`\n- 70 ≤ s < 80 → `C`\n- 60 ≤ s < 70 → `D`\n- s < 60 → `F`",
        "hint": "if-else if 链，从高到低判断。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int s;
    scanf("%d", &s);
    if (s >= 90)      printf("A\n");
    else if (s >= 80) printf("B\n");
    else if (s >= 70) printf("C\n");
    else if (s >= 60) printf("D\n");
    else              printf("F\n");
    return 0;
}""",
        "cases": [
            {"input": "95\n", "output": "A"},
            {"input": "80\n", "output": "B"},
            {"input": "59\n", "output": "F"},
        ],
    },
    {
        "id": "c017", "chapter": 4, "difficulty": "easy", "tags": ["循环", "累加"],
        "title": "1 到 n 求和",
        "description": "读入 n（1 ≤ n ≤ 10^6），输出 1 + 2 + ... + n。\n\n**用循环实现，不要用求和公式。**",
        "hint": "累加变量要用 `long long`，n=10^6 时和约 5×10^11，超过 int。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    long long sum = 0;
    for (int i = 1; i <= n; i++) sum += i;
    printf("%lld\n", sum);
    return 0;
}""",
        "cases": [
            {"input": "10\n", "output": "55"},
            {"input": "1\n", "output": "1"},
            {"input": "1000000\n", "output": "500000500000"},
        ],
    },
    {
        "id": "c018", "chapter": 4, "difficulty": "easy", "tags": ["嵌套循环"],
        "title": "九九乘法表的一行",
        "description": "读入 n（1 ≤ n ≤ 9），输出乘法表的第 n 行，格式为 `n*1=..n*2=..`，用空格分隔，末尾不带空格。\n\n输入 `3` 输出 `3*1=3 3*2=6 3*3=9`。",
        "hint": "注意最后一个元素后面不要多输出空格。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    for (int i = 1; i <= n; i++) {
        if (i > 1) putchar(' ');
        printf("%d*%d=%d", n, i, n * i);
    }
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "3\n", "output": "3*1=3 3*2=6 3*3=9"},
            {"input": "1\n", "output": "1*1=1"},
        ],
    },
    {
        "id": "c019", "chapter": 4, "difficulty": "medium", "tags": ["素数", "循环"],
        "title": "素数判断",
        "description": "读入 n（n ≥ 1），是素数输出 `yes`，否则 `no`。\n\n注意：1 不是素数。",
        "hint": "只需试除到 √n。写成 `i * i <= n` 避免开方函数。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    long long n;
    scanf("%lld", &n);
    if (n < 2) { printf("no\n"); return 0; }
    for (long long i = 2; i * i <= n; i++) {
        if (n % i == 0) { printf("no\n"); return 0; }
    }
    printf("yes\n");
    return 0;
}""",
        "cases": [
            {"input": "7\n", "output": "yes"},
            {"input": "1\n", "output": "no"},
            {"input": "97\n", "output": "yes"},
            {"input": "100\n", "output": "no"},
        ],
    },
    {
        "id": "c020", "chapter": 4, "difficulty": "easy", "tags": ["循环", "continue"],
        "title": "1 到 n 的偶数",
        "description": "读入 n，输出 1 到 n 之间所有偶数，用空格分隔，末尾不带空格。\n\n输入 `6` 输出 `2 4 6`。",
        "hint": "可以用 `i += 2` 直接跳，也可以用 continue 练习。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    int first = 1;
    for (int i = 2; i <= n; i += 2) {
        if (!first) putchar(' ');
        printf("%d", i);
        first = 0;
    }
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "6\n", "output": "2 4 6"},
            {"input": "1\n", "output": ""},
            {"input": "2\n", "output": "2"},
        ],
    },
    {
        "id": "c021", "chapter": 4, "difficulty": "medium", "tags": ["循环", "数字拆分"],
        "title": "整数反转",
        "description": "读入一个整数 n，输出它反转后的结果。\n\n- 输入 `1234` → `4321`\n- 输入 `-120` → `-21`（末尾的 0 消失）\n- 输入 `0` → `0`",
        "hint": "先取绝对值处理，最后补符号。用 `rev * 10 + n % 10` 逐位构建。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    long long n;
    scanf("%lld", &n);
    int neg = n < 0;
    if (neg) n = -n;
    long long rev = 0;
    while (n > 0) {
        rev = rev * 10 + n % 10;
        n /= 10;
    }
    printf("%lld\n", neg ? -rev : rev);
    return 0;
}""",
        "cases": [
            {"input": "1234\n", "output": "4321"},
            {"input": "-120\n", "output": "-21"},
            {"input": "0\n", "output": "0"},
        ],
    },
    {
        "id": "c022", "chapter": 4, "difficulty": "medium", "tags": ["循环", "最大公约数"],
        "title": "最大公约数（辗转相除）",
        "description": "读入两个正整数 a、b，输出它们的最大公约数。\n\n**用辗转相除法（欧几里得算法）实现。**",
        "hint": "`while (b) { t = a % b; a = b; b = t; }` 循环结束时 a 就是答案。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    long long a, b;
    scanf("%lld %lld", &a, &b);
    while (b != 0) {
        long long t = a % b;
        a = b;
        b = t;
    }
    printf("%lld\n", a);
    return 0;
}""",
        "cases": [
            {"input": "12 18\n", "output": "6"},
            {"input": "7 13\n", "output": "1"},
            {"input": "100 100\n", "output": "100"},
        ],
    },
    # ---------------- 第 5 章：数组 ----------------
    {
        "id": "c023", "chapter": 5, "difficulty": "easy", "tags": ["数组", "遍历"],
        "title": "最大值与最小值",
        "description": "第一行输入 n（1 ≤ n ≤ 1000），第二行输入 n 个整数。输出最大值和最小值，用空格分隔。",
        "hint": "初始化时把 min 设成第一个元素，不要设成 0。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    int a[1000];
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    int mx = a[0], mn = a[0];
    for (int i = 1; i < n; i++) {
        if (a[i] > mx) mx = a[i];
        if (a[i] < mn) mn = a[i];
    }
    printf("%d %d\n", mx, mn);
    return 0;
}""",
        "cases": [
            {"input": "5\n3 9 1 7 4\n", "output": "9 1"},
            {"input": "1\n-5\n", "output": "-5 -5"},
            {"input": "4\n-3 -9 -1 -7\n", "output": "-1 -9"},
        ],
    },
    {
        "id": "c024", "chapter": 5, "difficulty": "easy", "tags": ["数组", "逆序"],
        "title": "数组逆序输出",
        "description": "第一行输入 n，第二行输入 n 个整数。按逆序输出这些数，用空格分隔。",
        "hint": "从 n-1 倒着遍历。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n, a[1000];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    for (int i = n - 1; i >= 0; i--) {
        if (i != n - 1) putchar(' ');
        printf("%d", a[i]);
    }
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "5\n1 2 3 4 5\n", "output": "5 4 3 2 1"},
            {"input": "1\n7\n", "output": "7"},
        ],
    },
    {
        "id": "c025", "chapter": 5, "difficulty": "easy", "tags": ["数组", "统计"],
        "title": "求和与平均值",
        "description": "第一行输入 n，第二行输入 n 个整数。输出它们的和与平均值，平均值保留 2 位小数，用空格分隔。",
        "hint": "平均值要用 `(double)sum / n`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n, a[1000];
    scanf("%d", &n);
    long long sum = 0;
    for (int i = 0; i < n; i++) {
        scanf("%d", &a[i]);
        sum += a[i];
    }
    printf("%lld %.2f\n", sum, (double)sum / n);
    return 0;
}""",
        "cases": [
            {"input": "4\n1 2 3 4\n", "output": "10 2.50"},
            {"input": "3\n1 2 4\n", "output": "7 2.33"},
        ],
    },
    {
        "id": "c026", "chapter": 5, "difficulty": "medium", "tags": ["排序", "冒泡"],
        "title": "冒泡排序",
        "description": "第一行输入 n，第二行输入 n 个整数。将它们升序排序后输出，用空格分隔。\n\n**要求手写冒泡排序，不许用 qsort。**",
        "hint": "两层循环，外层控制轮数，内层比较相邻元素。加 swapped 标志提前退出。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n, a[1000];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    for (int i = 0; i < n - 1; i++) {
        int swapped = 0;
        for (int j = 0; j < n - 1 - i; j++) {
            if (a[j] > a[j + 1]) {
                int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
                swapped = 1;
            }
        }
        if (!swapped) break;
    }
    for (int i = 0; i < n; i++) {
        if (i) putchar(' ');
        printf("%d", a[i]);
    }
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "5\n5 1 4 2 8\n", "output": "1 2 4 5 8"},
            {"input": "3\n3 2 1\n", "output": "1 2 3"},
            {"input": "1\n9\n", "output": "9"},
        ],
    },
    {
        "id": "c027", "chapter": 5, "difficulty": "medium", "tags": ["二分查找"],
        "title": "二分查找",
        "description": "第一行输入 n（升序排列），第二行输入 n 个整数，第三行输入待查找的 target。\n\n找到输出它的下标（从 0 开始），找不到输出 `-1`。\n\n**要求用二分查找，O(log n)。**",
        "hint": "`mid = lo + (hi - lo) / 2` 防溢出。循环条件是 `lo <= hi`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n, a[1000], t;
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    scanf("%d", &t);
    int lo = 0, hi = n - 1, ans = -1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (a[mid] == t) { ans = mid; break; }
        if (a[mid] < t) lo = mid + 1;
        else hi = mid - 1;
    }
    printf("%d\n", ans);
    return 0;
}""",
        "cases": [
            {"input": "5\n1 3 5 7 9\n7\n", "output": "3"},
            {"input": "5\n1 3 5 7 9\n2\n", "output": "-1"},
            {"input": "1\n42\n42\n", "output": "0"},
        ],
    },
    {
        "id": "c028", "chapter": 5, "difficulty": "easy", "tags": ["数组", "统计"],
        "title": "正负零统计",
        "description": "第一行输入 n，第二行输入 n 个整数。输出正数、负数、零的个数，用空格分隔。",
        "hint": "三个计数器，一次遍历。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    int pos = 0, neg = 0, zero = 0;
    for (int i = 0; i < n; i++) {
        int x;
        scanf("%d", &x);
        if (x > 0) pos++;
        else if (x < 0) neg++;
        else zero++;
    }
    printf("%d %d %d\n", pos, neg, zero);
    return 0;
}""",
        "cases": [
            {"input": "6\n1 -2 0 3 -4 0\n", "output": "2 2 2"},
            {"input": "3\n1 2 3\n", "output": "3 0 0"},
        ],
    },
    {
        "id": "c029", "chapter": 5, "difficulty": "medium", "tags": ["二维数组", "矩阵"],
        "title": "矩阵转置",
        "description": "第一行输入行数 m 和列数 n（均 ≤ 10），随后 m 行每行 n 个整数，输出转置后的矩阵（n 行，每行 m 个数，空格分隔）。",
        "hint": "新矩阵的第 i 行第 j 列 = 原矩阵的第 j 行第 i 列。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int m, n, a[10][10];
    scanf("%d %d", &m, &n);
    for (int i = 0; i < m; i++)
        for (int j = 0; j < n; j++)
            scanf("%d", &a[i][j]);

    for (int j = 0; j < n; j++) {
        for (int i = 0; i < m; i++) {
            if (i) putchar(' ');
            printf("%d", a[i][j]);
        }
        putchar('\n');
    }
    return 0;
}""",
        "cases": [
            {"input": "2 3\n1 2 3\n4 5 6\n", "output": "1 4\n2 5\n3 6"},
            {"input": "1 1\n7\n", "output": "7"},
        ],
    },
    # ---------------- 第 6 章：函数 ----------------
    {
        "id": "c030", "chapter": 6, "difficulty": "medium", "tags": ["函数", "递归"],
        "title": "阶乘（递归）",
        "description": "读入 n（0 ≤ n ≤ 20），输出 n!。\n\n**要求用递归函数实现。**\n\n注意 20! = 2432902008176640000，需要 `long long`。",
        "hint": "`if (n <= 1) return 1; return n * f(n - 1);`",
        "template": TPL,
        "answer": r"""#include <stdio.h>

long long fact(int n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}

int main(void) {
    int n;
    scanf("%d", &n);
    printf("%lld\n", fact(n));
    return 0;
}""",
        "cases": [
            {"input": "5\n", "output": "120"},
            {"input": "0\n", "output": "1"},
            {"input": "20\n", "output": "2432902008176640000"},
        ],
    },
]

# -*- coding: utf-8 -*-
"""题库 31-60 题"""

TPL = r"""#include <stdio.h>

int main(void) {
    /* 在这里写代码 */


    return 0;
}
"""

QUESTIONS_B = [
    # ---------------- 第 6 章：函数（续） ----------------
    {
        "id": "c031", "chapter": 6, "difficulty": "medium", "tags": ["函数", "递归", "记忆化"],
        "title": "斐波那契数列",
        "description": "读入 n（0 ≤ n ≤ 90），输出第 n 个斐波那契数。\n\n定义：F(0)=0, F(1)=1, F(n)=F(n-1)+F(n-2)。\n\n**提示：朴素递归在 n=45 时就要跑几秒，请用迭代或记忆化。**",
        "hint": "迭代写法：`a=0, b=1; 循环 n 次 { t=a+b; a=b; b=t; }`。注意用 long long。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    long long a = 0, b = 1;
    for (int i = 0; i < n; i++) {
        long long t = a + b;
        a = b;
        b = t;
    }
    printf("%lld\n", a);
    return 0;
}""",
        "cases": [
            {"input": "10\n", "output": "55"},
            {"input": "0\n", "output": "0"},
            {"input": "90\n", "output": "2880067194370816120"},
        ],
    },
    {
        "id": "c032", "chapter": 6, "difficulty": "medium", "tags": ["函数", "递归"],
        "title": "汉诺塔步数",
        "description": "读入盘子数 n（1 ≤ n ≤ 30），输出把 n 个盘子从 A 移到 C 所需的最少步数。\n\n规律：h(n) = 2·h(n-1) + 1。\n\n**要求用递归实现。**",
        "hint": "也可以用公式 2^n - 1，但本题要求递归。注意 2^30 超出 int。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

long long hanoi(int n) {
    if (n == 0) return 0;
    return 2 * hanoi(n - 1) + 1;
}

int main(void) {
    int n;
    scanf("%d", &n);
    printf("%lld\n", hanoi(n));
    return 0;
}""",
        "cases": [
            {"input": "3\n", "output": "7"},
            {"input": "1\n", "output": "1"},
            {"input": "30\n", "output": "1073741823"},
        ],
    },
    {
        "id": "c033", "chapter": 6, "difficulty": "easy", "tags": ["函数", "完全数"],
        "title": "完全数判断",
        "description": "读入 n（2 ≤ n ≤ 10^5），如果 n 等于它所有真因子（不含自身）之和，输出 `yes`，否则 `no`。\n\n例如 6 = 1+2+3，是；28 = 1+2+4+7+14，是。\n\n**要求把因子求和封装成函数。**",
        "hint": "试除到 n/2 即可，或到 √n 时成对累加（更快）。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

long long sum_proper_divisors(int n) {
    long long s = 0;
    for (int i = 1; i * i <= n; i++) {
        if (n % i == 0) {
            s += i;
            if (i != 1 && i != n / i) s += n / i;
        }
    }
    return s;
}

int main(void) {
    int n;
    scanf("%d", &n);
    printf("%s\n", sum_proper_divisors(n) == n ? "yes" : "no");
    return 0;
}""",
        "cases": [
            {"input": "6\n", "output": "yes"},
            {"input": "28\n", "output": "yes"},
            {"input": "10\n", "output": "no"},
        ],
    },
    {
        "id": "c034", "chapter": 6, "difficulty": "medium", "tags": ["函数", "数组参数"],
        "title": "数组排序（函数封装）",
        "description": "第一行输入 n（≤ 100），第二行输入 n 个整数。\n\n**要求写一个 `void sort(int a[], int n)` 函数完成排序**，主函数负责输入输出。输出升序结果。",
        "hint": "数组参数写成 `int a[]` 等价于 `int *a`，函数内 `sizeof(a)` 是 8，长度必须另传。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

void sort(int a[], int n) {
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - 1 - i; j++)
            if (a[j] > a[j + 1]) {
                int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
            }
}

int main(void) {
    int n, a[100];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    sort(a, n);
    for (int i = 0; i < n; i++) {
        if (i) putchar(' ');
        printf("%d", a[i]);
    }
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "5\n5 3 8 1 9\n", "output": "1 3 5 8 9"},
            {"input": "2\n2 1\n", "output": "1 2"},
        ],
    },
    # ---------------- 第 7 章：指针 ----------------
    {
        "id": "c035", "chapter": 7, "difficulty": "medium", "tags": ["指针", "参数"],
        "title": "用指针交换（函数版）",
        "description": "读入两个整数，**写一个 `void swap(int *a, int *b)` 函数**交换它们，然后输出。\n\n这一题的目的：亲眼看到「值传递」为什么会让普通函数交换失败。",
        "hint": "函数内用 `*a`、`*b` 操作，调用时传 `&x`、`&y`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

void swap(int *a, int *b) {
    int t = *a;
    *a = *b;
    *b = t;
}

int main(void) {
    int x, y;
    scanf("%d %d", &x, &y);
    swap(&x, &y);
    printf("%d %d\n", x, y);
    return 0;
}""",
        "cases": [
            {"input": "1 2\n", "output": "2 1"},
            {"input": "-5 5\n", "output": "5 -5"},
        ],
    },
    {
        "id": "c036", "chapter": 7, "difficulty": "medium", "tags": ["指针", "遍历"],
        "title": "用指针求数组和",
        "description": "第一行输入 n，第二行输入 n 个整数。\n\n**要求用指针遍历数组求和**（不许用 `a[i]` 下标写法），输出总和。",
        "hint": "`for (int *p = a; p < a + n; p++) sum += *p;`",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n, a[1000];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    long long sum = 0;
    for (int *p = a; p < a + n; p++) sum += *p;
    printf("%lld\n", sum);
    return 0;
}""",
        "cases": [
            {"input": "4\n1 2 3 4\n", "output": "10"},
            {"input": "1\n100\n", "output": "100"},
        ],
    },
    {
        "id": "c037", "chapter": 7, "difficulty": "medium", "tags": ["指针", "输出参数"],
        "title": "minmax：一次遍历带回两个结果",
        "description": "第一行输入 n，第二行输入 n 个整数。\n\n**要求写 `void minmax(const int *a, int n, int *mn, int *mx)`**，通过指针参数带回最小值和最大值，主函数输出 `最小值 最大值`。",
        "hint": "`const int *a` 表示不修改数组内容。输出参数用 `if (mn) *mn = ...` 更健壮。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

void minmax(const int *a, int n, int *mn, int *mx) {
    int lo = a[0], hi = a[0];
    for (int i = 1; i < n; i++) {
        if (a[i] < lo) lo = a[i];
        if (a[i] > hi) hi = a[i];
    }
    if (mn) *mn = lo;
    if (mx) *mx = hi;
}

int main(void) {
    int n, a[1000], lo, hi;
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    minmax(a, n, &lo, &hi);
    printf("%d %d\n", lo, hi);
    return 0;
}""",
        "cases": [
            {"input": "5\n4 -2 9 0 3\n", "output": "-2 9"},
            {"input": "1\n7\n", "output": "7 7"},
        ],
    },
    # ---------------- 第 8 章：动态内存 ----------------
    {
        "id": "c038", "chapter": 8, "difficulty": "medium", "tags": ["malloc", "动态数组"],
        "title": "动态数组求和",
        "description": "第一行输入 n（1 ≤ n ≤ 10^6），第二行输入 n 个整数。\n\n**要求用 `malloc` 按实际大小分配数组**（不许开固定大小的数组），求和后输出，并正确 `free`。",
        "hint": "`int *a = malloc(n * sizeof(*a)); if (!a) return 1; ... free(a);`",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int n;
    scanf("%d", &n);
    int *a = malloc((size_t)n * sizeof(*a));
    if (!a) return 1;
    long long sum = 0;
    for (int i = 0; i < n; i++) {
        scanf("%d", &a[i]);
        sum += a[i];
    }
    printf("%lld\n", sum);
    free(a);
    return 0;
}""",
        "cases": [
            {"input": "3\n1 2 3\n", "output": "6"},
            {"input": "1\n-100\n", "output": "-100"},
        ],
    },
    {
        "id": "c039", "chapter": 8, "difficulty": "hard", "tags": ["calloc", "动态数组", "逆序"],
        "title": "动态数组原地逆序",
        "description": "第一行输入 n，第二行输入 n 个整数。\n\n**要求用 `calloc` 分配数组**，在原数组上原地逆序（不许开第二个数组），输出结果。",
        "hint": "双指针：`i` 从 0、`j` 从 n-1，交换后向中间收缩，直到 `i >= j`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int n;
    scanf("%d", &n);
    int *a = calloc((size_t)n, sizeof(*a));
    if (!a) return 1;
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);

    for (int i = 0, j = n - 1; i < j; i++, j--) {
        int t = a[i]; a[i] = a[j]; a[j] = t;
    }

    for (int i = 0; i < n; i++) {
        if (i) putchar(' ');
        printf("%d", a[i]);
    }
    putchar('\n');
    free(a);
    return 0;
}""",
        "cases": [
            {"input": "5\n1 2 3 4 5\n", "output": "5 4 3 2 1"},
            {"input": "4\n1 2 3 4\n", "output": "4 3 2 1"},
        ],
    },
    {
        "id": "c040", "chapter": 8, "difficulty": "hard", "tags": ["模拟", "数组"],
        "title": "约瑟夫环",
        "description": "n 个人围成一圈，从第 1 个人开始报数，报到 m 的人出圈，下一个人重新从 1 报数。输出最后剩下的人的编号（1 开始）。\n\n输入 `5 3`，输出 `4`。",
        "hint": "用数组标记出圈状态，模拟过程。或者用递推公式 `f(i) = (f(i-1) + m) % i`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n, m;
    scanf("%d %d", &n, &m);
    int alive[10000] = {0};
    int cnt = n, pos = 0;
    while (cnt > 1) {
        int step = 0;
        while (step < m) {
            if (!alive[pos]) step++;
            if (step == m) break;
            pos = (pos + 1) % n;
        }
        alive[pos] = 1;
        cnt--;
        pos = (pos + 1) % n;
    }
    for (int i = 0; i < n; i++)
        if (!alive[i]) printf("%d\n", i + 1);
    return 0;
}""",
        "cases": [
            {"input": "5 3\n", "output": "4"},
            {"input": "1 1\n", "output": "1"},
            {"input": "7 2\n", "output": "7"},
        ],
    },
    # ---------------- 第 9 章：字符串 ----------------
    {
        "id": "c041", "chapter": 9, "difficulty": "easy", "tags": ["字符串", "指针"],
        "title": "手写 strlen",
        "description": "读入一行不含空格的字符串（长度 ≤ 1000），输出它的长度。\n\n**不许用 `strlen`，自己写一个 `size_t my_strlen(const char *s)`。**",
        "hint": "`while (*s) s++;` 然后返回走过的字符数。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <stddef.h>

size_t my_strlen(const char *s) {
    const char *p = s;
    while (*p) p++;
    return (size_t)(p - s);
}

int main(void) {
    char s[1001];
    scanf("%1000s", s);
    printf("%zu\n", my_strlen(s));
    return 0;
}""",
        "cases": [
            {"input": "hello\n", "output": "5"},
            {"input": "a\n", "output": "1"},
            {"input": "abcdefghij\n", "output": "10"},
        ],
    },
    {
        "id": "c042", "chapter": 9, "difficulty": "medium", "tags": ["字符串", "双指针"],
        "title": "字符串反转",
        "description": "读入一行不含空格的字符串，输出反转后的结果。\n\n**要求原地反转（双指针交换），不许用 strrev。**",
        "hint": "`strlen` 求长度后，i 从 0、j 从 len-1 向中间交换。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <string.h>

int main(void) {
    char s[1001];
    scanf("%1000s", s);
    size_t n = strlen(s);
    for (size_t i = 0, j = n - 1; i < j; i++, j--) {
        char t = s[i]; s[i] = s[j]; s[j] = t;
    }
    printf("%s\n", s);
    return 0;
}""",
        "cases": [
            {"input": "hello\n", "output": "olleh"},
            {"input": "ab\n", "output": "ba"},
            {"input": "x\n", "output": "x"},
        ],
    },
    {
        "id": "c043", "chapter": 9, "difficulty": "medium", "tags": ["字符串", "双指针"],
        "title": "回文串判断",
        "description": "读入一行不含空格的字符串，是回文输出 `yes`，否则 `no`。\n\n**要求用双指针从两端向中间比较，不许先反转再比较。**",
        "hint": "`s[i] != s[j]` 就立刻判负。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <string.h>

int main(void) {
    char s[1001];
    scanf("%1000s", s);
    int n = (int)strlen(s);
    int ok = 1;
    for (int i = 0, j = n - 1; i < j; i++, j--) {
        if (s[i] != s[j]) { ok = 0; break; }
    }
    printf("%s\n", ok ? "yes" : "no");
    return 0;
}""",
        "cases": [
            {"input": "level\n", "output": "yes"},
            {"input": "abc\n", "output": "no"},
            {"input": "abba\n", "output": "yes"},
        ],
    },
    {
        "id": "c044", "chapter": 9, "difficulty": "easy", "tags": ["字符串", "统计"],
        "title": "统计元音字母",
        "description": "读入一行不含空格的字符串，统计其中元音字母 `a e i o u` 的个数（不区分大小写）。",
        "hint": "遍历每个字符，先统一转小写再判断。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    char s[1001];
    scanf("%1000s", s);
    int cnt = 0;
    for (int i = 0; s[i]; i++) {
        char c = s[i];
        if (c >= 'A' && c <= 'Z') c = c - 'A' + 'a';
        if (c=='a'||c=='e'||c=='i'||c=='o'||c=='u') cnt++;
    }
    printf("%d\n", cnt);
    return 0;
}""",
        "cases": [
            {"input": "hello\n", "output": "2"},
            {"input": "AEIOU\n", "output": "5"},
            {"input": "xyz\n", "output": "0"},
        ],
    },
    {
        "id": "c045", "chapter": 9, "difficulty": "medium", "tags": ["字符串", "ASCII"],
        "title": "大小写互换",
        "description": "读入一行不含空格的字符串，把大写字母转小写、小写转大写，其他字符不变，输出结果。\n\n**不许用 toupper / tolower。**",
        "hint": "`'a' - 'A'` 恒等于 32，大小写互转就是加减 32。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    char s[1001];
    scanf("%1000s", s);
    for (int i = 0; s[i]; i++) {
        if (s[i] >= 'a' && s[i] <= 'z') s[i] -= 32;
        else if (s[i] >= 'A' && s[i] <= 'Z') s[i] += 32;
    }
    printf("%s\n", s);
    return 0;
}""",
        "cases": [
            {"input": "Hello\n", "output": "hELLO"},
            {"input": "aBc123\n", "output": "AbC123"},
        ],
    },
    {
        "id": "c046", "chapter": 9, "difficulty": "medium", "tags": ["字符串", "分割"],
        "title": "单词计数",
        "description": "读入一整行（可能含多个空格），统计其中单词的个数。\n\n单词之间由空格分隔，可能有连续空格，行首行尾可能有空格。\n\n输入 `  hello   world  ` 输出 `2`。\n\n**用 `fgets` 读整行，不许用 `scanf(\"%s\")`。**",
        "hint": "核心逻辑：当前字符不是空格、前一个字符是空格（或位置 0）时，单词数 +1。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <string.h>

int main(void) {
    char line[1001];
    if (!fgets(line, sizeof line, stdin)) return 0;
    int cnt = 0, in_word = 0;
    for (int i = 0; line[i]; i++) {
        if (line[i] == ' ' || line[i] == '\n' || line[i] == '\t') {
            in_word = 0;
        } else if (!in_word) {
            in_word = 1;
            cnt++;
        }
    }
    printf("%d\n", cnt);
    return 0;
}""",
        "cases": [
            {"input": "hello world\n", "output": "2"},
            {"input": "  a  b   c  \n", "output": "3"},
            {"input": "one\n", "output": "1"},
        ],
    },
    {
        "id": "c047", "chapter": 9, "difficulty": "medium", "tags": ["字符串", "拼接"],
        "title": "手写字符串拼接",
        "description": "读入两个不含空格的字符串（各自长度 ≤ 100），输出拼接后的结果。\n\n**不许用 `strcat`，自己遍历复制。**",
        "hint": "先 `strlen(a)` 找到结尾位置，再从这里开始复制 b 的每个字符，最后补 `'\\0'`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <string.h>

int main(void) {
    char a[300], b[101];
    scanf("%100s %100s", a, b);
    size_t i = strlen(a);
    for (size_t j = 0; b[j]; j++, i++) a[i] = b[j];
    a[i] = '\0';
    printf("%s\n", a);
    return 0;
}""",
        "cases": [
            {"input": "hello world\n", "output": "helloworld"},
            {"input": "a b\n", "output": "ab"},
        ],
    },
    {
        "id": "c048", "chapter": 9, "difficulty": "hard", "tags": ["字符串", "哈希", "统计"],
        "title": "出现次数最多的字母",
        "description": "读入一行只含小写字母的字符串（可能含空格，长度 ≤ 1000），输出出现次数最多的字母及其次数，用空格分隔。\n\n如果并列，输出字典序最小的那个。",
        "hint": "开一个 `int cnt[26] = {0}` 直接当哈希表，下标就是 `c - 'a'`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    char line[1001];
    if (!fgets(line, sizeof line, stdin)) return 0;
    int cnt[26] = {0};
    for (int i = 0; line[i]; i++) {
        if (line[i] >= 'a' && line[i] <= 'z') cnt[line[i] - 'a']++;
    }
    int best = 0;
    for (int i = 1; i < 26; i++)
        if (cnt[i] > cnt[best]) best = i;
    printf("%c %d\n", 'a' + best, cnt[best]);
    return 0;
}""",
        "cases": [
            {"input": "hello world\n", "output": "l 3"},
            {"input": "aabbbcc\n", "output": "b 3"},
            {"input": "abc\n", "output": "a 1"},
        ],
    },
    # ---------------- 第 10 章：结构体 ----------------
    {
        "id": "c049", "chapter": 10, "difficulty": "medium", "tags": ["结构体", "排序"],
        "title": "学生成绩排序",
        "description": "第一行输入 n（≤ 100），随后 n 行每行是 `姓名 成绩`（姓名不含空格）。\n\n按成绩从高到低输出姓名，成绩相同则按输入顺序。每行一个。",
        "hint": "定义 `struct Student { char name[32]; int score; }`，用稳定排序（冒泡）或记录原始下标。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

typedef struct { char name[32]; int score; } Student;

int main(void) {
    int n;
    Student s[100];
    scanf("%d", &n);
    for (int i = 0; i < n; i++)
        scanf("%31s %d", s[i].name, &s[i].score);

    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - 1 - i; j++)
            if (s[j].score < s[j + 1].score) {
                Student t = s[j]; s[j] = s[j + 1]; s[j + 1] = t;
            }

    for (int i = 0; i < n; i++) printf("%s\n", s[i].name);
    return 0;
}""",
        "cases": [
            {"input": "3\nTom 85\nAmy 92\nBob 78\n", "output": "Amy\nTom\nBob"},
            {"input": "2\na 100\nb 100\n", "output": "a\nb"},
        ],
    },
    {
        "id": "c050", "chapter": 10, "difficulty": "medium", "tags": ["结构体", "指针"],
        "title": "结构体指针求最高分",
        "description": "第一行输入 n，随后 n 行 `学号 成绩`（学号是整数）。\n\n**要求写 `Student *find_top(Student *arr, int n)` 返回最高分学生的指针**，主函数输出该学生的学号。",
        "hint": "函数返回 `&arr[i]`，用 `->` 访问成员。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

typedef struct { int id; int score; } Student;

Student *find_top(Student *arr, int n) {
    Student *best = &arr[0];
    for (int i = 1; i < n; i++)
        if (arr[i].score > best->score) best = &arr[i];
    return best;
}

int main(void) {
    int n;
    Student s[100];
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d %d", &s[i].id, &s[i].score);
    Student *top = find_top(s, n);
    printf("%d\n", top->id);
    return 0;
}""",
        "cases": [
            {"input": "3\n101 88\n102 95\n103 70\n", "output": "102"},
            {"input": "1\n7 60\n", "output": "7"},
        ],
    },
    {
        "id": "c051", "chapter": 10, "difficulty": "medium", "tags": ["结构体", "枚举"],
        "title": "用枚举做简单计算器",
        "description": "读入形如 `3 + 4` 的表达式（运算符为 + - * / 之一，两个整数），输出结果。\n\n除法按整数除法处理，保证除数不为 0。\n\n**要求用枚举表示运算符，用 switch 分派。**",
        "hint": "先读第一个数、运算符字符、第二个数：`scanf(\"%d %c %d\", &a, &op, &b)`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

typedef enum { ADD, SUB, MUL, DIV } Op;

int main(void) {
    int a, b;
    char c;
    scanf("%d %c %d", &a, &c, &b);

    Op op = ADD;
    switch (c) {
        case '+': op = ADD; break;
        case '-': op = SUB; break;
        case '*': op = MUL; break;
        case '/': op = DIV; break;
        default: return 1;
    }

    int r = 0;
    switch (op) {
        case ADD: r = a + b; break;
        case SUB: r = a - b; break;
        case MUL: r = a * b; break;
        case DIV: r = a / b; break;
    }
    printf("%d\n", r);
    return 0;
}""",
        "cases": [
            {"input": "3 + 4\n", "output": "7"},
            {"input": "10 - 3\n", "output": "7"},
            {"input": "7 * 6\n", "output": "42"},
            {"input": "9 / 2\n", "output": "4"},
        ],
    },
    # ---------------- 第 11 章：文件与流 ----------------
    {
        "id": "c052", "chapter": 11, "difficulty": "medium", "tags": ["EOF", "循环读取"],
        "title": "读到 EOF 为止求和",
        "description": "不断读入整数，直到输入结束（EOF），输出所有数之和。\n\n输入可能有多行，也可能一个数都没有（此时输出 0）。",
        "hint": "`while (scanf(\"%d\", &x) == 1)` 是读 EOF 的标准写法，不要用 `while (!feof(stdin))`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    long long sum = 0;
    int x;
    while (scanf("%d", &x) == 1) sum += x;
    printf("%lld\n", sum);
    return 0;
}""",
        "cases": [
            {"input": "1 2 3\n", "output": "6"},
            {"input": "1\n2\n3\n4\n5\n", "output": "15"},
            {"input": "", "output": "0"},
        ],
    },
    {
        "id": "c053", "chapter": 11, "difficulty": "medium", "tags": ["fgets", "行处理"],
        "title": "统计行数",
        "description": "不断读入多行文本（可能含空格），直到 EOF，输出总行数。\n\n最后一行如果没有换行符也要算一行。",
        "hint": "`while (fgets(line, sizeof line, stdin)) lines++;` 这个写法天然处理了最后一行无换行的情况。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    char line[4096];
    int lines = 0;
    while (fgets(line, sizeof line, stdin)) lines++;
    printf("%d\n", lines);
    return 0;
}""",
        "cases": [
            {"input": "a\nb\nc\n", "output": "3"},
            {"input": "only one line", "output": "1"},
            {"input": "", "output": "0"},
        ],
    },
    {
        "id": "c054", "chapter": 11, "difficulty": "hard", "tags": ["解析", "sscanf"],
        "title": "从文本中提取数字",
        "description": "读入一行文本，提取其中所有连续的整数（可能有正负号），输出它们的和。\n\n输入 `abc12de-3f45` 输出 `54`。",
        "hint": "遍历字符，遇到数字或符号就开始累积，用 `strtol` 一次解析更省事。",
        "template": TPL,
        "answer": r"""#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>

int main(void) {
    char line[4096];
    if (!fgets(line, sizeof line, stdin)) return 0;
    long long sum = 0;
    const char *p = line;
    while (*p) {
        if (isdigit((unsigned char)*p) ||
            ((*p == '-' || *p == '+') && isdigit((unsigned char)p[1]))) {
            char *end;
            long v = strtol(p, &end, 10);
            sum += v;
            p = end;
        } else {
            p++;
        }
    }
    printf("%lld\n", sum);
    return 0;
}""",
        "cases": [
            {"input": "abc12de-3f45\n", "output": "54"},
            {"input": "1 2 3\n", "output": "6"},
            {"input": "no digits\n", "output": "0"},
        ],
    },
    # ---------------- 第 12 章：预处理与综合 ----------------
    {
        "id": "c055", "chapter": 12, "difficulty": "easy", "tags": ["宏"],
        "title": "用宏实现最大值",
        "description": "读入三个整数，输出最大值。\n\n**必须定义 `#define MAX(a, b) ...` 宏，并在代码中调用它两次**，不许直接写 if 比较三次。\n\n注意宏的每个参数都要加括号。",
        "hint": "`#define MAX(a,b) ((a) > (b) ? (a) : (b))`",
        "template": TPL,
        "answer": r"""#include <stdio.h>

#define MAX(a, b) ((a) > (b) ? (a) : (b))

int main(void) {
    int a, b, c;
    scanf("%d %d %d", &a, &b, &c);
    printf("%d\n", MAX(MAX(a, b), c));
    return 0;
}""",
        "cases": [
            {"input": "3 9 5\n", "output": "9"},
            {"input": "-1 -2 -3\n", "output": "-1"},
        ],
    },
    {
        "id": "c056", "chapter": 12, "difficulty": "medium", "tags": ["宏", "字符串化"],
        "title": "打印变量名与值",
        "description": "读入一个整数 n，输出两行：\n\n```\nn = <值>\n```\n\n**要求用 `#` 字符串化运算符实现一个 `SHOW(x)` 宏**，不许手写变量名。",
        "hint": "`#define SHOW(x) printf(\"%s = %d\\n\", #x, (x))`，`#x` 把参数变成字符串。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

#define SHOW(x) printf("%s = %d\n", #x, (x))

int main(void) {
    int n;
    scanf("%d", &n);
    SHOW(n);
    return 0;
}""",
        "cases": [
            {"input": "42\n", "output": "n = 42"},
            {"input": "-7\n", "output": "n = -7"},
        ],
    },
    {
        "id": "c057", "chapter": 12, "difficulty": "medium", "tags": ["条件编译"],
        "title": "条件编译：输出模式",
        "description": "同一份代码，通过 `-D` 编译参数控制行为：\n\n- 定义了 `VERBOSE` 时，输出 `[debug] value=<n>`\n- 未定义时，只输出 `<n>`\n\n读入一个整数 n。\n\n**本题的判题环境未定义 VERBOSE，所以实际输出只有数字。** 但你必须用 `#ifdef` 写出两个分支。",
        "hint": "`#ifdef VERBOSE ... #else ... #endif`。用 `scanf` 读 n，两个分支都要输出它。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
#ifdef VERBOSE
    printf("[debug] value=%d\n", n);
#else
    printf("%d\n", n);
#endif
    return 0;
}""",
        "cases": [
            {"input": "5\n", "output": "5"},
            {"input": "-100\n", "output": "-100"},
        ],
    },
    {
        "id": "c058", "chapter": 12, "difficulty": "hard", "tags": ["综合", "去重", "排序"],
        "title": "排序并去重",
        "description": "第一行输入 n（≤ 1000），第二行输入 n 个整数（可能有重复）。\n\n升序输出去重后的结果，用空格分隔。",
        "hint": "先排序，再输出时跳过与前一个相同的元素。也可以边输出边判断。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int a[1000];

int main(void) {
    int n;
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);

    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - 1 - i; j++)
            if (a[j] > a[j + 1]) {
                int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
            }

    int first = 1;
    for (int i = 0; i < n; i++) {
        if (i > 0 && a[i] == a[i - 1]) continue;
        if (!first) putchar(' ');
        printf("%d", a[i]);
        first = 0;
    }
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "6\n3 1 3 2 1 4\n", "output": "1 2 3 4"},
            {"input": "1\n5\n", "output": "5"},
            {"input": "4\n1 1 1 1\n", "output": "1"},
        ],
    },
    {
        "id": "c059", "chapter": 12, "difficulty": "hard", "tags": ["综合", "矩阵乘法"],
        "title": "矩阵乘法",
        "description": "第一行 `m n p`，随后 m 行每行 n 个整数（矩阵 A），再随后 n 行每行 p 个整数（矩阵 B）。\n\n输出 A × B 的结果（m 行，每行 p 个数，空格分隔）。\n\n保证 m, n, p ≤ 20，元素绝对值 ≤ 100。",
        "hint": "三重循环：`C[i][j] = Σ A[i][k] * B[k][j]`。中间那层循环放最里面缓存更友好。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int m, n, p;
    int A[20][20], B[20][20];
    long long C[20][20] = {{0}};

    scanf("%d %d %d", &m, &n, &p);
    for (int i = 0; i < m; i++)
        for (int j = 0; j < n; j++) scanf("%d", &A[i][j]);
    for (int i = 0; i < n; i++)
        for (int j = 0; j < p; j++) scanf("%d", &B[i][j]);

    for (int i = 0; i < m; i++)
        for (int k = 0; k < n; k++)
            for (int j = 0; j < p; j++)
                C[i][j] += (long long)A[i][k] * B[k][j];

    for (int i = 0; i < m; i++) {
        for (int j = 0; j < p; j++) {
            if (j) putchar(' ');
            printf("%lld", C[i][j]);
        }
        putchar('\n');
    }
    return 0;
}""",
        "cases": [
            {"input": "2 2 2\n1 2\n3 4\n5 6\n7 8\n", "output": "19 22\n43 50"},
            {"input": "1 1 1\n2\n3\n", "output": "6"},
        ],
    },
    {
        "id": "c060", "chapter": 12, "difficulty": "hard", "tags": ["综合", "大数"],
        "title": "大数阶乘",
        "description": "读入 n（1 ≤ n ≤ 200），输出 n! 的精确值。\n\n20! 已经超出 `long long`，需要用数组模拟高精度乘法。",
        "hint": "用 `int d[1000]` 逆序存各位（d[0] 是个位），每次 `d[i] * k + carry`。",
        "template": TPL,
        "answer": r"""#include <stdio.h>

int main(void) {
    int n;
    scanf("%d", &n);
    int d[1000] = {0};
    d[0] = 1;
    int len = 1;

    for (int k = 2; k <= n; k++) {
        int carry = 0;
        for (int i = 0; i < len; i++) {
            int v = d[i] * k + carry;
            d[i] = v % 10;
            carry = v / 10;
        }
        while (carry) {
            d[len++] = carry % 10;
            carry /= 10;
        }
    }

    for (int i = len - 1; i >= 0; i--) putchar('0' + d[i]);
    putchar('\n');
    return 0;
}""",
        "cases": [
            {"input": "5\n", "output": "120"},
            {"input": "20\n", "output": "2432902008176640000"},
            {"input": "25\n", "output": "15511210043330985984000000"},
        ],
    },
]

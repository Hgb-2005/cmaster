# -*- coding: utf-8 -*-
"""第 7-12 章：C 的真正门槛（指针、内存、字符串、结构体、文件、工程化）"""

CHAPTERS_B = [
    {
        "id": 7,
        "title": "指针基础",
        "icon": "🎯",
        "stage": "进阶篇 · 内存与抽象",
        "stage_description": "指针不是难，是被讲错了。它只是一个装地址的变量。",
        "description": "学完本章，你应该能够：\n\n- 区分「指针的值」「指针指向的值」「指针自己的地址」三件事；\n- 解释指针与数组的等价关系，以及两者在 sizeof 上的差异；\n- 用指针实现函数的「输出参数」；\n- 说清 const 与指针的三种组合各自的含义。",
        "knowledge_points": [
            {
                "title": "7.1 地址、解引用与三件不同的事",
                "kind": "concept",
                "md": r"""```c
int x = 42;
int *p = &x;      // p 保存 x 的地址

printf("%d\n", x);     // 42
printf("%p\n", (void *)&x);   // x 的地址
printf("%p\n", (void *)p);    // p 的值 —— 和上一行一样
printf("%d\n", *p);    // 42，跟着地址找到 x
printf("%p\n", (void *)&p);   // p 自己的地址 —— 和上面都不同
```

**三个必须分清的东西：**

| 表达式 | 含义 | 类型 |
|---|---|---|
| `p` | 指针里存的地址 | `int *` |
| `*p` | 那个地址上的值 | `int` |
| `&p` | 指针变量自己的地址 | `int **` |

**声明语法的坑：**

```c
int *a, b;      // a 是指针，b 是 int！
int *a, *b;     // 两个都是指针
```

`*` 绑定的是变量名，不是类型。这是 C 声明语法遗留的设计缺陷，建议一行只声明一个变量。

**指针必须初始化：**

```c
int *p;         // 野指针，值不确定
*p = 5;         // UB，可能写坏任意内存
```

安全做法有两条：

```c
int *p = NULL;              // 初始化为空，用前判空
int x; int *p = &x;         // 或者立刻指向一个真实对象
```

**NULL 和 0 的关系：** `NULL` 是宏，展开通常是 `((void*)0)`。所以 `if (p)` 和 `if (p != NULL)` 等价。

**空指针解引用**是最常见的崩溃：`*p` 里 p 是 NULL 时，访问地址 0，操作系统直接终止进程（segmentation fault / 访问冲突）。
""",
            },
            {
                "title": "7.2 指针与数组：等价但有区别",
                "kind": "concept",
                "md": r"""第 5 章说过数组在内存里连续，所以指针加上偏移就能访问元素：

```c
int a[5] = {10, 20, 30, 40, 50};
int *p = a;            // 等价于 &a[0]

printf("%d\n", *p);        // 10
printf("%d\n", *(p + 2));  // 30
printf("%d\n", p[2]);      // 30 —— 指针也能用下标
```

**`p[i]` 就是 `*(p + i)`**，这是语言定义，不是巧合。

**遍历的四种写法，性能完全一样：**

```c
for (int i = 0; i < n; i++) sum += a[i];
for (int i = 0; i < n; i++) sum += *(a + i);
for (int *p = a; p < a + n; p++) sum += *p;
for (int *p = a; p < a + n; ) sum += *p++;
```

**但数组和指针不是一回事：**

```c
int a[5];
int *p = a;

sizeof(a)    // 20（整个数组）
sizeof(p)    // 8（一个指针）

&a + 1       // 跳过 20 字节（整个数组）
p + 1        // 跳过 4 字节（一个元素）
```

**关键区别：**

| | 数组名 | 指针 |
|---|---|---|
| 能否赋值 | 否（`a = p` 编译错误） | 是 |
| `sizeof` | 整个数组大小 | 指针大小 |
| 存哪 | 可能在栈/静态区/堆上 | 是个变量 |
| 能否自增 | 否 | 是 |

数组名是「地址常量」，不是变量，所以不能 `a++`。

**指针算术的类型感知：** `p + 1` 移动的是 `sizeof(*p)` 个字节，不是 1 个字节。

```c
int *ip;      ip + 1  // 后移 4 字节
char *cp;     cp + 1  // 后移 1 字节
double *dp;   dp + 1  // 后移 8 字节
```

这个特性让「遍历任意类型数组」可以写成同一套代码，也是为什么 void* 不能做算术——它不知道元素大小。
""",
            },
            {
                "title": "7.3 指针作为输出参数",
                "kind": "practice",
                "md": r"""C 函数只能返回一个值。想带回多个结果，标准做法是**传指针**。

```c
#include <stdio.h>

// 求数组的最大值和最小值，通过指针带回
void minmax(const int a[], int n, int *out_min, int *out_max) {
    int mn = a[0], mx = a[0];
    for (int i = 1; i < n; i++) {
        if (a[i] < mn) mn = a[i];
        if (a[i] > mx) mx = a[i];
    }
    if (out_min) *out_min = mn;      // 判空，允许调用方不关心某个输出
    if (out_max) *out_max = mx;
}

int main(void) {
    int data[] = {3, 1, 4, 1, 5, 9, 2, 6};
    int lo, hi;
    minmax(data, 8, &lo, &hi);
    printf("min=%d max=%d\n", lo, hi);
    return 0;
}
```

**`if (out_min)` 这个判空是有意设计的**：调用方如果只想要最大值，可以传 `NULL`，函数就跳过写入。这是 C 库的常见约定。

**返回状态码 + 指针输出**，是 C 处理「可能失败的操作」的标准组合：

```c
// 成功返回 0，失败返回 -1，结果从 out 带回
int parse_int(const char *s, int *out) {
    if (!s || !out) return -1;
    char *end = NULL;
    long v = strtol(s, &end, 10);
    if (end == s || *end != '\0') return -1;   // 有非法字符
    *out = (int)v;
    return 0;
}
```

调用处就变成：

```c
int n;
if (parse_int(argv[1], &n) != 0) {
    fprintf(stderr, "参数不是整数\n");
    return 1;
}
```

**这个模式值得背下来**：返回值管「成不成功」，指针参数管「结果是什么」。两者分离，调用方就不可能忘记检查错误。

**对比另一种风格**：直接返回结果，失败返回特殊值（如 `-1`）。问题是 `-1` 可能是合法结果，无法区分。所以带输出的接口更严谨。
""",
            },
            {
                "title": "7.4 const 与指针的三种组合",
                "kind": "pitfall",
                "md": r"""`const` 放在 `*` 的哪一边，含义完全不同。**读法是：从右往左读。**

```c
const int *p;          // p 指向「const int」→ 不能通过 p 改值
int * const p = &x;    // p 是「const 指针」→ 不能改 p 的指向
const int * const p = &x;  // 两者都不能改
```

**第一种：指向常量的指针**（最常见）

```c
void print(const char *s);      // 承诺不修改 s 指向的内容
void sum(const int a[], int n); // 承诺不改数组

const int *p = &x;
*p = 10;      // 编译错误
p = &y;       // 合法，可以换指向
```

**第二种：常量指针**（较少见）

```c
int * const p = &x;
*p = 10;      // 合法
p = &y;       // 编译错误
```

**第三种：两者都不能改**

```c
const int * const p = &x;
```

**记住一句口诀：`const` 在 `*` 左边修饰「值」，在 `*` 右边修饰「指针本身」。**

**为什么值得加 const？**

1. **自文档**：`const char *` 一眼看出这个函数不会改你的字符串
2. **编译器帮你查**：不小心写了 `s[0] = 'x'` 会直接报错
3. **能接住字符串字面量**：`print("hello")` 里 `"hello"` 在只读段，参数不加 `const` 会有警告

**一个常见困惑：**

```c
char *s = "hello";
s[0] = 'H';        // 运行崩溃！字符串字面量在只读内存
```

正确写法：

```c
const char *s = "hello";        // 只读，改会编译报错
char buf[] = "hello";           // 拷贝到栈上，可改
buf[0] = 'H';                   // 合法
```

**函数参数的最佳实践：**

| 参数用途 | 写法 |
|---|---|
| 只读的小数据 | `int x` |
| 只读的字符串 | `const char *s` |
| 只读的大结构体 | `const Big *b` |
| 要修改的 | `Big *b` 或 `int *out` |

> 加 `const` 的成本是零，收益是编译期就能发现的一批 bug。养成习惯。
""",
            },
        ],
    },
    {
        "id": 8,
        "title": "指针进阶与动态内存",
        "icon": "🧠",
        "stage": "进阶篇 · 内存与抽象",
        "stage_description": "从今天起，你的程序自己管内存——这是 C 最强大也最危险的地方。",
        "description": "学完本章，你应该能够：\n\n- 使用二级指针修改指针本身；\n- 用函数指针实现回调与简易分派表；\n- 用 malloc / calloc / realloc / free 管理堆内存；\n- 识别并修复四类内存错误：泄漏、重复释放、悬垂指针、越界。",
        "knowledge_points": [
            {
                "title": "8.1 二级指针：什么时候真的需要它",
                "kind": "concept",
                "md": r"""二级指针就是「指向指针的指针」。

```c
int x = 10;
int *p = &x;      // p 指向 x
int **pp = &p;    // pp 指向 p

printf("%d\n", **pp);    // 10
printf("%p\n", (void *)*pp);   // x 的地址
```

**什么时候真的需要 `**`？** 只有两种情况：

**一、要在函数里修改调用方的指针本身**

```c
// 错：只改了副本
void alloc_wrong(int *p) {
    p = malloc(sizeof(int));
}

// 对：改的是调用方那个指针
void alloc_right(int **p) {
    *p = malloc(sizeof(int));
    if (*p) **p = 42;
}

int main(void) {
    int *p = NULL;
    alloc_right(&p);
    printf("%d\n", *p);    // 42
    free(p);
    return 0;
}
```

**二、二维动态数组**

```c
int rows = 3, cols = 4;
int **m = malloc(rows * sizeof(int *));
for (int i = 0; i < rows; i++)
    m[i] = malloc(cols * sizeof(int));

// 用完必须逐行释放，再释放行指针数组
for (int i = 0; i < rows; i++) free(m[i]);
free(m);
```

**注意这里的释放顺序**：先放掉每行，再放掉「装着行指针的数组」。顺序反了，后面就找不到那些行了，直接泄漏。

**更优的做法：连续内存 + 一维索引**

```c
int *m = malloc(rows * cols * sizeof(int));
#define AT(m, i, j) ((m)[(i) * cols + (j)])
```

一次 malloc 一次 free，缓存友好，也不用担心释放顺序。**真实项目里优先选这个**。

**`int **` 不是二维数组。** 二维数组 `int a[3][4]` 是一整块连续内存，`int **` 是「指针的数组，每个指针再指向一块」。两者内存布局完全不同，不能互相赋值——这是类型系统在保护你。
""",
            },
            {
                "title": "8.2 函数指针与回调",
                "kind": "concept",
                "md": r"""函数也有地址。函数指针存的就是这个地址。

```c
int add(int a, int b) { return a + b; }
int sub(int a, int b) { return a - b; }

int (*op)(int, int) = add;     // 声明语法：返回值 (*名字)(参数类型)
printf("%d\n", op(3, 4));      // 7
op = sub;
printf("%d\n", op(3, 4));      // -1
```

**声明语法为什么这么丑？** 因为 `*` 必须和名字括在一起，否则 `int *op(int, int)` 会被解析成「返回 int* 的函数」。用 `typedef` 可以大幅改善可读性：

```c
typedef int (*binop_t)(int, int);

binop_t op = add;
```

**用途一：回调（把行为当参数传）**

```c
void for_each(int *a, int n, void (*fn)(int *)) {
    for (int i = 0; i < n; i++) fn(&a[i]);
}

void double_it(int *x) { *x *= 2; }

int main(void) {
    int a[] = {1, 2, 3};
    for_each(a, 3, double_it);    // 每个元素翻倍
    return 0;
}
```

标准库的 `qsort` 就是这个模式：排序算法固定，比较规则由你传。

```c
int cmp(const void *a, const void *b) {
    int x = *(const int *)a, y = *(const int *)b;
    return (x > y) - (x < y);      // 安全的比较写法，避免差值溢出
}
qsort(arr, n, sizeof(arr[0]), cmp);
```

**用途二：分派表（用数组代替长 switch）**

```c
typedef struct {
    const char *name;
    int (*fn)(int, int);
} Op;

static Op ops[] = {
    {"add", add},
    {"sub", sub},
};

int apply(const char *name, int a, int b) {
    for (size_t i = 0; i < sizeof(ops) / sizeof(ops[0]); i++)
        if (strcmp(ops[i].name, name) == 0)
            return ops[i].fn(a, b);
    return 0;
}
```

**加一个操作只需往表里加一行**，不用改 `apply`。这是 C 实现「开闭原则」的常用手法，也是事件循环、状态机的底层结构。

**`(x > y) - (x < y)` 这个写法值得记住**：两个比较结果都是 0/1，相减正好得到 -1/0/1，且不会像 `x - y` 那样溢出。
""",
            },
            {
                "title": "8.3 malloc / free 与进程内存布局",
                "kind": "concept",
                "md": r"""先看清内存分区，后面所有行为都有了解释：

```
高地址
┌─────────────┐
│   内核空间   │
├─────────────┤
│    栈 stack  │ ← 局部变量、函数帧，向下增长，通常 1~8MB
├─────────────┤
│      ↓       │
│      ↑       │
│    堆 heap   │ ← malloc 分配，向上增长，可到几 GB
├─────────────┤
│  BSS 段      │ ← 未初始化的全局/静态变量，自动清零
├─────────────┤
│  数据段      │ ← 已初始化的全局/静态变量
├─────────────┤
│  代码段      │ ← 机器码，只读
└─────────────┘
低地址
```

**四个分配函数：**

```c
void *malloc(size_t size);                 // 分配 size 字节，内容未初始化
void *calloc(size_t n, size_t size);       // 分配 n*size，全部清零
void *realloc(void *p, size_t new_size);   // 调整大小
void  free(void *p);                       // 释放
```

**分配必须检查返回值：**

```c
int *a = malloc(n * sizeof(int));
if (a == NULL) {
    fprintf(stderr, "内存不足\n");
    return -1;
}
```

**`sizeof(int)` 不要写成 4。**

```c
int *a = malloc(n * sizeof(int));    // 对
int *a = malloc(n * 4);              // 换平台就错
```

**更稳的写法是 `sizeof(*a)`**，改类型时不用同时改两处：

```c
int *a = malloc(n * sizeof(*a));
```

**`calloc` 的两个好处**：自动清零，且对 `n * size` 做溢出检查。

**`realloc` 的三个细节：**

```c
int *b = realloc(a, new_n * sizeof(int));
if (!b) {
    // 失败时 a 仍然有效，必须自己 free，否则泄漏
    free(a);
    return -1;
}
a = b;    // 成功才覆盖原指针
```

1. 失败返回 NULL，**原内存不会自动释放**
2. 成功时可能搬移了地址，**旧的指针立即失效**
3. `realloc(p, 0)` 的行为在不同实现下不一致，别依赖

**`free(NULL)` 是安全的**（什么都不做），但 `free` 一个非 malloc 返回的地址是 UB。

**堆和栈的速度差异**：栈分配只是移动栈指针，一条指令；malloc 要走分配器算法。所以大量小对象频繁 malloc/free 会很慢，这也是为什么需要内存池。
""",
            },
            {
                "title": "8.4 四类内存错误与排查方法",
                "kind": "pitfall",
                "md": r"""C 的 bug 里，内存错误占了很大比例，而且往往在离现场很远的地方才崩。

**错误一：内存泄漏（忘记 free）**

```c
void leak(void) {
    int *p = malloc(100 * sizeof(int));
    if (some_error) return;      // 提前返回，p 泄漏
    free(p);
}
```

长时间运行的服务里，泄漏会累积到 OOM。**规则：每个 malloc 都要能指出对应的 free 在哪一行。**

**错误二：重复释放（double free）**

```c
int *p = malloc(sizeof(int));
free(p);
free(p);        // UB，分配器元数据被破坏，可能崩溃也可能被利用
```

防御性写法：

```c
free(p);
p = NULL;       // free(NULL) 是安全的，重复调用就没事
```

**错误三：悬垂指针（use after free）**

```c
int *p = malloc(sizeof(int));
*p = 42;
free(p);
printf("%d\n", *p);    // 内存已归还，值可能已经被别人覆盖
```

同样靠 `free` 之后立刻置 NULL 缓解，但**根本解法是理清所有权**：谁分配谁释放，指针在谁手里有效。

**错误四：越界写**

```c
int *a = malloc(10 * sizeof(int));
a[10] = 1;      // 越界，破坏了堆上相邻块的元数据
free(a);        // 这里才崩，离出事地点很远
```

**这就是内存错误最难查的原因：崩溃点和肇事点常常不在一起。**

**排查工具（务必用起来）：**

```c
# AddressSanitizer：抓越界、悬垂、重复释放，精度到字节
gcc -fsanitize=address -g main.c -o main
./main

# UBSan：抓整数溢出、空指针解引用、对齐问题
gcc -fsanitize=undefined -g main.c -o main

# Valgrind（Linux）：抓泄漏，慢但全面
valgrind --leak-check=full ./main

# Windows 上可用 Dr. Memory 或 Visual Studio 的诊断工具
```

**AddressSanitizer 是性价比最高的一个**：编译时加一个参数，运行时自动报出「第几行越界读写了几个字节」，比 printf 调试快十倍。

**四条防御性习惯：**

1. malloc 后立刻判 NULL
2. free 后立刻置 NULL
3. 数组访问前确认下标范围
4. 调试期一律开 `-fsanitize=address`
""",
            },
        ],
    },
    {
        "id": 9,
        "title": "字符串",
        "icon": "🔤",
        "stage": "进阶篇 · 内存与抽象",
        "stage_description": "C 没有字符串类型，只有以 \\0 结尾的字符数组——这解释了一切。",
        "description": "学完本章，你应该能够：\n\n- 解释 C 字符串为什么必须以 `\\0` 结尾；\n- 熟练使用 string.h 的常用函数，并理解它们的边界；\n- 手写 strlen / strcpy / strcmp；\n- 识别并防范缓冲区溢出，写出安全的字符串代码。",
        "knowledge_points": [
            {
                "title": "9.1 C 没有字符串类型",
                "kind": "concept",
                "md": r"""别的语言有 String 类型，C 没有。C 的「字符串」是一个约定：**以 `'\0'` 结尾的 char 数组**。

```c
char s1[] = "hello";      // 6 个字节：h e l l o \0
char s2[10] = "hello";    // 10 个字节，后 5 个是 0
const char *s3 = "hello"; // 指向只读段里的字面量
```

`sizeof(s1)` 是 6，`strlen(s1)` 是 5。**这两个数永远差 1**，是理解 C 字符串的第一把钥匙。

**为什么必须有 `\0`？** 因为函数不知道数组多长，只能一个个往后扫，直到遇到 0 才知道结束。

```c
size_t my_strlen(const char *s) {
    size_t n = 0;
    while (s[n] != '\0') n++;
    return n;
}
```

**推论一：没有 `\0` 的 char 数组不是字符串。**

```c
char buf[3] = {'a', 'b', 'c'};
printf("%s\n", buf);    // 越界读，直到偶然碰到 0 才停
```

**推论二：字符串长度是 O(n) 的。** `strlen` 不存长度，每次都要扫一遍。循环里反复调用是常见的性能陷阱：

```c
for (int i = 0; i < strlen(s); i++)      // O(n²)
for (int i = 0; s[i]; i++)               // O(n)
```

**推论三：字符串不能直接比较和赋值。**

```c
char a[10] = "hi", b[10];
b = a;                 // 编译错误：数组不能赋值
if (a == b) { }        // 比较的是地址，永远不相等
strcpy(b, a);          // 对
if (strcmp(a, b) == 0) // 对
```

**`s[i]` 等价于 `*(s + i)`**，所以遍历字符串有四种写法，最快的还是指针版：

```c
while (*s) { putchar(*s); s++; }
```
""",
            },
            {
                "title": "9.2 string.h 常用函数与边界",
                "kind": "syntax",
                "md": r"""| 函数 | 作用 | 陷阱 |
|---|---|---|
| `strlen(s)` | 长度（不含 `\0`） | O(n)，别放循环条件里 |
| `strcpy(d, s)` | 复制 | **不检查 d 的大小** |
| `strncpy(d, s, n)` | 限长复制 | 不保证 `\0` 结尾 |
| `strcat(d, s)` | 追加 | 不检查容量 |
| `strncat(d, s, n)` | 限长追加 | 长度要算剩余空间，不是总容量 |
| `strcmp(a, b)` | 比较 | 返回 0 才相等，别写成 `== 1` |
| `strchr(s, c)` | 找字符 | 找不到返回 NULL |
| `strstr(s, sub)` | 找子串 | 找不到返回 NULL |
| `strtok(s, sep)` | 分词 | **破坏原字符串**，非线程安全 |
| `sprintf(buf, fmt, ...)` | 格式化到字符串 | **不检查容量** |
| `snprintf(buf, n, fmt, ...)` | 限长格式化 | 返回值是「本应写入的长度」，可能 ≥ n |

**`strncpy` 的坑最值得单说：**

```c
char dst[5];
strncpy(dst, "hello", sizeof(dst));   // dst 里是 h e l l o，没有 '\0'！
printf("%s\n", dst);                  // 越界读
```

因为源串长度 ≥ n 时，`strncpy` 不补 `\0`。安全写法：

```c
dst[sizeof(dst) - 1] = '\0';
```

**`snprintf` 是首选**，它至少不会溢出：

```c
char buf[10];
int need = snprintf(buf, sizeof(buf), "%s-%d", name, id);
if (need >= (int)sizeof(buf)) {
    // 被截断了，need 是"完整写入需要多少字节"
}
```

**`strtok` 的三个限制**（所以工程上都避开它）：

1. 会修改原字符串（把分隔符换成 `\0`）
2. 内部用静态变量记位置，**不可重入、非线程安全**
3. 不能处理空字段

替代品：POSIX 的 `strtok_r`，或者自己遍历。

**一个自测题**：`strcmp("abc", "abd")` 返回什么？答案是负数（不是 -1，标准只保证符号）。所以判断相等必须写 `== 0`，不能写 `== 1`。
""",
            },
            {
                "title": "9.3 手写 strlen / strcpy / strcmp",
                "kind": "practice",
                "md": r"""面试高频题，也是练指针的最好素材。

```c
#include <stddef.h>

/* 返回长度，不含 '\0' */
size_t my_strlen(const char *s) {
    const char *p = s;
    while (*p) p++;
    return (size_t)(p - s);        // 指针相减得到元素个数
}

/* 复制，返回目标首地址（可链式调用） */
char *my_strcpy(char *dst, const char *src) {
    char *ret = dst;
    while ((*dst++ = *src++) != '\0')
        ;
    return ret;
}

/* 比较：返回 <0 / 0 / >0 */
int my_strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

/* 安全版复制：最多拷 n-1 个字符，保证 '\0' 结尾 */
char *my_strncpy_safe(char *dst, const char *src, size_t n) {
    if (n == 0) return dst;
    size_t i = 0;
    while (i < n - 1 && src[i]) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
    return dst;
}
```

**四个值得注意的细节：**

1. **`my_strcmp` 里强转 `unsigned char`**。直接用 `char` 相减，在 char 有符号的平台上，"中文字符"可能是负值，比较结果会错。
2. **`*dst++ = *src++` 的求值顺序**：后置 `++` 在当前表达式里用旧值，序列点（分号）之后才生效。这里没有 UB。
3. **`my_strlen` 用指针相减而不是计数**：`p - s` 是标准定义的两个同数组内指针的差，结果是元素个数，比 `int` 计数更不容易溢出。
4. **返回 `char *` 而不是 `void`**：为了支持 `strcpy(dst2, strcpy(dst1, src))` 这种链式调用。标准库就是这么设计的。

**为什么标准库版本更快？** 因为它用 SIMD 指令一次比较 16/32 字节。手写版本是教学用的，性能上别指望能拼过 glibc。但**理解原理比跑得快重要**——面试问的是你对指针的掌握。

**一个自测练习**：把 `my_strcmp` 改成忽略大小写版本，想想怎么处理 `'A'` 和 `'a'` 的差值。
""",
            },
            {
                "title": "9.4 缓冲区溢出：C 最经典的安全漏洞",
                "kind": "pitfall",
                "md": r"""```c
void vulnerable(void) {
    char buf[8];
    gets(buf);        // 最坏的函数，已从 C11 移除
}
```

输入 20 个字符，后 12 个字节会覆盖栈上 `buf` 之后的内容——包括函数的返回地址。这就是缓冲区溢出，1988 年的莫里斯蠕虫、无数次远程代码执行漏洞都是它。

**即使不用 `gets`，危险的写法到处都是：**

```c
char buf[8];
strcpy(buf, user_input);              // 溢出
sprintf(buf, "%s", user_input);       // 溢出
scanf("%s", buf);                     // 溢出
strcat(buf, user_input);              // 溢出
```

**安全版本：**

```c
char buf[8];
snprintf(buf, sizeof(buf), "%s", user_input);   // 不会溢出
fgets(buf, sizeof(buf), stdin);                 // 读一行，带长度限制
strncpy(...) + 手动补 '\0'
```

**`fgets` 的一个细节**：它会保留换行符，通常需要自己去掉：

```c
if (fgets(buf, sizeof buf, stdin)) {
    size_t n = strlen(buf);
    if (n && buf[n - 1] == '\n') buf[n - 1] = '\0';
}
```

**`fgets` 的另一个细节**：如果一行超过缓冲区大小，它只读前 n-1 个字符，剩下的留在输入缓冲区里，下次读会读到残留。所以做「读完整行」应该循环读并拼接，或者用 `getline`（POSIX）。

**现代编译器的防线：**

```c
# GCC/Clang 默认开启栈保护
gcc -fstack-protector-strong main.c

# 完全禁用（只用于分析，绝不用于生产）
gcc -fno-stack-protector -z execstack main.c
```

栈保护（canary）会在返回地址前插一个随机值，函数返回时检查是否被改。它能让溢出**崩溃得更早、更难利用**，但不阻止溢出本身。

**真正的防御是写对代码：**

| 危险 | 安全 |
|---|---|
| `gets` | `fgets` / `getline` |
| `strcpy` | `snprintf` / `strncpy` + 补 0 |
| `sprintf` | `snprintf` |
| `scanf("%s")` | `scanf("%7s")` 或 `fgets` |
| `strcat` | `snprintf(buf + strlen(buf), remaining, ...)` |

> 一条硬规则：**任何往固定大小缓冲区写数据的操作，都必须显式给出容量。** 没有例外。
""",
            },
        ],
    },
    {
        "id": 10,
        "title": "结构体、联合与枚举",
        "icon": "🏗️",
        "stage": "进阶篇 · 内存与抽象",
        "stage_description": "C 没有类，但结构体 + 函数指针已经能造出绝大部分抽象。",
        "description": "学完本章，你应该能够：\n\n- 定义、初始化、嵌套结构体，并用指针操作它；\n- 解释内存对齐规则，算出一个结构体的真实大小；\n- 用 union 做类型双关、用 enum 提升可读性；\n- 用 typedef 简化声明，并理解它与 #define 的区别。",
        "knowledge_points": [
            {
                "title": "10.1 定义、初始化与嵌套",
                "kind": "syntax",
                "md": r"""```c
struct Point {
    int x;
    int y;
};

struct Point p1 = {1, 2};                      // 按顺序
struct Point p2 = {.y = 5, .x = 3};            // 指定成员（C99，推荐）
struct Point p3 = {0};                         // 全部清零
```

**推荐用指定初始化器 `.x = 3`**：成员顺序变了也不用改代码，且没写的成员自动为 0。

**嵌套：**

```c
struct Rect {
    struct Point lt;      // 左上
    struct Point rb;      // 右下
};

struct Rect r = {{0, 0}, {10, 5}};
printf("%d\n", r.rb.x);   // 10
```

**结构体可以整体赋值、作为参数、作为返回值**（这是数组做不到的）：

```c
struct Point a = {1, 2};
struct Point b = a;            // 逐字节拷贝，合法的
```

**但不建议把大结构体按值传参**，会拷贝整个对象：

```c
void f(struct Rect r);            // 拷贝 16 字节
void g(const struct Rect *r);     // 只传 8 字节，且加 const 保证只读
```

**结构体指针用 `->`：**

```c
struct Point *p = &a;
printf("%d\n", p->x);      // 等价于 (*p).x
printf("%d\n", (*p).x);    // 注意括号，*p.x 会被解析成 *(p.x)
```

`->` 是语法糖，但省掉了括号这个坑，一律用它。

**匿名结构体与位域（了解即可）：**

```c
struct Flags {
    unsigned ready : 1;     // 占 1 位
    unsigned mode  : 3;     // 占 3 位
    unsigned       : 4;     // 未命名的填充位
};
```

位域用于协议头、硬件寄存器这类场景。注意**位域的内存布局是实现定义的**，跨平台传输数据时不能用。
""",
            },
            {
                "title": "10.2 内存对齐：为什么 sizeof 比你算的大",
                "kind": "concept",
                "md": r"""```c
struct A {
    char c;      // 1
    int  i;      // 4
    char d;      // 1
};
```

按成员大小相加是 6，但 `sizeof(struct A)` 通常是 **12**。

**原因是内存对齐**：CPU 访问内存时按字长批量取，如果一个 `int` 跨越了两个 4 字节块，要么访问两次，要么在部分架构上直接异常。所以编译器会在成员之间插入**填充字节**，让每个成员的偏移是它自身大小的整数倍。

```
struct A 的真实布局（x64）：
偏移 0  : c        (1 字节)
偏移 1-3: 填充     (3 字节)
偏移 4-7: i        (4 字节)
偏移 8  : d        (1 字节)
偏移 9-11: 填充    (3 字节)  ← 为了让整个结构体大小是最大成员对齐数(4)的倍数
共 12 字节
```

**验证：**

```c
#include <stdio.h>
#include <stddef.h>

int main(void) {
    struct A { char c; int i; char d; };
    printf("sizeof = %zu\n", sizeof(struct A));           // 12
    printf("c 偏移 %zu\n", offsetof(struct A, c));        // 0
    printf("i 偏移 %zu\n", offsetof(struct A, i));        // 4
    printf("d 偏移 %zu\n", offsetof(struct A, d));        // 8
    return 0;
}
```

**调整顺序可以省内存：**

```c
struct B { int i; char c; char d; };    // sizeof = 8，省了 4 字节
```

把大的成员放前面，小的凑在一起，填充就少了。**一个结构体省 4 字节看着不多，但存一百万个就是 4MB。**

**三个实务结论：**

1. **比较结构体不能用 memcmp**。填充字节的内容不确定，逐字节比较可能不相等。
2. **序列化不能直接 write 整个结构体**。填充字节会一起写进去，接收端可能对齐规则不同而解析错位。要用显式的字段序列化。
3. **`#pragma pack(1)` 可以取消填充**，用于协议解析。但代价是访问变慢，甚至在某些 ARM 上崩溃。只在确实需要时用，用完立刻 `#pragma pack()` 恢复。
""",
            },
            {
                "title": "10.3 union 与 enum",
                "kind": "concept",
                "md": r"""**union：所有成员共享同一块内存。**

```c
union Value {
    int   i;
    float f;
    char  bytes[4];
};

union Value v;
v.i = 0x41424344;
printf("%zu\n", sizeof(v));     // 4 —— 不是 12，成员重叠
printf("%c\n", v.bytes[0]);     // 取决于字节序：小端是 'D'
```

**用途一：带标签的变体（tagged union）**，这是 C 实现「多态数据」的标准手法：

```c
enum ValueKind { V_INT, V_FLOAT, V_STR };

struct Value {
    enum ValueKind kind;
    union {
        int   i;
        float f;
        char *s;
    } as;
};

void print_value(const struct Value *v) {
    switch (v->kind) {
        case V_INT:   printf("%d\n", v->as.i); break;
        case V_FLOAT: printf("%f\n", v->as.f); break;
        case V_STR:   printf("%s\n", v->as.s); break;
    }
}
```

`kind` 字段是必须的——union 自己不记录「当前哪个成员有效」，搞错就是 UB。

**用途二：类型双关（看同一个数的不同解释）**

```c
union { float f; unsigned u; } pun;
pun.f = 1.0f;
printf("%08X\n", pun.u);    // 3F800000，IEEE 754 的 1.0
```

C99 起用 union 做类型双关是合法的（读的成员与写的成员不同也算实现定义，但主流编译器支持）。更可移植的写法是 `memcpy`。

**enum：给整数起名字。**

```c
enum Color { RED, GREEN, BLUE };          // 0, 1, 2
enum Status { OK = 200, NotFound = 404 }; // 显式指定
```

**enum 就是 int**，主要价值是可读性和编译器的检查：

```c
if (c == REED) { }     // 拼错，编译器警告（虽然 C 只当未声明标识符）
```

**`typedef enum` 的组合写法：**

```c
typedef enum { MON, TUE, WED } Weekday;
Weekday today = TUE;
```

**一个常见用法：用 enum 做状态机。**

```c
typedef enum { IDLE, RUNNING, PAUSED, STOPPED } State;

State next_state(State s, int event) {
    switch (s) {
        case IDLE:    return event == START ? RUNNING : IDLE;
        case RUNNING: return event == PAUSE ? PAUSED : RUNNING;
        case PAUSED:  return event == RESUME ? RUNNING : PAUSED;
        default:      return s;
    }
}
```

比用一堆 `#define` 或裸数字强得多，而且加 `-Wswitch` 时编译器会提醒你漏了分支。
""",
            },
            {
                "title": "10.4 typedef 与 #define 的区别",
                "kind": "syntax",
                "md": r"""两者都能「造新名字」，但机制完全不同。

```c
typedef unsigned long ulong;    // 编译器层面，真正的类型别名
#define ULONG unsigned long     // 预处理器层面，纯文本替换
```

**关键差异一：作用域。**

`typedef` 遵守作用域规则，可以放在函数内、结构体内；`#define` 从定义处到文件末尾（或 `#undef`），无视作用域。

**关键差异二：指针。**

```c
typedef int *intptr;
intptr a, b;        // a 和 b 都是 int*

#define INTPTR int*
INTPTR c, d;        // c 是 int*，d 是 int！因为展开成 int* c, d;
```

这是 `#define` 最经典的坑。

**关键差异三：类型安全与调试。**

`typedef` 是类型系统的一部分，调试器能看到；`#define` 在预处理阶段就消失了，调试信息里没有它。

**结构体的标准写法：**

```c
typedef struct Point {
    int x, y;
} Point;            // 之后直接写 Point，不用写 struct Point
```

**注意这个写法里的 `Point` 出现了两次**，一个是 struct tag，一个是 typedef 名，两者是不同命名空间里的东西（C 允许同名）。很多人以为只写了一次。

**函数指针 typedef，这是最实用的场景：**

```c
typedef void (*Callback)(int, void *);   // 比裸声明可读性高太多

void set_handler(Callback cb);
```

**给数组类型起名（少见但有用）：**

```c
typedef int Matrix4[4][4];
Matrix4 m;          // 等价于 int m[4][4]
```

**什么时候用哪个？**

| 场景 | 选择 |
|---|---|
| 类型别名 | `typedef`（唯一正确选择） |
| 常量 | `#define` 或 `const`（类型安全优先选 `const`） |
| 函数式宏 | `#define`（但要小心副作用，见第 12 章） |
| 条件编译 | `#define`（唯一选择） |

**现代 C 的做法**：常量用 `enum` 或 `static const`，类型别名用 `typedef`，`#define` 只留给条件编译和必要的宏。
""",
            },
        ],
    },
    {
        "id": 11,
        "title": "文件操作",
        "icon": "📁",
        "stage": "工程篇 · 写出能用的程序",
        "stage_description": "数据要活过进程结束，就得落到磁盘上。",
        "description": "学完本章，你应该能够：\n\n- 用 fopen / fclose 正确打开和关闭文件，并处理失败；\n- 用 fprintf / fscanf / fgets 做文本读写；\n- 用 fread / fwrite 做二进制读写，并说清它与文本模式的区别；\n- 写出不会泄漏文件句柄、不会静默丢数据的代码。",
        "knowledge_points": [
            {
                "title": "11.1 fopen 模式与错误处理",
                "kind": "syntax",
                "md": r"""文件操作的核心是一个 `FILE *` 句柄，它背后是操作系统打开的文件描述符加一块缓冲区。

```c
#include <stdio.h>

int main(void) {
    FILE *fp = fopen("data.txt", "r");
    if (fp == NULL) {
        perror("打开文件失败");     // 自动打印 "打开文件失败: No such file or directory"
        return 1;
    }
    // ... 读写
    fclose(fp);
    return 0;
}
```

**打开模式表：**

| 模式 | 含义 | 文件不存在时 | 已存在时 |
|---|---|---|---|
| `"r"` | 只读 | 失败 | 从头读 |
| `"w"` | 只写 | 创建 | **清空**（危险！） |
| `"a"` | 追加 | 创建 | 末尾追加 |
| `"r+"` | 读写 | 失败 | 从头读写 |
| `"w+"` | 读写 | 创建 | **清空** |
| `"a+"` | 读追加 | 创建 | 读任意位置，写只在末尾 |
| `"rb"` `"wb"` | 二进制模式 | 同上 | 同上，不做换行转换 |

**三个必须知道的点：**

1. **`"w"` 会立刻清空文件**，哪怕你后面什么都没写。用之前想清楚。
2. **永远检查 `fopen` 的返回值**。没检查就是空指针解引用，直接崩。
3. **Windows 上文本模式会把 `\r\n` 和 `\n` 互相转换**，二进制模式不会。处理图片、压缩包、序列化数据必须用 `"rb"/"wb"`。

**`perror` 是最省事的错误输出**，它读 `errno` 并翻译成人话。想看错误码就用 `strerror(errno)`。

**关闭文件：**

```c
fclose(fp);
```

`fclose` 会**刷新缓冲区**。忘记 `fclose` 的后果不只是泄漏句柄——缓冲区里的数据可能根本没写进磁盘。

**进程退出时，标准库会替你关闭所有流**，所以小脚本里偶尔漏了也能跑。但长期运行的服务会耗尽文件描述符（默认 1024 个），必须成对关闭。
""",
            },
            {
                "title": "11.2 文本读写：fprintf / fscanf / fgets",
                "kind": "syntax",
                "md": r"""**写：`fprintf` 和 `fputs`**

```c
FILE *fp = fopen("out.txt", "w");
if (!fp) return 1;

fprintf(fp, "name=%s, age=%d\n", "Tom", 20);
fputs("一行文本\n", fp);
fputc('X', fp);

fclose(fp);
```

`fprintf` 就是「输出到文件的 printf」，格式符规则完全一样。

**读：三种方式，各有适用场景。**

**方式一：`fgets`（逐行读，最安全）**

```c
char line[256];
while (fgets(line, sizeof(line), fp)) {
    // line 里包含换行符，通常要去掉
    size_t n = strlen(line);
    if (n && line[n-1] == '\n') line[n-1] = '\0';
    printf("[%s]\n", line);
}
```

优点：不会溢出。缺点：超长行会被截断。

**方式二：`fscanf`（按格式读，适合结构化数据）**

```c
int id; char name[32]; double score;
while (fscanf(fp, "%d %31s %lf", &id, name, &score) == 3) {
    printf("%d %s %.2f\n", id, name, score);
}
```

三个坑：

1. **返回值要检查**。返回成功匹配的项数，不等于 3 就说明格式对不上或到 EOF 了。
2. **`%s` 会溢出**，必须写宽度 `%31s`（数组大小减 1）。
3. **`fscanf` 遇到空白就停**，读不了带空格的字段。

**方式三：`fgets` + `sscanf`（最稳的组合）**

```c
char line[256];
while (fgets(line, sizeof line, fp)) {
    int id; char name[32]; double score;
    if (sscanf(line, "%d %31s %lf", &id, name, &score) == 3) {
        // 处理
    }
}
```

先整行读进来，再在内存里解析。这样超长行不会把整个解析流程搞乱，也便于报错时打印原始行。

**`fgets` 处理长行的正确姿势：**

```c
char buf[256];
int truncated = 0;
while (fgets(buf, sizeof buf, fp)) {
    size_t n = strlen(buf);
    if (n > 0 && buf[n-1] == '\n') {
        buf[--n] = '\0';
    } else if (!feof(fp)) {
        truncated = 1;      // 这行没读完，后面的内容还在流里
    }
}
```

**判断读完要用返回值，不要用 `feof` 当循环条件**：

```c
while (!feof(fp)) { fgets(...); }    // 错：会多循环一次，最后一行重复处理
while (fgets(...)) { ... }           // 对
```

`feof` 只有在**尝试读取失败之后**才返回真，所以放在循环条件里必然多跑一轮。这是 C 的经典错误。
""",
            },
            {
                "title": "11.3 二进制读写：fread / fwrite",
                "kind": "syntax",
                "md": r"""二进制模式直接读写内存字节，不经过任何转换。

```c
#include <stdio.h>

typedef struct { int id; char name[16]; double score; } Record;

int main(void) {
    Record recs[2] = {
        {1, "Alice", 92.5},
        {2, "Bob",   88.0},
    };

    // 写
    FILE *fp = fopen("data.bin", "wb");
    if (!fp) return 1;
    size_t n = fwrite(recs, sizeof(Record), 2, fp);
    printf("写入 %zu 条\n", n);
    fclose(fp);

    // 读
    fp = fopen("data.bin", "rb");
    if (!fp) return 1;
    Record buf[2] = {0};
    n = fread(buf, sizeof(Record), 2, fp);
    printf("读出 %zu 条，第一条: %d %s %.1f\n",
           n, buf[0].id, buf[0].name, buf[0].score);
    fclose(fp);
    return 0;
}
```

**`fread` / `fwrite` 的返回值是「成功的元素个数」，不是字节数。**

```c
size_t n = fread(buf, sizeof(Record), 2, fp);
if (n != 2) {
    if (feof(fp))  printf("文件不足 2 条\n");
    if (ferror(fp)) printf("读取出错\n");
}
```

**二进制读写的四个限制（很重要）：**

1. **不可跨平台**。结构体有填充字节（第 10 章），字节序有大小端差异。x86 写的文件到 ARM 上可能读不出来。
2. **不可跨版本**。结构体加一个字段，旧文件全部读错。
3. **指针不能存**。存的是地址，下次运行地址就变了。
4. **不能直接存变长数据**。字符串要固定长度或另存长度+内容。

**正确的跨平台序列化做法：**

```c
// 逐字段写出，显式指定字节序和宽度
static void put_u32(uint8_t *p, uint32_t v) {   // 大端序
    p[0] = (v >> 24) & 0xFF;
    p[1] = (v >> 16) & 0xFF;
    p[2] = (v >> 8)  & 0xFF;
    p[3] = v & 0xFF;
}
```

这就是为什么真实项目用 JSON / MessagePack / protobuf 而不是直接 dump 结构体。

**但二进制读写有个不可替代的场景：同机同版本的程序内部缓存。** 快、简单、零依赖。用之前确认这个前提成立。

**定位：**

```c
fseek(fp, 2 * sizeof(Record), SEEK_SET);   // 跳到第 3 条
long pos = ftell(fp);                       // 当前位置
rewind(fp);                                 // 回到开头
fseek(fp, 0, SEEK_END);                     // 到末尾（文件大小 = ftell）
```

`ftell` 返回 `long`，大于 2GB 的文件在 32 位 `long` 平台上会溢出。大文件用 `fseeko`/`ftello`（POSIX）或平台 API。
""",
            },
            {
                "title": "11.4 资源管理与常见失败模式",
                "kind": "pitfall",
                "md": r"""文件句柄和堆内存一样，是必须显式释放的资源。用错的后果是句柄泄漏，最终 `fopen` 返回 NULL。

**模式一：多出口函数里漏关文件**

```c
int process(const char *path) {
    FILE *fp = fopen(path, "r");
    if (!fp) return -1;

    char *buf = malloc(1024);
    if (!buf) return -1;              // 泄漏 fp！

    if (parse(fp) != 0) {
        free(buf);
        return -1;                     // 漏了 fclose(fp)
    }

    free(buf);
    fclose(fp);
    return 0;
}
```

清理代码写四遍，漏一遍就是 bug。**正确做法是单一出口 + goto**：

```c
int process(const char *path) {
    int ret = -1;
    FILE *fp = NULL;
    char *buf = NULL;

    fp = fopen(path, "r");
    if (!fp) goto cleanup;

    buf = malloc(1024);
    if (!buf) goto cleanup;

    if (parse(fp) != 0) goto cleanup;

    ret = 0;

cleanup:
    if (fp)  fclose(fp);      // fclose(NULL) 是 UB，所以判空
    free(buf);                // free(NULL) 安全，不用判
    return ret;
}
```

这是 Linux 内核的标准写法：**无论从哪条路径退出，清理逻辑只有一份**。`free(NULL)` 是安全的，`fclose(NULL)` **不是**，所以只有 fp 需要判空。

**模式二：缓冲区没刷新就异常退出**

```c
fprintf(fp, "重要数据\n");
exit(1);           // 缓冲区可能没刷，数据丢了
```

`exit` 会刷新所有流，但 `_exit` / `abort` / 段错误不会。**在关键写入之后显式 `fflush(fp)`**，或者用 `fclose` 正常关闭。

**模式三：检查了 fopen 但没检查写入**

```c
fprintf(fp, "%d\n", massive_data);   // 磁盘满了，返回值被忽略
fclose(fp);
// 用户以为保存成功了
```

写入失败在磁盘满、权限变更、网络盘断开时都会发生。关键数据要检查：

```c
if (fprintf(fp, "%d\n", v) < 0) {
    perror("写入失败");
}
if (fclose(fp) != 0) {          // fclose 也会失败，因为要刷缓冲区
    perror("关闭失败，数据可能未落盘");
}
```

**模式四：`remove` / `rename` 与打开的文件冲突**

Windows 上文件被打开时不能删除或重命名。**先 `fclose` 再操作**，否则 `rename` 返回 -1，`errno` 是 `EACCES`。

**临时文件的安全写法：**

```c
FILE *tmp = tmpfile();       // 自动删除，最省事
```

或者 `mkstemp`（POSIX）生成唯一文件名。**不要用 `tmpnam`**，它有竞态漏洞。

**一份可用的检查清单：**

- [ ] 每个 `fopen` 都有对应的 `fclose`，且在**所有**退出路径上
- [ ] `fopen` 返回值判空
- [ ] 读写返回值检查（至少关键路径）
- [ ] 二进制模式用于非文本数据
- [ ] 循环读用返回值判断，不用 `feof`
""",
            },
        ],
    },
    {
        "id": 12,
        "title": "预处理与多文件编译",
        "icon": "⚙️",
        "stage": "工程篇 · 写出能用的程序",
        "stage_description": "从「一个 .c 文件」到「一个能交付的项目」，差的是这一章。",
        "description": "学完本章，你应该能够：\n\n- 用 `#define` 写宏，并识别宏展开的经典陷阱；\n- 用条件编译做平台适配与调试开关；\n- 写正确的头文件，用 include guard 解决重复包含；\n- 用 gcc 手工编译多文件项目，并读懂 Makefile。",
        "knowledge_points": [
            {
                "title": "12.1 #define 与宏的三个陷阱",
                "kind": "pitfall",
                "md": r"""`#define` 是**纯文本替换**，发生在编译之前。它不理解类型，也不理解表达式结构。

**陷阱一：缺少括号**

```c
#define SQUARE(x) x * x

SQUARE(3 + 1)      // 展开成 3 + 1 * 3 + 1 = 7，不是 16
```

**正确写法：每个参数和整个表达式都加括号。**

```c
#define SQUARE(x) ((x) * (x))
```

即便如此，`SQUARE(i++)` 仍然会把 `i++` 执行两次。**带副作用的参数不要传给宏。**

**陷阱二：宏没有类型检查**

```c
#define MAX(a, b) ((a) > (b) ? (a) : (b))

MAX(x, "hello")    // 编译错误，但报错信息指向宏展开后的位置，很难读
```

**C11 的 `_Generic` 或 `inline` 函数可以解决。** 现代 C 的优先选择是：

```c
static inline int max_int(int a, int b) { return a > b ? a : b; }
```

有类型检查、能调试、能取地址，且 `inline` 不损失性能。

**陷阱三：宏里的分号**

```c
#define SWAP(a, b) do { int t = (a); (a) = (b); (b) = t; } while (0)

if (cond)
    SWAP(x, y);      // 展开成一个完整的语句块，安全
else
    ...
```

`do { ... } while (0)` 是标准技巧——它让宏在语法上等价于一条语句，能安全地放进 `if` 分支而不会破坏 `else` 的配对。**顺带一提，结尾不加分号，让调用方自己加。**

**宏还有用的地方：**

| 用途 | 例子 |
|---|---|
| 编译期常量 | `#define BUF_SIZE 4096` |
| 字符串化 | `#define STR(x) #x` |
| 拼接 token | `#define CAT(a,b) a##b` |
| 条件编译 | `#ifdef DEBUG` |
| 日志的 `__FILE__` / `__LINE__` | 见下 |

```c
#define LOG(fmt, ...) \
    fprintf(stderr, "[%s:%d] " fmt "\n", __FILE__, __LINE__, ##__VA_ARGS__)
```

`##__VA_ARGS__` 是 GCC 扩展，允许零个可变参数。标准 C 需要至少一个参数。

**总结：能用 `const` / `enum` / `static inline` 就别用宏。** 宏只留给这四种场景：条件编译、头文件保护、字符串化/拼接、必须知道调用位置（`__LINE__`）的日志。
""",
            },
            {
                "title": "12.2 条件编译：一份代码适配多个环境",
                "kind": "syntax",
                "md": r"""条件编译在预处理阶段决定哪些代码参与编译。

```c
#ifdef _WIN32
    #include <windows.h>
    #define PATH_SEP '\\'
#elif defined(__linux__)
    #include <unistd.h>
    #define PATH_SEP '/'
#else
    #error "不支持的操作系统"
#endif
```

**四种指令：**

| 指令 | 含义 |
|---|---|
| `#ifdef X` / `#ifndef X` | X 是否被定义 |
| `#if expr` | 表达式是否为真（可以做数值比较） |
| `#elif` / `#else` / `#endif` | 分支 |
| `#undef X` | 取消定义 |

**千万别写 `#if X` 来判断宏是否存在**，除非你确定它被定义过：

```c
#define FEATURE            // 空定义
#if FEATURE                 // 错：展开成 #if，语法错误
#if defined(FEATURE)        // 对
#ifdef FEATURE              // 对，等价
```

**实用模式一：调试开关**

```c
#ifdef DEBUG
    #define DBG(fmt, ...) fprintf(stderr, fmt "\n", ##__VA_ARGS__)
#else
    #define DBG(fmt, ...) ((void)0)
#endif
```

`((void)0)` 而不是空，是为了让 `DBG("x");` 在语法上仍然是一条语句。

编译时用 `-DDEBUG` 打开。**不要把调试代码用 `//` 注释掉**，用条件编译，否则代码会腐烂。

**实用模式二：平台差异**

```c
#if defined(_WIN32)
    #define EXPORT __declspec(dllexport)
#else
    #define EXPORT __attribute__((visibility("default")))
#endif
```

**实用模式三：容器尺寸与静态断言**

```c
#define MAX_ITEMS 100
typedef struct { int data[MAX_ITEMS]; int n; } Buffer;

_Static_assert(sizeof(Buffer) < 1024, "Buffer 太大了");   // C11
```

**`#error` 与 `#pragma message`：**

```c
#if !defined(__STDC_VERSION__) || __STDC_VERSION__ < 201112L
    #error "本项目需要 C11 或更高标准"
#endif
```

在编译期把「环境不对」变成明确的错误信息，比运行时崩掉好查一百倍。

**一个真实用途：单头文件库。**

很多 C 库（stb、cJSON 的某些版本）用这种模式同时提供「声明」和「实现」：

```c
// mylib.h
#ifdef MYLIB_IMPLEMENTATION
    // 这里是完整的实现代码
#endif
// 这里是声明
```

在**一个** .c 文件里 `#define MYLIB_IMPLEMENTATION` 再 include，就得到实现；其他文件正常 include 只得到声明。省掉了「头文件 + 源文件」的配对管理。
""",
            },
            {
                "title": "12.3 头文件与 include guard",
                "kind": "concept",
                "md": r"""多文件项目的核心问题是：**声明放头文件，定义放 .c 文件。**

```c
/* math_utils.h */
#ifndef MATH_UTILS_H        // include guard，防止重复包含
#define MATH_UTILS_H

int  add(int a, int b);
int  mul(int a, int b);

#endif /* MATH_UTILS_H */
```

```c
/* math_utils.c */
#include "math_utils.h"

int add(int a, int b) { return a + b; }
int mul(int a, int b) { return a * b; }
```

```c
/* main.c */
#include <stdio.h>
#include "math_utils.h"

int main(void) {
    printf("%d\n", add(2, 3));
    return 0;
}
```

**为什么需要 include guard？**

```c
// a.h 里 #include "b.h"
// c.c 里 #include "a.h" 和 #include "b.h"
```

没有保护，`b.h` 的内容会被展开两次，导致「重复定义」错误。guard 让第二次包含时整体被跳过。

**现代替代品：`#pragma once`**

```c
#pragma once
```

更短，且能处理 guard 处理不了的边界情况（同一个文件通过不同路径被包含）。但它不是标准的一部分——尽管所有主流编译器都支持。**跨平台代码用 guard，内部项目用 `#pragma once`。**

**头文件里绝对不能放的东西：**

| 不能放 | 原因 | 正确做法 |
|---|---|---|
| 函数定义 | 每个包含它的 .c 都生成一份，链接时重复定义 | 放 .c，头文件只放声明 |
| 全局变量定义 | 同上 | 头文件用 `extern` 声明，.c 里定义 |
| `static` 变量 | 每个 .c 各有一份副本，行为诡异 | 放 .c |
| `using namespace`（C 无关） | — | — |

**例外：`static inline` 函数可以放头文件**，因为 `static` 让每个文件有自己的副本，不产生链接冲突。

```c
/* utils.h */
static inline int clamp(int v, int lo, int hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}
```

**全局变量的正确写法：**

```c
/* config.h */
extern int g_verbose;         // 声明，不分配内存

/* config.c */
int g_verbose = 0;            // 定义，唯一的一份
```

**`#include <...>` 和 `"..."` 的区别：**

- `< >` 从系统头文件路径找
- `"..."` 先从当前文件所在目录找，再找系统路径

自己写的头文件一律用 `"..."`。

**一条经验法则：头文件应该只包含「别人需要知道什么」，不包含「你是怎么实现的」。** 这是 C 里唯一能实现信息隐藏的手段。
""",
            },
            {
                "title": "12.4 多文件编译与 Makefile",
                "kind": "practice",
                "md": r"""**最简单的多文件编译：**

```c
gcc main.c math_utils.c -o app
```

gcc 会依次编译每个 .c，再统一链接。

**分步编译（理解过程用）：**

```c
gcc -c main.c -o main.o
gcc -c math_utils.c -o math_utils.o
gcc main.o math_utils.o -o app
```

分步的好处是**只重编改过的文件**。改一行代码就全量重编，在几十个文件的项目里是灾难。

**用 Makefile 自动化：**

```makefile
CC      = gcc
CFLAGS  = -std=c11 -Wall -Wextra -O2 -g
TARGET  = app
OBJS    = main.o math_utils.o

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

.PHONY: clean
```

**四行关键语法：**

| 符号 | 含义 |
|---|---|
| `$@` | 目标名 |
| `$^` | 所有依赖 |
| `$<` | 第一个依赖 |
| `.PHONY` | 声明伪目标，避免和同名文件冲突 |

**注意：Makefile 的缩进必须是 Tab，不是空格。** 这是新手最常见的报错（`missing separator`）。

**头文件依赖是 Make 的一个坑**：上面的规则只跟踪 .c 的变化，改了 .h 不会触发重编。解决方式是用 `-MMD` 自动生成依赖：

```makefile
CFLAGS += -MMD -MP
-include $(OBJS:.o=.d)
```

**跨平台的项目更常用 CMake：**

```cmake
cmake_minimum_required(VERSION 3.10)
project(cmaster C)

set(CMAKE_C_STANDARD 11)
add_executable(app main.c math_utils.c)
target_compile_options(app PRIVATE -Wall -Wextra)
```

CMake 的优点是能生成 Makefile、Ninja、Visual Studio 工程等任意构建系统。**中大型 C 项目基本都用它。**

**链接错误怎么读：**

```
undefined reference to `add'
```

三种可能：

1. 忘了把对应的 .o 或 .c 加进编译命令
2. 函数签名与声明不一致（比如声明 `int add(int,int)`，定义成 `double add`）
3. 用了数学库但没加 `-lm`

```
multiple definition of `add'
```

一种可能：函数定义写在了头文件里，被多个 .c 包含。

**一句话总结这一章：** 单文件程序靠语法，多文件项目靠**声明与定义分离**。这条线划清楚了，C 就能写大的。
""",
            },
        ],
    },
]

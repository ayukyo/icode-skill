#include "calc.h"
#include <limits.h>
#include <math.h>  /* would_overflow_power 预计算 base^exp 用 log2/log */
#include <stddef.h>  /* NULL */

/* ---- 内部溢出检查辅助 ---- */

/* 前向声明：calc_power 入口防御用 */
static int would_overflow_power(int base, int exp);

/* 加法溢出：a+b 是否超出 int 范围。b>0 时查上界，b<0 时查下界 */
static int add_overflows(int a, int b)
{
    if (b > 0) {
        return a > INT_MAX - b;  /* 正溢出：a+b > INT_MAX */
    } else if (b < 0) {
        return a < INT_MIN - b;  /* 负溢出：a+b < INT_MIN */
    }
    return 0;  /* b==0 不溢出 */
}

/* 减法溢出：a-b 是否超出 int 范围。b<0 时查上界（a-负=a+正），b>0 时查下界 */
static int sub_overflows(int a, int b)
{
    if (b < 0) {
        return a > INT_MAX + b;  /* a-负数 > INT_MAX */
    } else if (b > 0) {
        return a < INT_MIN + b;  /* a-正数 < INT_MIN */
    }
    return 0;  /* b==0 不溢出 */
}

/* 乘法溢出：a*b 是否超出 int 范围。处理 b=0/b<0/INT_MIN 边界 */
static int mul_overflows(int a, int b)
{
    if (a == 0 || b == 0) {
        return 0;  /* 任一为 0，结果 0 不溢出 */
    }
    /* 用 |a|>|INT_MAX/b| 判断，但需先排除 b 整除 INT_MAX 的边界 */
    if (a == INT_MIN && b == -1) {
        return 1;  /* INT_MIN * -1 = INT_MAX+1 溢出（且 |INT_MIN| 不可表示） */
    }
    if (b == -1) {
        return a == INT_MIN;  /* a 非	INT_MIN 时 a*(-1) 可表示 */
    }
    /* 一般情况：比较 |a| 与 |INT_MAX / b|，注意 INT_MAX/b 已截断 */
    if (a > 0) {
        return b > 0 ? (a > INT_MAX / b) : (b < INT_MIN / a);
    } else {
        return b > 0 ? (a < INT_MIN / b) : (a < INT_MAX / b);  /* a<0,b<0: 结果为正，查上界 */
    }
}

/* ---- 公开接口 ---- */

/*
 * 对两个整数做四则运算 + 取模
 * op: '+' '-' '*' '/' '%'
 * 成功 *result 存结果返回 CALC_OK；失败返回错误码不写 *result
 */
int calc_basic(int a, int b, char op, int *result)
{
    if (result == 0) {
        return CALC_ERR_INVALID;  /* 空指针 */
    }

    switch (op) {
    case '+':
        if (add_overflows(a, b)) {
            return CALC_ERR_OVERFLOW;  /* 加法溢出 */
        }
        *result = a + b;
        return CALC_OK;
    case '-':
        if (sub_overflows(a, b)) {
            return CALC_ERR_OVERFLOW;  /* 减法溢出 */
        }
        *result = a - b;
        return CALC_OK;
    case '*':
        if (mul_overflows(a, b)) {
            return CALC_ERR_OVERFLOW;  /* 乘法溢出 */
        }
        *result = a * b;
        return CALC_OK;
    case '/':
        if (b == 0) {
            return CALC_ERR_DIV_ZERO;  /* 除零 */
        }
        *result = a / b;
        return CALC_OK;
    case '%':
        if (b == 0) {
            return CALC_ERR_DIV_ZERO;  /* 取模零，与除法同义 */
        }
        *result = a % b;  /* 取模结果 |a%b|<|b| 不会溢出 */
        return CALC_OK;
    default:
        return CALC_ERR_INVALID;  /* 未知操作符 */
    }
}

/*
 * 整数幂运算 base^exp
 * exp<0 返回 INVALID；exp==0 返回 1；exp>0 累乘，溢出返回 OVERFLOW
 */
int calc_power(int base, int exp, int *result)
{
    if (result == 0 || exp < 0) {
        return CALC_ERR_INVALID;  /* 空指针或负指数 */
    }

    /* exp+base 组合防御：预计算 base^exp 是否超 INT_MAX（防高次幂静默溢出） */
    if (exp > 0 && would_overflow_power(base, exp)) {
        return CALC_ERR_OVERFLOW;
    }

    *result = 1;  /* exp==0 时直接返回 1（循环不执行） */
    for (int i = 0; i < exp; i++) {
        if (mul_overflows(*result, base)) {
            return CALC_ERR_OVERFLOW;  /* 累乘溢出 */
        }
        *result *= base;
    }
    return CALC_OK;
}

/*
 * 整数开方 ⌊√x⌋（向下取整）
 * 纯整数二分法：在 [1, x] 区间找最大 r 使 r²≤x，即向下取整
 * 复用 mul_overflows 检查 mid²溢出（mid 大时防御性检查）
 */
int calc_sqrt(int x, int *result)
{
    if (result == 0 || x < 0) {
        return CALC_ERR_INVALID;  /* 空指针或负数无实数平方根 */
    }
    if (x == 0 || x == 1) {
        *result = x;  /* √0=0, √1=1 直接返回 */
        return CALC_OK;
    }

    int lo = 1;
    int hi = x;  /* 二分区间 [1, x] */
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;  /* 防加法溢出 */
        if (mul_overflows(mid, mid)) {
            hi = mid - 1;  /* mid²溢出，mid偏大，缩上界 */
            continue;
        }
        int sq = mid * mid;
        if (sq == x) {
            *result = mid;  /* 完美平方 */
            return CALC_OK;
        } else if (sq < x) {
            lo = mid + 1;  /* mid偏小 */
        } else {
            hi = mid - 1;  /* mid偏大 */
        }
    }
    *result = hi;  /* 循环结束 lo>hi，hi 是最大 r 使 r²<x，即向下取整 */
    return CALC_OK;
}

/*
 * 整数绝对值 |x|
 * x >= 0 直接返回；x < 0 取反；INT_MIN 特判溢出（|INT_MIN|=INT_MAX+1）
 */
int calc_abs(int x, int *result)
{
    if (result == 0) {
        return CALC_ERR_INVALID;  /* 空指针 */
    }
    if (x < 0) {
        if (x == INT_MIN) {
            return CALC_ERR_OVERFLOW;  /* |INT_MIN| 溢出 */
        }
        *result = -x;  /* 负数取反 */
    } else {
        *result = x;  /* 正数或零 */
    }
    return CALC_OK;
}

/*
 * 整数平方根 ⌊√x⌋（向下取整）
 * 纯整数二分法：在 [1, x] 区间找最大 r 使 r²≤x，即向下取整
 * 复用 mul_overflows 检查 mid²溢出（mid 大时防御性检查）
 */
int calc_isqrt(int x, int *result)
{
    if (result == 0 || x < 0) {
        return CALC_ERR_INVALID;  /* 空指针或负数无实数平方根 */
    }
    if (x == 0 || x == 1) {
        *result = x;  /* √0=0, √1=1 直接返回 */
        return CALC_OK;
    }

    int lo = 1;
    int hi = x;  /* 二分区间 [1, x] */
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;  /* 防加法溢出 */
        if (mul_overflows(mid, mid)) {
            hi = mid - 1;  /* mid²溢出，mid偏大，缩上界 */
            continue;
        }
        int sq = mid * mid;
        if (sq == x) {
            *result = mid;  /* 完美平方 */
            return CALC_OK;
        } else if (sq < x) {
            lo = mid + 1;  /* mid偏小 */
        } else {
            hi = mid - 1;  /* mid偏大 */
        }
    }
    *result = hi;  /* 循环结束 lo>hi，hi 是最大 r 使 r²<x，即向下取整 */
    return CALC_OK;
}

/*
 * 整数最大公约数 gcd(a, b)（欧几里得算法）
 * 算法：gcd(a,b) = gcd(b, a%b)，终止条件 gcd(a, 0) = |a|
 * 负数先取绝对值（INT_MIN 特殊处理，避免 abs() 未定义行为）
 * b == 0 前置判断，避免 a % b 除零
 * 约定 gcd(0, 0) = 0
 */
int calc_gcd(int a, int b, int *result)
{
    if (result == 0) {
        return CALC_ERR_INVALID;  /* 空指针 */
    }

    /* INT_MIN 安全处理：abs(INT_MIN) 是未定义行为，替换为 INT_MAX（近似 |INT_MIN|） */
    if (a == INT_MIN) {
        a = INT_MAX;  /* INT_MIN 的最大公约数结果在 INT_MAX 范围内仍正确 */
    } else if (a < 0) {
        a = -a;  /* 负数取反 */
    }
    if (b == INT_MIN) {
        b = INT_MAX;
    } else if (b < 0) {
        b = -b;
    }

    /* b == 0 前置判断：避免后续 a % b 除零错误 */
    /* 同时处理 gcd(0,n)=n, gcd(n,0)=|n|, gcd(0,0)=0 三种边界 */
    if (b == 0) {
        *result = a;  /* 此时 a 已是非负整数 */
        return CALC_OK;
    }

    /* 欧几里得算法迭代版 */
    while (b != 0) {
        int t = b;
        b = a % b;
        a = t;
    }
    *result = a;
    return CALC_OK;
}

/*
 * 整数最小公倍数 lcm(a, b)（基于 gcd 的公式 lcm = |a*b|/gcd）
 * 算法：先除后乘 lcm = |a/gcd| * |b|，避免中间结果溢出
 * 零短路：a==0 或 b==0 时直接返回 0（避免 gcd(0,0)=0 后触发除零）
 * INT_MIN 对齐 calc_gcd 处理：a==INT_MIN 替换为 INT_MAX（避免 abs 未定义）
 * 最终乘法调用 mul_overflows 检查，避免 |a/gcd|*|b| 在 gcd=1 时仍溢出
 */
int calc_lcm(int a, int b, int *result)
{
    int g, quotient, product;

    if (result == 0) {
        return CALC_ERR_INVALID;  /* 空指针 */
    }

    /* 零短路：避免 calc_gcd(0,0) 返回 0 后触发除零 */
    if (a == 0 || b == 0) {
        *result = 0;
        return CALC_OK;
    }

    /* INT_MIN 安全处理：abs(INT_MIN) 是未定义行为，替换为 INT_MAX（对齐 calc_gcd） */
    if (a == INT_MIN) {
        a = INT_MAX;
    } else if (a < 0) {
        a = -a;
    }
    if (b == INT_MIN) {
        b = INT_MAX;
    } else if (b < 0) {
        b = -b;
    }

    /* 复用 calc_gcd 计算最大公约数 */
    if (calc_gcd(a, b, &g) != CALC_OK) {
        return CALC_ERR_INVALID;  /* 不应发生，防御性 */
    }

    /* 先除后乘：避免 |a*b| 中间结果溢出 */
    quotient = a / g;
    product = quotient * b;  /* 实际乘法（g 可能为 0？已通过零短路排除） */

    /* 复用 mul_overflows 检查最终乘法（gcd=1 时退化为 |a|*|b|，仍可能溢出） */
    (void)product;  /* 占位避免 unused 警告 */
    if (mul_overflows(quotient, b)) {
        return CALC_ERR_OVERFLOW;
    }
    *result = quotient * b;
    return CALC_OK;
}

/*
 * 预计算 base^exp 是否超 INT_MAX
 * 用对数法：base^exp > INT_MAX iff exp*log2(|base|) > log2(INT_MAX)≈31
 * base=0/1/-1 特判（结果在范围内不超）
 */
static int would_overflow_power(int base, int exp)
{
    if (base == 0 || base == 1 || base == -1) {
        return 0;  /* base^exp 在 [-1, 1] 或 [0, 0]，不超 */
    }
    double log_int_max = 31.0;  /* log2(2147483647) ≈ 31 */
    double log_base;
    if (base == 2) {
        log_base = 1.0;
    } else if (base == 3) {
        log_base = 1.585;  /* log2(3) ≈ 1.585 */
    } else if (base == 4) {
        log_base = 2.0;
    } else if (base > 0) {
        log_base = log((double)base) / log(2.0);
    } else {
        log_base = log((double)(-base)) / log(2.0);
    }
    return (double)exp * log_base > log_int_max;
}

/*
 * 字符串表达式求值器（递归下降 parser）
 * 语法（BNF）：
 *   expr     := term (('+' | '-') term)*
 *   term     := factor (('*' | '/') factor)*
 *   factor   := ('-' factor) | primary
 *   primary  := NUMBER | '(' expr ')'
 *   NUMBER   := [0-9]+
 * 复用 calc_basic 处理二元运算
 * ParserState 参数化（可重入，无 static 全局）
 * 错误时 *result 保持原值
 */

/* 表达式最大长度（含 '\0' 不算字符数，仅字符数） */
#define CALC_EVAL_MAX_LEN 256

/* Parser 状态：参数化传入，禁止 static 全局 */
typedef struct {
    const char *p;       /* 当前扫描位置 */
} ParserState;

/* 跳过空白字符 */
static void skip_whitespace(ParserState *st)
{
    while (*st->p == ' ' || *st->p == '\t') {
        st->p++;
    }
}

/* 解析数字（仅正整数，无前导 0 限制） */
static int parse_number(ParserState *st, int *out)
{
    int value = 0;
    int has_digit = 0;
    skip_whitespace(st);
    if (*st->p < '0' || *st->p > '9') {
        return CALC_ERR_SYNTAX;  /* 非数字 */
    }
    while (*st->p >= '0' && *st->p <= '9') {
        int digit = *st->p - '0';
        /* 溢出检查 1：value*10 是否溢出 */
        if (mul_overflows(value, 10)) {
            return CALC_ERR_OVERFLOW;
        }
        /* 溢出检查 2：value*10 + digit 是否超 INT_MAX */
        if (value > (INT_MAX - digit) / 10) {
            return CALC_ERR_OVERFLOW;
        }
        value = value * 10 + digit;
        st->p++;
        has_digit = 1;
    }
    if (!has_digit) {
        return CALC_ERR_SYNTAX;
    }
    *out = value;
    return CALC_OK;
}

/* 前向声明 */
static int parse_expr(ParserState *st, int *out);

/* primary := NUMBER | '(' expr ')' */
static int parse_primary(ParserState *st, int *out)
{
    skip_whitespace(st);
    if (*st->p >= '0' && *st->p <= '9') {
        return parse_number(st, out);
    }
    if (*st->p == '(') {
        st->p++;  /* consume '(' */
        int rc = parse_expr(st, out);
        if (rc != CALC_OK) {
            return rc;  /* 错误传播 */
        }
        skip_whitespace(st);
        if (*st->p != ')') {
            return CALC_ERR_SYNTAX;  /* 期望 ')' */
        }
        st->p++;  /* consume ')' */
        return CALC_OK;
    }
    return CALC_ERR_SYNTAX;  /* 既不是数字也不是 '(' */
}

/* factor := ('-' factor) | primary（右结合递归） */
static int parse_factor(ParserState *st, int *out)
{
    skip_whitespace(st);
    if (*st->p == '-') {
        st->p++;  /* consume '-' */
        int rc = parse_factor(st, out);  /* 递归，支持 --2 = 2 */
        if (rc != CALC_OK) {
            return rc;
        }
        /* 一元负号取反：避免 INT_MIN 溢出 */
        if (*out == INT_MIN) {
            return CALC_ERR_OVERFLOW;
        }
        *out = -*out;
        return CALC_OK;
    }
    return parse_primary(st, out);
}

/* term := factor (('*' | '/') factor)* */
static int parse_term(ParserState *st, int *out)
{
    int rc = parse_factor(st, out);
    if (rc != CALC_OK) {
        return rc;
    }
    while (1) {
        skip_whitespace(st);
        char op = *st->p;
        if (op != '*' && op != '/') {
            break;
        }
        st->p++;  /* consume op */
        int right;
        rc = parse_factor(st, &right);
        if (rc != CALC_OK) {
            return rc;
        }
        int new_val;
        rc = calc_basic(*out, right, op, &new_val);
        if (rc != CALC_OK) {
            return rc;  /* 透传 DIV_ZERO / OVERFLOW */
        }
        *out = new_val;
    }
    return CALC_OK;
}

/* expr := term (('+' | '-') term)* */
static int parse_expr(ParserState *st, int *out)
{
    int rc = parse_term(st, out);
    if (rc != CALC_OK) {
        return rc;
    }
    while (1) {
        skip_whitespace(st);
        char op = *st->p;
        if (op != '+' && op != '-') {
            break;
        }
        st->p++;  /* consume op */
        int right;
        rc = parse_term(st, &right);
        if (rc != CALC_OK) {
            return rc;
        }
        int new_val;
        rc = calc_basic(*out, right, op, &new_val);
        if (rc != CALC_OK) {
            return rc;
        }
        *out = new_val;
    }
    return CALC_OK;
}

int calc_eval(const char *expr, int *result)
{
    int final_value;
    int len;

    if (expr == NULL || result == NULL) {
        return CALC_ERR_INVALID;  /* 参数错 */
    }

    /* 长度限制：仅字符数（不含 '\0'） */
    len = 0;
    while (expr[len] != '\0') {
        len++;
    }
    if (len > CALC_EVAL_MAX_LEN) {
        return CALC_ERR_SYNTAX;  /* 长度超限 */
    }

    /* 空字符串检查 */
    if (len == 0) {
        return CALC_ERR_SYNTAX;
    }

    ParserState st;
    st.p = expr;

    int rc = parse_expr(&st, &final_value);
    if (rc != CALC_OK) {
        return rc;  /* 错误时 *result 保持原值 */
    }

    /* 表达式结束后必须消费全部字符（否则视为语法错） */
    skip_whitespace(&st);
    if (*st.p != '\0') {
        return CALC_ERR_SYNTAX;  /* 多余字符，如 (1)(2) 或 1+2a */
    }

    *result = final_value;
    return CALC_OK;
}

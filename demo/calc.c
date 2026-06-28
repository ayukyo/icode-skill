#include "calc.h"
#include <limits.h>
#include <math.h>  /* would_overflow_power 预计算 base^exp 用 log2/log */

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

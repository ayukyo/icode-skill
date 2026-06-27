#include "calc.h"
#include <limits.h>

/* ---- 内部溢出检查辅助 ---- */

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

    *result = 1;  /* exp==0 时直接返回 1（循环不执行） */
    for (int i = 0; i < exp; i++) {
        if (mul_overflows(*result, base)) {
            return CALC_ERR_OVERFLOW;  /* 累乘溢出 */
        }
        *result *= base;
    }
    return CALC_OK;
}

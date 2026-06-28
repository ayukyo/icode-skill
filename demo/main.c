#include <stdio.h>
#include <limits.h>
#include "calc.h"

/* 测试用例：验证 calc_basic 各运算分支、calc_power、错误处理与溢出检查 */
int main(void)
{
    int result = 0;
    int rc = 0;

    /* ---- 既有四则运算 ---- */
    rc = calc_basic(10, 3, '+', &result);
    printf("10 + 3 = %d (rc=%d)\n", result, rc);

    rc = calc_basic(10, 3, '-', &result);
    printf("10 - 3 = %d (rc=%d)\n", result, rc);

    rc = calc_basic(10, 3, '*', &result);
    printf("10 * 3 = %d (rc=%d)\n", result, rc);

    rc = calc_basic(10, 3, '/', &result);
    printf("10 / 3 = %d (rc=%d)\n", result, rc);

    /* ---- 新增：取模运算 ---- */
    rc = calc_basic(10, 3, '%', &result);
    printf("10 %% 3 = %d (rc=%d)\n", result, rc);

    rc = calc_basic(10, 0, '%', &result);  /* 取模零，应返回 DIV_ZERO */
    printf("10 %% 0 rc=%d (expect %d)\n", rc, CALC_ERR_DIV_ZERO);

    /* ---- 新增：幂运算 ---- */
    rc = calc_power(2, 10, &result);
    printf("2 ^ 10 = %d (rc=%d)\n", result, rc);  /* 1024 */

    rc = calc_power(5, 0, &result);
    printf("5 ^ 0 = %d (rc=%d)\n", result, rc);  /* 1 */

    rc = calc_power(2, -1, &result);  /* 负指数，应返回 INVALID */
    printf("2 ^ -1 rc=%d (expect %d)\n", rc, CALC_ERR_INVALID);

    /* ---- 新增：溢出检查 ---- */
    rc = calc_basic(INT_MAX, 1, '+', &result);  /* INT_MAX+1 溢出 */
    printf("INT_MAX + 1 rc=%d (expect %d)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_basic(INT_MIN, 1, '-', &result);  /* INT_MIN-1 溢出 */
    printf("INT_MIN - 1 rc=%d (expect %d)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_basic(INT_MAX, 2, '*', &result);  /* INT_MAX*2 溢出 */
    printf("INT_MAX * 2 rc=%d (expect %d)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_basic(INT_MIN, -1, '*', &result);  /* INT_MIN*-1 溢出（边界） */
    printf("INT_MIN * -1 rc=%d (expect %d)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_power(2, 31, &result);  /* 2^31 溢出 int */
    printf("2 ^ 31 rc=%d (expect %d)\n", rc, CALC_ERR_OVERFLOW);

    /* ---- 既有错误场景 ---- */
    rc = calc_basic(10, 0, '/', &result);
    printf("10 / 0 rc=%d (expect %d)\n", rc, CALC_ERR_DIV_ZERO);

    rc = calc_basic(10, 3, '?', &result);  /* 无效操作符 */
    printf("10 ? 3 rc=%d (expect %d)\n", rc, CALC_ERR_INVALID);

    /* ---- 新增：开方运算（向下取整） ---- */
    rc = calc_sqrt(0, &result);
    printf("sqrt(0) = %d (rc=%d)\n", result, rc);  /* 0 */

    rc = calc_sqrt(1, &result);
    printf("sqrt(1) = %d (rc=%d)\n", result, rc);  /* 1 */

    rc = calc_sqrt(10, &result);
    printf("sqrt(10) = %d (rc=%d)\n", result, rc);  /* 3（向下取整） */

    rc = calc_sqrt(16, &result);
    printf("sqrt(16) = %d (rc=%d)\n", result, rc);  /* 4（完美平方） */

    rc = calc_sqrt(-4, &result);  /* 负数，应返回 INVALID */
    printf("sqrt(-4) rc=%d (expect %d)\n", rc, CALC_ERR_INVALID);

    rc = calc_sqrt(INT_MAX, &result);
    printf("sqrt(INT_MAX) = %d (rc=%d)\n", result, rc);  /* 46340 */

    /* ---- 新增：绝对值运算 ---- */
    rc = calc_abs(5, &result);
    printf("abs(5) = %d (rc=%d)\n", result, rc);  /* 5 */

    rc = calc_abs(-5, &result);
    printf("abs(-5) = %d (rc=%d)\n", result, rc);  /* 5 */

    rc = calc_abs(0, &result);
    printf("abs(0) = %d (rc=%d)\n", result, rc);  /* 0 */

    rc = calc_abs(INT_MIN, &result);  /* INT_MIN 溢出 */
    printf("abs(INT_MIN) rc=%d (expect %d)\n", rc, CALC_ERR_OVERFLOW);

    /* ---- 新增：calc_power 边界测试（验证 exp+base 防御） ---- */
    rc = calc_power(2, 30, &result);  /* 2^30=1073741824 < INT_MAX，不溢出 */
    printf("2 ^ 30 = %d (rc=%d, expect 0)\n", result, rc);

    rc = calc_power(2, 31, &result);  /* 2^31=INT_MAX+1 必超，应返 OVERFLOW */
    printf("2 ^ 31 rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_power(3, 20, &result);  /* 3^20=3486784401 > INT_MAX，应返 OVERFLOW（base≠2 边界） */
    printf("3 ^ 20 rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    /* ---- 新增：isqrt 整数平方根 ---- */
    rc = calc_isqrt(0, &result);
    printf("isqrt(0) = %d (rc=%d)\n", result, rc);  /* 0 */

    rc = calc_isqrt(1, &result);
    printf("isqrt(1) = %d (rc=%d)\n", result, rc);  /* 1 */

    rc = calc_isqrt(10, &result);
    printf("isqrt(10) = %d (rc=%d)\n", result, rc);  /* 3（向下取整） */

    rc = calc_isqrt(100, &result);
    printf("isqrt(100) = %d (rc=%d)\n", result, rc);  /* 10 */

    rc = calc_isqrt(-4, &result);  /* 负数，应返回 INVALID */
    printf("isqrt(-4) rc=%d (expect %d)\n", rc, CALC_ERR_INVALID);

    rc = calc_isqrt(INT_MAX, &result);
    printf("isqrt(INT_MAX) = %d (rc=%d)\n", result, rc);  /* 46340 */

    /* ---- 新增：gcd 最大公约数 ---- */
    rc = calc_gcd(12, 8, &result);
    printf("gcd(12, 8) = %d (rc=%d)\n", result, rc);  /* 4 */

    rc = calc_gcd(17, 13, &result);
    printf("gcd(17, 13) = %d (rc=%d)\n", result, rc);  /* 1（互质数） */

    rc = calc_gcd(0, 5, &result);
    printf("gcd(0, 5) = %d (rc=%d)\n", result, rc);  /* 5 */

    rc = calc_gcd(5, 0, &result);
    printf("gcd(5, 0) = %d (rc=%d)\n", result, rc);  /* 5 */

    rc = calc_gcd(0, 0, &result);
    printf("gcd(0, 0) = %d (rc=%d)\n", result, rc);  /* 0（约定） */

    rc = calc_gcd(-12, 8, &result);
    printf("gcd(-12, 8) = %d (rc=%d)\n", result, rc);  /* 4（负数取绝对值） */

    rc = calc_gcd(-12, -8, &result);
    printf("gcd(-12, -8) = %d (rc=%d)\n", result, rc);  /* 4（两个负数） */

    rc = calc_gcd(100, 75, &result);
    printf("gcd(100, 75) = %d (rc=%d)\n", result, rc);  /* 25 */

    /* ---- 新增：lcm 最小公倍数 ---- */
    rc = calc_lcm(4, 6, &result);
    printf("lcm(4, 6) = %d (rc=%d)\n", result, rc);  /* 12 */

    rc = calc_lcm(7, 11, &result);
    printf("lcm(7, 11) = %d (rc=%d)\n", result, rc);  /* 77（互质） */

    rc = calc_lcm(12, 4, &result);
    printf("lcm(12, 4) = %d (rc=%d)\n", result, rc);  /* 12（整除） */

    rc = calc_lcm(0, 5, &result);
    printf("lcm(0, 5) = %d (rc=%d)\n", result, rc);  /* 0（零短路） */

    rc = calc_lcm(5, 0, &result);
    printf("lcm(5, 0) = %d (rc=%d)\n", result, rc);  /* 0（零短路） */

    rc = calc_lcm(-4, 6, &result);
    printf("lcm(-4, 6) = %d (rc=%d)\n", result, rc);  /* 12（负数取绝对值） */

    rc = calc_lcm(50000, 49999, &result);
    printf("lcm(50000, 49999) rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_lcm(2, INT_MAX, &result);
    printf("lcm(2, INT_MAX) rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_lcm(INT_MAX, INT_MAX, &result);
    printf("lcm(INT_MAX, INT_MAX) = %d (rc=%d)\n", result, rc);  /* INT_MAX（gcd=INT_MAX） */

    rc = calc_lcm(46341, 46341, &result);
    printf("lcm(46341, 46341) = %d (rc=%d)\n", result, rc);  /* 46341（gcd=46341，不溢出） */

    /* ---- 新增：calc_eval 字符串表达式求值器 ---- */
    rc = calc_eval("1+2", &result);
    printf("calc_eval(\"1+2\") = %d (rc=%d)\n", result, rc);  /* 3 */

    rc = calc_eval("1+2*3", &result);
    printf("calc_eval(\"1+2*3\") = %d (rc=%d)\n", result, rc);  /* 7（优先级） */

    rc = calc_eval("(1+2)*3", &result);
    printf("calc_eval(\"(1+2)*3\") = %d (rc=%d)\n", result, rc);  /* 9 */

    rc = calc_eval("((1+2)*3)+1", &result);
    printf("calc_eval(\"((1+2)*3)+1\") = %d (rc=%d)\n", result, rc);  /* 10（嵌套） */

    rc = calc_eval("-1+2", &result);
    printf("calc_eval(\"-1+2\") = %d (rc=%d)\n", result, rc);  /* 1（一元负号） */

    rc = calc_eval("--2", &result);
    printf("calc_eval(\"--2\") = %d (rc=%d)\n", result, rc);  /* 2（负号右结合） */

    rc = calc_eval("-(1+2)", &result);
    printf("calc_eval(\"-(1+2)\") = %d (rc=%d)\n", result, rc);  /* -3 */

    rc = calc_eval(" 1 + 2 ", &result);
    printf("calc_eval(\" 1 + 2 \") = %d (rc=%d)\n", result, rc);  /* 3（首尾空格） */

    rc = calc_eval("1++2", &result);
    printf("calc_eval(\"1++2\") rc=%d (expect %d SYNTAX)\n", rc, CALC_ERR_SYNTAX);

    rc = calc_eval("1+", &result);
    printf("calc_eval(\"1+\") rc=%d (expect %d SYNTAX)\n", rc, CALC_ERR_SYNTAX);

    rc = calc_eval("(1+2", &result);
    printf("calc_eval(\"(1+2\") rc=%d (expect %d SYNTAX)\n", rc, CALC_ERR_SYNTAX);

    rc = calc_eval("(1)(2)", &result);
    printf("calc_eval(\"(1)(2)\") rc=%d (expect %d SYNTAX)\n", rc, CALC_ERR_SYNTAX);

    rc = calc_eval("1+2a", &result);
    printf("calc_eval(\"1+2a\") rc=%d (expect %d SYNTAX)\n", rc, CALC_ERR_SYNTAX);

    rc = calc_eval("", &result);
    printf("calc_eval(\"\") rc=%d (expect %d SYNTAX)\n", rc, CALC_ERR_SYNTAX);

    rc = calc_eval("1/0", &result);
    printf("calc_eval(\"1/0\") rc=%d (expect %d DIV_ZERO)\n", rc, CALC_ERR_DIV_ZERO);

    rc = calc_eval("1+2147483647", &result);
    printf("calc_eval(\"1+2147483647\") rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    /* ---- 新增：parse_number 整数溢出检查（demo-9 BUG 修复）---- */
    rc = calc_eval("2147483647", &result);
    printf("calc_eval(\"2147483647\") = %d (rc=%d, expect INT_MAX OK)\n", result, rc);

    rc = calc_eval("2147483648", &result);
    printf("calc_eval(\"2147483648\") rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_eval("99999999999", &result);
    printf("calc_eval(\"99999999999\") rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    rc = calc_eval("-2147483648", &result);
    printf("calc_eval(\"-2147483648\") = %d (rc=%d, expect INT_MIN OK)\n", result, rc);

    rc = calc_eval("-99999999999", &result);
    printf("calc_eval(\"-99999999999\") rc=%d (expect %d OVERFLOW)\n", rc, CALC_ERR_OVERFLOW);

    return 0;
}

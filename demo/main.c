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

    return 0;
}

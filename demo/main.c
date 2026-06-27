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

    return 0;
}

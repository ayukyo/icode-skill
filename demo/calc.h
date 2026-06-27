#ifndef CALC_H
#define CALC_H

/*
 * 计算器核心接口
 * 提供基础四则运算，返回 0 表示成功，非 0 表示错误码
 */

/* 错误码定义 */
#define CALC_OK            0  /* 成功 */
#define CALC_ERR_DIV_ZERO  1  /* 除零错误（除法/取模 b==0） */
#define CALC_ERR_INVALID   2  /* 无效操作（未知 op / result==NULL / 幂 exp<0） */
#define CALC_ERR_OVERFLOW  3  /* 整数溢出（加减乘/幂结果超出 int 范围） */

/*
 * 对两个整数做四则运算 + 取模
 * op: '+' '-' '*' '/' '%'
 * 成功时 *result 存结果，返回 CALC_OK
 * 失败返回对应错误码（DIV_ZERO/INVALID/OVERFLOW），不写 *result
 * 注：加减乘会做溢出检查；取模结果 |a%b|<|b| 不会溢出
 */
int calc_basic(int a, int b, char op, int *result);

/*
 * 整数幂运算 base^exp
 * exp < 0：返回 CALC_ERR_INVALID（整数幂不支持负指数）
 * exp == 0：*result = 1
 * exp > 0：累乘，过程中溢出返回 CALC_ERR_OVERFLOW
 * 成功返回 CALC_OK，失败不写 *result
 */
int calc_power(int base, int exp, int *result);

/*
 * 整数开方 ⌊√x⌋（向下取整）
 * x < 0：返回 CALC_ERR_INVALID（负数无实数平方根）
 * x >= 0：*result = 向下取整的整数平方根（如 √10=3，因 3²=9≤10<16=4²）
 * 成功返回 CALC_OK，失败不写 *result
 */
int calc_sqrt(int x, int *result);

#endif /* CALC_H */

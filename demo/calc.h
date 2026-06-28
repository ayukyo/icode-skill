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
#define CALC_ERR_SYNTAX    4  /* 语法错误（calc_eval：表达式格式/字符/括号/长度超限） */

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

/*
 * 整数绝对值 |x|
 * x >= 0：*result = x
 * x < 0（非 INT_MIN）：*result = -x
 * x == INT_MIN：返回 CALC_ERR_OVERFLOW（|INT_MIN| = INT_MAX+1 超出 int 范围）
 * 成功返回 CALC_OK，失败不写 *result
 */
int calc_abs(int x, int *result);

/*
 * 整数平方根 ⌊√x⌋（向下取整）
 * x < 0：返回 CALC_ERR_INVALID（负数无实数平方根）
 * x >= 0：*result = 向下取整的整数平方根（如 √10=3，因 3²=9≤10<16=4²）
 * 成功返回 CALC_OK，失败不写 *result
 * 注：isqrt 与 sqrt 功能完全等价，此处保留 calc_ 前缀以符合 calc_* 命名规范
 */
int calc_isqrt(int x, int *result);

/*
 * 整数最大公约数 gcd(a, b)（欧几里得算法）
 * result == NULL：返回 CALC_ERR_INVALID
 * 正常：*result = gcd(|a|, |b|)，约定 gcd(0, 0) = 0
 * 负数取绝对值后计算（gcd(-4, 6) = gcd(4, 6) = 2）
 * 成功返回 CALC_OK，失败不写 *result
 */
int calc_gcd(int a, int b, int *result);

/*
 * 整数最小公倍数 lcm(a, b)（基于 gcd 的公式 lcm(a,b)=|a*b|/gcd(a,b)）
 * result == NULL：返回 CALC_ERR_INVALID
 * a == 0 或 b == 0：*result = 0（零短路，避免除零）
 * 乘法溢出（lcm > INT_MAX）：返回 CALC_ERR_OVERFLOW
 * 负数取绝对值后计算（lcm(-4, 6) = lcm(4, 6) = 12）
 * 成功返回 CALC_OK，失败不写 *result
 */
int calc_lcm(int a, int b, int *result);

/*
 * 字符串表达式求值器（递归下降 parser）
 * 支持：四则（+ - * /）+ 一元负号 + 括号 + 空格容忍
 * 长度限制：256 字符
 * expr == NULL 或 result == NULL：返回 CALC_ERR_INVALID，不写 *result
 * 语法错误（连续运算符/末尾运算符/括号不匹配/非法字符/空串/长度超限）：返回 CALC_ERR_SYNTAX
 * 除零：返回 CALC_ERR_DIV_ZERO（透传 calc_basic）
 * 溢出：返回 CALC_ERR_OVERFLOW（透传 calc_basic）
 * 成功：*result = 表达式值
 * 错误时 *result 保持原值不修改
 */
int calc_eval(const char *expr, int *result);

#endif /* CALC_H */

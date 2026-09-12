CC = gcc
CFLAGS = -Wall -Wextra -std=c99 -O2
# calc_power exp+base 防御用 log/log2，需 libm
LDFLAGS = -lm

TARGET = calc_demo
SRCS = main.c calc.c
OBJS = $(SRCS:.c=.o)

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS)

%.o: %.c calc.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

# 自检目标：运行演示程序并校验关键用例，供提交前或 CI 快速回归。
# 覆盖阶乘（含溢出）与三角数（含溢出、负数）两组既有能力，断言失败即非零退出。
TEST_OUT = .test_output.tmp
test: $(TARGET)
	@./$(TARGET) > $(TEST_OUT) 2>&1 || { echo "FAIL: $(TARGET) 退出码非 0"; rm -f $(TEST_OUT); exit 1; }
	@grep -q "5! = 120" $(TEST_OUT) || { echo "FAIL: 阶乘用例 5! = 120"; rm -f $(TEST_OUT); exit 1; }
	@grep -q "13! rc=3" $(TEST_OUT) || { echo "FAIL: 阶乘溢出 13! 应返 OVERFLOW"; rm -f $(TEST_OUT); exit 1; }
	@grep -q "T(100) = 5050" $(TEST_OUT) || { echo "FAIL: 三角数 T(100) = 5050"; rm -f $(TEST_OUT); exit 1; }
	@grep -q "T(65536) rc=3" $(TEST_OUT) || { echo "FAIL: 三角数溢出 T(65536) 应返 OVERFLOW"; rm -f $(TEST_OUT); exit 1; }
	@grep -q "T(-1) rc=2" $(TEST_OUT) || { echo "FAIL: 三角数负数 T(-1) 应返 INVALID"; rm -f $(TEST_OUT); exit 1; }
	@rm -f $(TEST_OUT)
	@echo "PASS: calc_demo 全部关键用例通过"

.PHONY: all clean test

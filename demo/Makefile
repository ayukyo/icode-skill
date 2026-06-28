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

.PHONY: all clean

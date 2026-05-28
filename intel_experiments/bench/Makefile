CC      = gcc
# -O2 for optimized streaming; -fno-tree-vectorize keeps inner loops readable
# and prevents auto-vectorization from interfering with MOVNTDQA hand-tuning.
# -march=native enables AVX-512 MOVNTDQA; -g for perf annotation.
CFLAGS  = -O2 -fno-tree-vectorize -march=native -g -Wall -Wextra \
          -I./lib
LDFLAGS = -lnuma -lm

LIB_SRCS = lib/msr.c lib/hugepage.c lib/pmu.c
LIB_OBJS = $(LIB_SRCS:.c=.o)

VICTIM_BIN  = victim/pointer_chase
AGGR_BINS   = aggressor/stream_wb \
              aggressor/stream_wb_nopf \
              aggressor/stream_wc \
              aggressor/stream_wc_nopf \
              aggressor/stream_nt \
              aggressor/random_chase \
              aggressor/forced_turnover

ALL_BINS = $(VICTIM_BIN) $(AGGR_BINS)

.PHONY: all clean

all: $(ALL_BINS)

# Victim
$(VICTIM_BIN): victim/pointer_chase.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

# Aggressors
aggressor/stream_wb: aggressor/stream_wb.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

aggressor/stream_wb_nopf: aggressor/stream_wb_nopf.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

aggressor/stream_wc: aggressor/stream_wc.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

aggressor/stream_wc_nopf: aggressor/stream_wc_nopf.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

aggressor/stream_nt: aggressor/stream_nt.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

aggressor/random_chase: aggressor/random_chase.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

aggressor/forced_turnover: aggressor/forced_turnover.c $(LIB_OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

# Library objects
lib/msr.o: lib/msr.c lib/msr.h
lib/hugepage.o: lib/hugepage.c lib/hugepage.h
lib/pmu.o: lib/pmu.c lib/pmu.h

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(LIB_OBJS) $(ALL_BINS)

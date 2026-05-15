CC       = gcc
CFLAGS   = -O2 -march=native -mavx2 -msse4.1 -Wall -Wextra -pthread
LDFLAGS  = -lnuma -lm -lpthread

SRCDIR   = src
BINDIR   = bin

TARGETS  = $(BINDIR)/aggressor $(BINDIR)/victim $(BINDIR)/validate $(BINDIR)/latency_chase $(BINDIR)/intra_app_corun

.PHONY: all clean kmod kmod-clean

all: $(BINDIR) $(TARGETS)

$(BINDIR):
	mkdir -p $(BINDIR)

$(BINDIR)/aggressor: $(SRCDIR)/aggressor.c $(SRCDIR)/common.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

$(BINDIR)/victim: $(SRCDIR)/victim.c $(SRCDIR)/common.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

$(BINDIR)/validate: $(SRCDIR)/validate_memtype.c $(SRCDIR)/common.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

$(BINDIR)/latency_chase: $(SRCDIR)/latency_chase.c $(SRCDIR)/common.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

$(BINDIR)/intra_app_corun: $(SRCDIR)/intra_app_corun.c $(SRCDIR)/common.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

kmod:
	$(MAKE) -C kmod

kmod-clean:
	$(MAKE) -C kmod clean

clean: kmod-clean
	rm -rf $(BINDIR)

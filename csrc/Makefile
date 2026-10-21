# symfonOS – syp und syp-convert (C)
PREFIX  ?= /usr
DESTDIR ?=
DATADIR ?= $(PREFIX)/share/syp
CC      ?= cc
PKGS    := libarchive libzstd libsodium sqlite3 libcurl
CFLAGS  ?= -O2 -g
ALL_CFLAGS = $(CFLAGS) -std=c11 -Wall -Wextra -Wno-unused-parameter -Wno-sign-compare -D_FORTIFY_SOURCE=2 \
           -fstack-protector-strong -Ilib $(shell pkg-config --cflags $(PKGS)) -DSYP_DATADIR=\"$(DATADIR)\"
LDLIBS  += $(shell pkg-config --libs $(PKGS)) -lm

LIB_SRC := $(wildcard lib/*.c)
LIB_OBJ := $(LIB_SRC:.c=.o)
SYP_OBJ := $(patsubst %.c,%.o,$(wildcard syp/*.c))
CONV_OBJ := $(patsubst %.c,%.o,$(wildcard convert/*.c))

all: bin/syp bin/syp-convert

libsyp.a: $(LIB_OBJ)
	$(AR) rcs $@ $^

bin/syp: $(SYP_OBJ) libsyp.a
	@mkdir -p bin
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

bin/syp-convert: $(CONV_OBJ) libsyp.a
	@mkdir -p bin
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c $(wildcard lib/*.h)
	$(CC) $(ALL_CFLAGS) -c -o $@ $<

install: all
	install -Dm755 bin/syp $(DESTDIR)$(PREFIX)/bin/syp
	install -Dm755 bin/syp-convert $(DESTDIR)$(PREFIX)/bin/syp-convert
	install -Dm644 -t $(DESTDIR)$(DATADIR)/triggers ../system/usr/share/syp/triggers/*.toml
	install -Dm644 ../system/usr/share/syp/convert-map.toml $(DESTDIR)$(DATADIR)/convert-map.toml

clean:
	rm -rf lib/*.o syp/*.o convert/*.o libsyp.a bin

.PHONY: all install clean

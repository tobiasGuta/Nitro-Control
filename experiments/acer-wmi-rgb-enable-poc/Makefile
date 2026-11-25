obj-m += nitro_rgb_enable_poc.o

ifeq ($(EXPERIMENTAL),1)
ccflags-y += -DNITRO_ENABLE_EXPERIMENTAL_PARAMS=1
endif

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD  := $(shell pwd)

.PHONY: all clean

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

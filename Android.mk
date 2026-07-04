LOCAL_PATH := $(call my-dir)
ifeq ($(TARGET_DEVICE),emira)
include $(call all-subdir-makefiles)
endif

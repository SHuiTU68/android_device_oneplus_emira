#
# Copyright (C) 2025 The TWRP Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# ...
#

DEVICE_PATH := device/oplus/emira

# 继承设备配置
$(call inherit-product, $(DEVICE_PATH)/device.mk)
$(call inherit-product, $(DEVICE_PATH)/OrangeFox.mk)

# 产品标识
PRODUCT_DEVICE       := emira
PRODUCT_NAME         := twrp_emira
PRODUCT_BRAND        := OnePlus
PRODUCT_MODEL        := OnePlus Ace5 Ultra
PRODUCT_MANUFACTURER := OPLUS

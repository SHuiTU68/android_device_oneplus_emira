#
# Copyright (C) 2025 The TWRP Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

DEVICE_PATH := device/oplus/OP60EDL1

# 继承设备配置
$(call inherit-product, $(DEVICE_PATH)/device.mk)
$(call inherit-product, $(DEVICE_PATH)/OrangeFox.mk)

# 产品标识
PRODUCT_DEVICE       := OP60EDL1
PRODUCT_NAME         := twrp_OP60EDL1
PRODUCT_BRAND        := OnePlus
PRODUCT_MODEL        := OnePlus Ace5 Ultra
PRODUCT_MANUFACTURER := OPLUS
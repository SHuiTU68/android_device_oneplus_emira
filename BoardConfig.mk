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

# Architecture
# 天玑 9400 实际是 ARMv9 Cortex-X925，但 AOSP 构建系统不认识这些值
# 使用向下兼容的 armv8-2a 和 cortex-a78 作为安全选项
TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_VARIANT := generic
TARGET_CPU_VARIANT_RUNTIME := cortex-a78

TARGET_2ND_ARCH := arm
TARGET_2ND_ARCH_VARIANT := armv7-a-neon
TARGET_2ND_CPU_ABI := armeabi-v7a
TARGET_2ND_CPU_ABI2 := armeabi
TARGET_2ND_CPU_VARIANT := generic
TARGET_2ND_CPU_VARIANT_RUNTIME := cortex-a55

# For building with minimal manifest
ALLOW_MISSING_DEPENDENCIES := true

# Power
ENABLE_CPUSETS := true
ENABLE_SCHEDBOOST := true

# Build Hack
BUILD_BROKEN_DUP_RULES := true
BUILD_BROKEN_ELF_PREBUILT_PRODUCT_COPY_FILES := true

# APEX
OVERRIDE_TARGET_FLATTEN_APEX := true

# Bootloader
TARGET_BOOTLOADER_BOARD_NAME := mt6991
TARGET_NO_BOOTLOADER := true

# Kernel — 使用预编译 DTB，不编译内核源码
TARGET_KERNEL_ARCH := arm64
TARGET_KERNEL_HEADER_ARCH := arm64
TARGET_PREBUILT_DTB := $(DEVICE_PATH)/prebuilt/dtb.img

# Kernel boot image parameters
# 以下所有数值均从原厂 vendor_boot.img 二进制头部解析得出
BOARD_VENDOR_CMDLINE      := bootopt=64S3,32N2,64N2 buildvariant=user
BOARD_KERNEL_BASE         := 0x7fff8000
BOARD_PAGE_SIZE           := 4096
BOARD_KERNEL_OFFSET       := 0x00008000
BOARD_RAMDISK_OFFSET      := 0x26f08000
BOARD_TAGS_OFFSET         := 0x07c88000
BOARD_BOOT_HEADER_VERSION := 4
BOARD_HEADER_SIZE         := 2128
BOARD_DTB_SIZE            := 540944
BOARD_DTB_OFFSET          := 0x07c88000
BOARD_FLASH_BLOCK_SIZE    := 262144  # PAGE_SIZE * 64
BOARD_RAMDISK_USE_LZ4     := true

BOARD_MKBOOTIMG_ARGS += --dtb $(TARGET_PREBUILT_DTB)
BOARD_MKBOOTIMG_ARGS += --vendor_cmdline "$(BOARD_VENDOR_CMDLINE)"
BOARD_MKBOOTIMG_ARGS += --pagesize $(BOARD_PAGE_SIZE) --board ""
BOARD_MKBOOTIMG_ARGS += --kernel_offset $(BOARD_KERNEL_OFFSET)
BOARD_MKBOOTIMG_ARGS += --ramdisk_offset $(BOARD_RAMDISK_OFFSET)
BOARD_MKBOOTIMG_ARGS += --tags_offset $(BOARD_TAGS_OFFSET)
BOARD_MKBOOTIMG_ARGS += --header_version $(BOARD_BOOT_HEADER_VERSION)
BOARD_MKBOOTIMG_ARGS += --dtb_offset $(BOARD_DTB_OFFSET)

BOARD_KERNEL_SEPARATED_DTBO := true
TARGET_NO_KERNEL := true

# Partitions — 物理分区大小（来自 /proc/partitions，单位：字节）
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := 100663296   # sdc43 = 96 MB

# Super / 动态分区
BOARD_SUPER_PARTITION_SIZE   := 14680064000          # sdc94 = 14336000 KB
BOARD_SUPER_PARTITION_GROUPS := main
BOARD_MAIN_SIZE              := 14675869696          # SUPER_SIZE - 4MiB

# 逻辑分区列表
# 注意：AOSP 12 编译系统对该列表有严格的白名单校验，不允许填入 my_* 或 system_dlkm
# 这些非标准分区只要写在 recovery.fstab 和 device.mk 的 AB_OTA_PARTITIONS 里，
# TWRP 运行时同样能通过超级分区头(super header)动态识别并挂载，这里只写 AOSP 标准分区即可欺骗编译系统。
BOARD_MAIN_PARTITION_LIST := \
    system \
    system_ext \
    vendor \
    product \
    odm \
    vendor_dlkm \
    odm_dlkm

TARGET_COPY_OUT_ODM         := odm
TARGET_COPY_OUT_PRODUCT     := product
TARGET_COPY_OUT_SYSTEM      := system
TARGET_COPY_OUT_SYSTEM_EXT  := system_ext
TARGET_COPY_OUT_SYSTEM_DLKM := system_dlkm
TARGET_COPY_OUT_VENDOR      := vendor

# 文件系统类型
BOARD_PRODUCTIMAGE_FILE_SYSTEM_TYPE     := erofs
BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE      := erofs
BOARD_SYSTEM_EXTIMAGE_FILE_SYSTEM_TYPE  := erofs
BOARD_SYSTEM_DLKMIMAGE_FILE_SYSTEM_TYPE := erofs
BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE      := erofs
BOARD_ODMIMAGE_FILE_SYSTEM_TYPE         := erofs
BOARD_USERDATAIMAGE_FILE_SYSTEM_TYPE    := f2fs
BOARD_METADATAIMAGE_FILE_SYSTEM_TYPE    := ext4

# Properties
TARGET_SYSTEM_PROP += $(DEVICE_PATH)/system.prop

# Fstab — Android fs_mgr/libbootloader_message uses this to find /misc for BCB.
# OrangeFox/TWRP reads /etc/twrp.fstab, copied from $(DEVICE_PATH)/twrp.fstab.
TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery.fstab

# System as root
# BOARD_BUILD_SYSTEM_ROOT_IMAGE is obsolete on Android 14+.

# Platform
TARGET_BOARD_PLATFORM := mt6991

# 屏幕分辨率（来自 wm size）
TARGET_SCREEN_HEIGHT  := 2800
TARGET_SCREEN_WIDTH   := 1272

# 屏幕像素密度（来自 wm density）
TARGET_SCREEN_DENSITY := 560

# Recovery
TARGET_NO_RECOVERY := true
BOARD_HAS_LARGE_FILESYSTEM := true
BOARD_HAS_NO_SELECT_BUTTON := true
BOARD_SUPPRESS_SECURE_ERASE := true
TARGET_RECOVERY_PIXEL_FORMAT := RGBX_8888
TARGET_USERIMAGES_USE_EXT4 := true
TARGET_USERIMAGES_USE_F2FS := true

# Verified Boot
BOARD_AVB_ENABLE := true

# Fastbootd
TW_INCLUDE_FASTBOOTD := true

# Crypto — Android 15, metadata-encrypted FBE v2
# /data needs metadata decrypt first; user CE storage may remain locked.
TW_INCLUDE_CRYPTO         := true
TW_INCLUDE_CRYPTO_FBE     := true
BOARD_USES_METADATA_PARTITION    := true
TW_INCLUDE_FBE_METADATA_DECRYPT  := true
TW_USE_FSCRYPT_POLICY     := 2
# OF_SKIP_FBE_DECRYPTION    := 1
# 以下三个值设为 9999 是 TWRP 的惯用做法，绕过 AVB 时间戳校验
PLATFORM_VERSION              := 99.87.36
PLATFORM_VERSION_LAST_STABLE  := $(PLATFORM_VERSION)
PLATFORM_SECURITY_PATCH       := 2099-12-31
BOOT_SECURITY_PATCH           := $(PLATFORM_SECURITY_PATCH)
VENDOR_SECURITY_PATCH         := $(PLATFORM_SECURITY_PATCH)

# TWRP 界面配置
TW_THEME              := portrait_hdpi
TW_EXTRA_LANGUAGES    := false
TW_SCREEN_BLANK_ON_BOOT := true
TW_INPUT_BLACKLIST    := "hbtp_vm"
TW_USE_TOOLBOX        := true
TW_INCLUDE_REPACKTOOLS := true

# 文件系统工具
TW_INCLUDE_NTFS_3G  := false
TARGET_USES_MKE2FS  := true
TW_INCLUDE_FUSE_NTFS  := false
TW_INCLUDE_FUSE_EXFAT := false

# 调试工具（开发期间保持开启）
TARGET_USES_LOGD     := true
TWRP_INCLUDE_LOGCAT  := true
TWRP_EVENT_LOGGING   := true

# 内置工具
TW_INCLUDE_RESETPROP     := true
TW_INCLUDE_LIBRESETPROP  := true
TW_INCLUDE_LPDUMP        := true
TW_INCLUDE_LPTOOLS       := true

# 状态栏指示器
TW_CUSTOM_CPU_TEMP_PATH     := /sys/class/power_supply/battery/temp
TW_BATTERY_SYSFS_WAIT_SECONDS := 6

# TWRP 扩展配置
TW_FRAMERATE      := 120
TW_DEFAULT_LANGUAGE     := en
# 亮度值来自 /sys/class/leds/lcd-backlight/max_brightness = 4094
TW_DEFAULT_BRIGHTNESS   := 1000
TW_MAX_BRIGHTNESS       := 4094
TW_DEVICE_VERSION       := OP60EDL1
TW_EXCLUDE_APEX         := true
TW_NO_LEGACY_PROPS      := true
TW_NO_BIND_SYSTEM       := true
TW_BACKUP_EXCLUSIONS    := /data/fonts
TW_USE_SERIALNO_PROPERTY_FOR_DEVICE_ID := true
RECOVERY_SDCARD_ON_DATA := true
TW_EXCLUDE_DEFAULT_USB_INIT := true
TW_PREPARE_DATA_MEDIA_EARLY := false
TW_USE_NEW_MINADBD      := true

# USB Mass Storage 路径（与 qqcandy 相同，MTK 通用）
TARGET_USE_CUSTOM_LUN_FILE_PATH := /config/usb_gadget/g1/functions/mass_storage.usb0/lun.%d/file

# Boot Control HAL — MT6991 使用 AIDL 接口（非 HIDL）
# 原厂二进制名称：android.hardware.boot-service.mtk
# 这与 qqcandy（MT6895）不同，qqcandy 用 HIDL @1.2-mtkimpl
# 需要从原厂 vendor 分区提取以下文件：
#   /vendor/bin/hw/android.hardware.boot-service.mtk
#   /vendor/etc/vintf/manifest/android.hardware.boot-service.mtk.xml (如有)
#   /vendor/lib64/hw/ 下的 boot HAL .so 文件
# 注意：qqcandy 仓库的 bootctrl/ 目录代码是 HIDL 的，不能直接用于 MT6991
BOARD_BOOT_CONTROL_HAL := android.hardware.boot-service.mtk

# Vendor Boot（Recovery 打包进 vendor_boot）
# 参考 rubens TWRP: 使用 GKI 模式，让构建系统自动创建 vendor ramdisk (platform)，
# 确保 lib/modules 进入 ramdisk.cpio (platform) 而非 recovery.cpio (recovery)
BOARD_USES_VENDOR_BOOT              := true
BOARD_BUILD_VENDOR_BOOT             := true
BOARD_USES_GENERIC_KERNEL_IMAGE     := true
BOARD_MOVE_RECOVERY_RESOURCES_TO_VENDOR_BOOT := true
BOARD_INCLUDE_RECOVERY_RAMDISK_IN_VENDOR_BOOT := true
BOARD_VENDOR_RAMDISK_DIR            := $(DEVICE_PATH)/vendor_ramdisk
BOARD_VENDOR_RAMDISK_COMPRESSION    := lz4
BOARD_VENDOR_RAMDISK_USE_LZ4        := true
BOARD_VENDOR_RAMDISK_FSTAB          := $(DEVICE_PATH)/vendor_ramdisk/first_stage_ramdisk/fstab.mt6991
BOARD_USES_GENERIC_VENDOR_RAMDISK   := false  # 使用自定义 vendor_ramdisk 而非 GKI 通用版本

# 显式声明 vendor ramdisk 中的内核模块，确保构建系统将它们放入 platform ramdisk
BOARD_VENDOR_RAMDISK_KERNEL_MODULES := $(wildcard $(DEVICE_PATH)/vendor_ramdisk/lib/modules/*.ko)

# se_omapi
TW_INCLUDE_OMAPI := true

# Delete modules
TW_EXCLUDE_NANO := true
TW_EXCLUDE_BASH := true
TW_EXCLUDE_LPDUMP := true

# 振动马达支持（从 Redmi K50 TWRP 参考）
# aw8697.ko 为 AWINIC 触觉驱动，haptic.ko/haptic_feedback.ko 为 MTK 通用振动驱动
# 内核模块在 modules.load.recovery 中已列出，TWRP 会自动加载
TW_LOAD_VENDOR_MODULES := "aw8697.ko haptic.ko haptic_feedback.ko"
TW_SUPPORT_INPUT_AIDL_HAPTICS := true

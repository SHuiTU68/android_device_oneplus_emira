#
# Copyright (C) 2025 The TWRP Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# ...
#

DEVICE_PATH := device/oplus/OP60EDL1

$(call inherit-product, $(SRC_TARGET_DIR)/product/base.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit_only.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/virtual_ab_ota.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/virtual_ab_ota/compression.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/emulated_storage.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/virtual_ab_ota/launch_with_vendor_ramdisk.mk)
$(call inherit-product, vendor/twrp/config/common.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/developer_gsi_keys.mk)

# Enable Fuse Passthrough
PRODUCT_PROPERTY_OVERRIDES += persist.sys.fuse.passthrough.enable=true

PRODUCT_SHIPPING_API_LEVEL := 34
PRODUCT_TARGET_VNDK_VERSION := 34

# TWRP in Vendor Boot
PRODUCT_PROPERTY_OVERRIDES += ro.twrp.vendor_boot=true

# Stock vendor ramdisk essentials for the platform fragment in vendor_boot v4.
# BOARD_VENDOR_RAMDISK_DIR 在 AOSP 14 中不再自动复制文件，
# 需要显式用 PRODUCT_COPY_FILES 将 vendor_ramdisk 中的所有文件复制到 vendor ramdisk。
# 注意：内核模块必须放在 platform ramdisk 中，不能通过 PRODUCT_COPY_FILES 复制到 recovery ramdisk。
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/vendor_ramdisk/first_stage_ramdisk/fstab.mt6991:vendor_ramdisk/first_stage_ramdisk/fstab.mt6991 \
    $(foreach f,$(shell cd $(DEVICE_PATH)/vendor_ramdisk && find . -type f ! -path './first_stage_ramdisk/*'),$(DEVICE_PATH)/vendor_ramdisk/$(f):vendor_ramdisk/$(f)) \
    $(DEVICE_PATH)/twrp.fstab:recovery/root/system/etc/twrp.fstab \
    $(DEVICE_PATH)/recovery/root/system/etc/vintf/manifest.xml:recovery/root/system/etc/vintf/manifest.xml \
    $(DEVICE_PATH)/recovery/root/vendor/manifest.xml:recovery/root/vendor/manifest.xml \
    $(DEVICE_PATH)/recovery/root/vendor/etc/vintf/manifest.xml:recovery/root/vendor/etc/vintf/manifest.xml \
    $(DEVICE_PATH)/recovery/root/vendor/etc/ueventd.rc:recovery/root/vendor/etc/ueventd.rc \
    system/core/libprocessgroup/profiles/task_profiles.json:recovery/root/system/etc/task_profiles.json

# A/B
AB_OTA_UPDATER := true
ENABLE_VIRTUAL_AB := true
TARGET_ENFORCE_AB_OTA_PARTITION_LIST := true

# 物理 A/B 分区（来自 /dev/block/by-name 的实际分区列表）
AB_OTA_PARTITIONS := \
    boot \
    init_boot \
    vendor_boot \
    dtbo \
    vbmeta \
    vbmeta_system \
    vbmeta_vendor \
    system \
    system_ext \
    system_dlkm \
    vendor \
    product \
    odm \
    odm_dlkm \
    vendor_dlkm \
    lk \
    preloader_raw \
    modem \
    audio_dsp \
    apusys \
    tee \
    mcupm \
    spmfw \
    sspm \
    ccu \
    scp \
    vcp \
    gz \
    dpm \
    gpueb \
    mvpu_algo \
    mcf_ota \
    pi_img \
    connsys_bt \
    connsys_wifi \
    connsys_gnss \
    pvmfw \
    ise \
    rotfw \
    cdt_engineering

# OPLUS my_* 逻辑分区（在 super 内，/dev/block/mapper/ 下可见）
AB_OTA_PARTITIONS += \
    my_bigball \
    my_carrier \
    my_company \
    my_engineering \
    my_heytap \
    my_manifest \
    my_preload \
    my_product \
    my_region \
    my_stock

# Dynamic Partitions
PRODUCT_USE_DYNAMIC_PARTITIONS := true

# A/B Post-install
AB_OTA_POSTINSTALL_CONFIG += \
    RUN_POSTINSTALL_system=true \
    POSTINSTALL_PATH_system=system/bin/otapreopt_script \
    FILESYSTEM_TYPE_system=ext4 \
    POSTINSTALL_OPTIONAL_system=true

# Boot Control HAL — MT6991 使用 AIDL 接口
# 需要从原厂 vendor 分区提取 android.hardware.boot-service.mtk 二进制
# 放入 recovery/root/system/bin/ 或 prebuilt/ 目录
PRODUCT_PACKAGES += \
    android.hardware.boot-service.mtk

PRODUCT_PACKAGES_DEBUG += \
    bootctrl

# OTA 引擎
#PRODUCT_PACKAGES += \
#    update_engine \
#    update_engine_sideload \
#    update_verifier \
#    checkpoint_gc

# Boot HAL AIDL 版本声明
PRODUCT_PACKAGES += \
    android.hardware.boot-V1-ndk

# Fastbootd
PRODUCT_PACKAGES += \
    fastbootd \
    android.hardware.fastboot@1.0 \
    android.hardware.fastboot@1.1

# Health（HIDL 供 recovery UI 电池显示，AIDL 供 fastbootd）
PRODUCT_PACKAGES += \
    android.hardware.health@2.1-impl \
    android.hardware.health@2.1-service \
    android.hardware.health-service.example_recovery

# AOSP 构建默认把 VINTF fragment 装到 /system/etc/vintf/manifest/（framework 目录），
# 但 fragment 是 device 类型，必须在 Android.bp 中加 vintf_fragments: [] 禁止自动安装。
# 然后手动放到正确的 vendor 目录：
PRODUCT_COPY_FILES += \
    hardware/interfaces/health/aidl/default/android.hardware.health-service.example.xml:recovery/root/vendor/etc/vintf/manifest/android.hardware.health-service.example.xml

# Keymaster / KeyMint (Trustonic TEE)
PRODUCT_PACKAGES += \
    android.hardware.keymaster@3.0 \
    android.hardware.keymaster@4.0 \
    android.hardware.keymaster@4.1

PRODUCT_PACKAGES += \
    android.hardware.security.keymint-V1-ndk_platform \
    android.hardware.security.secureclock-V1-ndk_platform \
    android.hardware.security.sharedsecret-V1-ndk_platform

# 等待 KeyMint 服务就绪（解密 data 必须）
PRODUCT_PACKAGES += \
    wait_for_keymaster

# MTK 平台路径工具（为 Recovery 创建块设备软链接）
PRODUCT_PACKAGES += \
    mtk_plpath_utils \
    mtk_plpath_utils.recovery

# Soong namespaces
PRODUCT_SOONG_NAMESPACES += $(DEVICE_PATH)

# OMAPI bridge for Weaver-based FBE decryption
PRODUCT_PACKAGES += \
    se_omapi

# Fix MTP and dmsetup
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/recovery/root/init.recovery.usb.rc:recovery/root/init.recovery.usb.rc \
    vendor/recovery/prebuilt/arm64/dmsetup:recovery/root/system/bin/dmsetup
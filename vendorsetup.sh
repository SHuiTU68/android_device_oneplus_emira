# AOSP 14.1 同时支持两种注册方式：
# 1. AndroidProducts.mk 的 COMMON_LUNCH_CHOICES（构建系统扫描用）
# 2. add_lunch_combo（envsetup.sh shell 函数，lunch 菜单用）
# 两者都保留以保证兼容性
add_lunch_combo twrp_OP60EDL1-eng

export FOX_MOVE_MAGISK_INSTALLER_TO_RAMDISK=0
export FOX_ENABLE_KERNELSU_SUPPORT=0
export FOX_ENABLE_KERNELSU_NEXT_SUPPORT=0
export FOX_ENABLE_SUKISU_SUPPORT=0
export FOX_USE_PATCHELF_BINARY=0
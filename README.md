# android_device_oneplus_emira

OnePlus Ace5 Ultra (emira) — MT6991 (Dimensity 9400) TWRP / OrangeFox 设备树

## 2026-08-04 修复

1. VINTF manifest 补 HAL 声明 (recovery/root/vendor/etc/vintf/manifest.xml)
   - 新增 keymint / rkp / secureclock / sharedsecret / gatekeeper / boot / health 的 AIDL 声明
   - 修复: /data 无法挂载 (metadata 加密解密依赖 keymint 服务注册成功)
   - 修复: fastbootd getvar current-slot 为空 (boot HAL 服务注册成功)

2. twrp.fstab — /data 行移除 MTK 私有 inlinecrypt_optimized / fsverity 参数,
   保留 fileencryption=aes-256-xts:aes-256-cts:v2 + keydirectory=/metadata/vold/metadata_encryption

3. init.recovery.mt6991.rc — keymint 改为在 init.svc.mobicore=running (TEE 就绪) 后启动,
   避免 keymint 先于 mcDriverDaemon 启动导致连接失败退出

4. tools/unpack_vendor_boot.py — MTK vendor_boot v4 双 ramdisk 正确解包工具
   - 修复 ramdisk.cpio 解出 0B / 打不开 (兼容 lz4 legacy 0x184C2102)
   - 修复只解出 ramdisk.cpio、缺 recovery.cpio (解析 vendor_ramdisk 段内 ramdisk 表)

## 解包 vendor_boot

    python3 tools/unpack_vendor_boot.py vendor_boot.img out/

    out/platform_ramdisk/   原厂 first_stage / vendor ramdisk
    out/recovery_ramdisk/   原厂 recovery ramdisk

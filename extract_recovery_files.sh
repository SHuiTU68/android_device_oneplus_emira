#!/system/bin/sh
# ============================================================
# MT6991 Recovery 文件提取脚本
# 在 adb shell 里以 root 运行
# 产出：/sdcard/recovery_root_files.tar.gz
# 解压到设备树的 recovery/root/ 目录即可
# ============================================================

set -e

WORK="/data/local/tmp/recovery_extract"
FAIL=0

log() { echo "[*] $1"; }
warn() { echo "[!] $1"; FAIL=$((FAIL + 1)); }

# 清理旧数据
rm -rf "$WORK"
mkdir -p "$WORK"

# ── 1. tee.rc / trustonic.rc（放在根目录，init.rc import /tee.rc） ──
log "提取 tee.rc 和 trustonic.rc"
for f in tee.rc trustonic.rc; do
    if [ -f "/vendor/etc/init/$f" ]; then
        cp "/vendor/etc/init/$f" "$WORK/$f"
        log "  ✓ $f"
    else
        warn "  ✗ /vendor/etc/init/$f 不存在"
    fi
done

# ── 2. vendor/bin/ — 服务二进制 ──
log "提取 vendor 服务二进制"
mkdir -p "$WORK/vendor/bin/hw"

BINS="
/vendor/bin/mcDriverDaemon
/vendor/bin/hw/android.hardware.security.keymint@3.0-service.trustonic
/vendor/bin/hw/android.hardware.gatekeeper-service.trustonic
/vendor/bin/hw/android.hardware.boot-service.mtk
"

for f in $BINS; do
    if [ -f "$f" ]; then
        # 保持相对路径结构
        dest="$WORK/vendor/${f#/vendor/}"
        mkdir -p "$(dirname "$dest")"
        cp "$f" "$dest"
        log "  ✓ $(basename $f)"
    else
        warn "  ✗ $f 不存在"
    fi
done

# ── 3. vendor/lib64/ — 加密服务依赖库（来自 /proc/maps 分析） ──
log "提取 vendor 共享库"
mkdir -p "$WORK/vendor/lib64"

LIBS="
android.hardware.security.keymint-V3-ndk.so
android.hardware.security.rkp-V3-ndk.so
android.hardware.security.secureclock-V1-ndk.so
android.hardware.security.sharedsecret-V1-ndk.so
android.hardware.gatekeeper-V1-ndk.so
libMcClient.so
libhidlbase.so
libcrypto.so
libbase.so
libc++.so
libcutils.so
libutils.so
libmtk_bsg.so
"

for f in $LIBS; do
    if [ -f "/vendor/lib64/$f" ]; then
        cp "/vendor/lib64/$f" "$WORK/vendor/lib64/$f"
        log "  ✓ $f"
    else
        warn "  ✗ /vendor/lib64/$f 不存在"
    fi
done

# ── 4. vendor/app/mcRegistry/ — Trustonic TEE 驱动 ──
log "提取 mcRegistry .drbin 文件"
mkdir -p "$WORK/vendor/app/mcRegistry"

if [ -d "/vendor/app/mcRegistry" ]; then
    cp /vendor/app/mcRegistry/*.drbin "$WORK/vendor/app/mcRegistry/" 2>/dev/null
    COUNT=$(ls "$WORK/vendor/app/mcRegistry/" | wc -l)
    log "  ✓ 共 $COUNT 个 .drbin 文件"
else
    warn "  ✗ /vendor/app/mcRegistry/ 不存在"
fi

# ── 5. odm 下的 mcRegistry（trustonic.rc 引用了这些） ──
log "提取 odm mcRegistry .drbin 文件"
mkdir -p "$WORK/odm/vendor/app/mcRegistry"

ODM_DRBINS="
030c0000000000000000000000000000.drbin
09070000000000000000000000000000.drbin
035c0000000000000000000000000000.drbin
033c0000000000000000000000000000.drbin
6b3f5fa0f8cf55a7be2582587d62d63a.drbin
031c0000000000000000000000000000.drbin
"

ODM_COUNT=0
for f in $ODM_DRBINS; do
    if [ -f "/odm/vendor/app/mcRegistry/$f" ]; then
        cp "/odm/vendor/app/mcRegistry/$f" "$WORK/odm/vendor/app/mcRegistry/$f"
        ODM_COUNT=$((ODM_COUNT + 1))
    else
        warn "  ✗ /odm/vendor/app/mcRegistry/$f 不存在"
    fi
done
log "  ✓ 共 $ODM_COUNT 个 odm drbin 文件"

# ── 6. persist 目录结构（空目录，供 mobicore 运行时使用） ──
log "创建 persist 挂载点目录"
mkdir -p "$WORK/mnt/vendor/persist/mcRegistry"

# ── 打包 ──
log "打包中..."
cd "$WORK"
tar czf /sdcard/recovery_root_files.tar.gz .
SIZE=$(ls -l /sdcard/recovery_root_files.tar.gz | awk '{print $5}')
log "============================================"
log "完成！文件大小: $SIZE 字节"
if [ "$FAIL" -gt 0 ]; then
    log "⚠ 有 $FAIL 个文件缺失，请检查上方警告"
fi
log "产出: /sdcard/recovery_root_files.tar.gz"
log ""
log "后续操作："
log "  adb pull /sdcard/recovery_root_files.tar.gz"
log "  cd device/oplus/emira/recovery/root/"
log "  tar xzf recovery_root_files.tar.gz"
log "============================================"

# 清理临时目录
rm -rf "$WORK"

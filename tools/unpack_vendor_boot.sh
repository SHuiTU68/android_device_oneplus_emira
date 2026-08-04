#!/system/bin/sh
# ============================================================
# unpack_vendor_boot.sh - MTK/AOSP vendor_boot v4 双 ramdisk 解包
# 手机 root 环境版 (toybox / magiskboot)
#
# 解决的问题:
#   1. ramdisk.cpio 解出 0 字节/打不开
#      MTK 用 lz4 legacy (magic 0x184C2102), 与新版 lz4 frame
#      (magic 0x184D2204) 不兼容, 旧工具按 frame 解压失败 -> 0B
#   2. 只解出 ramdisk.cpio, 没有 recovery.cpio
#      vendor_boot v4 的 vendor_ramdisk 段内部是 ramdisk 表:
#      表头条目 + N 个条目 (platform/recovery/dlkm)
#
# 用法:
#   sh unpack_vendor_boot.sh <vendor_boot.img> [输出目录]
#   示例: sh unpack_vendor_boot.sh /sdcard/Download/vendor_boot.img /sdcard/Download/vb_out
#
# 依赖: toybox(dd/od/cpio/lz4/gzip/tr/truncate) 或 magiskboot(推荐, 自动处理 lz4 legacy)
# ============================================================

IMG="$1"
OUT="${2:-vendor_boot_out}"
TMP="/data/local/tmp/vb_unpack_$$"

[ -n "$IMG" ] && [ -f "$IMG" ] || { echo "[!] 用法: sh $0 <vendor_boot.img> [输出目录]"; exit 1; }
mkdir -p "$OUT" "$TMP" 2>/dev/null || { echo "[!] 无法创建目录 (需要 root)"; exit 1; }
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

echo "=============================================="
echo "[*] 镜像: $IMG"
echo "[*] 输出: $OUT"

# ---------- 读取小端整数 ----------
read_u32() { od -An -tu4 -j "$2" -N 4 "$1" 2>/dev/null | tr -d ' \t'; }
read_u64() { od -An -tu8 -j "$2" -N 8 "$1" 2>/dev/null | tr -d ' \t'; }
align()   { echo $(( ( $1 + $2 - 1 ) / $2 * $2 )); }

# ---------- 检查 magic ----------
MAGIC=$(dd if="$IMG" bs=1 count=8 2>/dev/null)
if [ "$MAGIC" != "VNDRBOOT" ]; then
    echo "[!] 不是 vendor_boot 镜像 (magic != VNDRBOOT)"
    exit 1
fi

# ---------- 优先 magiskboot ----------
if command -v magiskboot >/dev/null 2>&1; then
    echo "[*] 检测到 magiskboot, 用它解包 (支持 lz4 legacy)"
    ( cd "$OUT" && magiskboot unpack "$IMG" ) || { echo "[!] magiskboot unpack 失败"; exit 1; }
    for RD in ramdisk recovery; do
        if [ -f "$OUT/$RD.cpio" ]; then
            mkdir -p "$OUT/${RD}_ramdisk"
            cp "$OUT/$RD.cpio" "$OUT/${RD}_ramdisk/ramdisk.cpio"
            ( cd "$OUT/${RD}_ramdisk" && cpio -idmu --no-absolute-filenames < ramdisk.cpio ) 2>/dev/null
            echo "[+] ${RD}_ramdisk/ 解包完成"
        elif [ -f "$OUT/$RD.cpio.lz4" ]; then
            mkdir -p "$OUT/${RD}_ramdisk"
            magiskboot decompress "$OUT/$RD.cpio.lz4" "$OUT/${RD}_ramdisk/ramdisk.cpio" 2>/dev/null \
                && ( cd "$OUT/${RD}_ramdisk" && cpio -idmu --no-absolute-filenames < ramdisk.cpio ) 2>/dev/null
            echo "[+] ${RD}_ramdisk/ 解包完成 (经 magiskboot decompress)"
        fi
    done
    echo "=============================================="
    echo "[*] 完成: platform_ramdisk/ + recovery_ramdisk/ + dtb + recovery_dtbo"
    exit 0
fi

# ---------- 手动解析 vendor_boot v4 header ----------
PAGE=$(read_u32 "$IMG" 12)          # page_size @12
HSIZE=$(read_u32 "$IMG" 2096)       # header_size @2096
VRSIZE=$(read_u32 "$IMG" 24)        # vendor_ramdisk_size @24
DTBSIZE=$(read_u32 "$IMG" 2100)     # dtb_size @2100
RDTBOSIZE=$(read_u32 "$IMG" 2116)   # recovery_dtbo_size @2116
RDTBOOFF=$(read_u64 "$IMG" 2120)    # recovery_dtbo_offset @2120
VRSTART=$(align "$HSIZE" "$PAGE")

echo "[*] page_size=$PAGE header_size=$HSIZE"
echo "[*] vendor_ramdisk: offset=$VRSTART size=$VRSIZE"
echo "[*] recovery_dtbo: offset=$RDTBOOFF size=$RDTBOSIZE"
echo "[*] dtb: size=$DTBSIZE"

# 提取 vendor_ramdisk 段 (页对齐)
dd if="$IMG" of="$TMP/vr.bin" bs="$PAGE" skip=$((VRSTART/PAGE)) count=$(( (VRSIZE+PAGE-1)/PAGE )) 2>/dev/null
[ -s "$TMP/vr.bin" ] || { echo "[!] 提取 vendor_ramdisk 失败"; exit 1; }
truncate -s "$VRSIZE" "$TMP/vr.bin" 2>/dev/null

COUNT=$(od -An -tu4 -N 4 "$TMP/vr.bin" | tr -d ' \t')
echo "[*] vendor_ramdisk 内 ramdisk 条目数: $COUNT"

process_ramdisk() {
    # $1=name $2=type $3=offset $4=size
    NAME=$1; TYPE=$2; OFF=$3; SIZE=$4
    [ "$SIZE" -gt 0 ] 2>/dev/null || { echo "[!] 条目 '$NAME' 大小为 0, 跳过"; return; }
    dd if="$TMP/vr.bin" of="$TMP/rd.bin" bs="$PAGE" skip=$((OFF/PAGE)) count=$(( (SIZE+PAGE-1)/PAGE )) 2>/dev/null
    truncate -s "$SIZE" "$TMP/rd.bin" 2>/dev/null
    # 检测压缩格式
    M4=$(dd if="$TMP/rd.bin" bs=1 count=4 2>/dev/null | od -An -tx1 | tr -d ' \n')
    case "$M4" in
        04224d18) C="lz4_frame" ;;
        02214c18) C="lz4_legacy" ;;
        1f8b*)    C="gzip" ;;
        28b52ffd) C="zstd" ;;
        *)        C="raw" ;;
    esac
    case "$TYPE" in
        1) D="$OUT/platform_ramdisk" ;;
        2) D="$OUT/recovery_ramdisk" ;;
        3) D="$OUT/dlkm_ramdisk" ;;
        *) D="$OUT/ramdisk_$NAME" ;;
    esac
    mkdir -p "$D"
    case "$C" in
        raw)
            cp "$TMP/rd.bin" "$D/ramdisk.cpio" ;;
        lz4_frame)
            lz4 -d "$TMP/rd.bin" "$D/ramdisk.cpio" 2>/dev/null \
                || cp "$TMP/rd.bin" "$D/ramdisk.cpio.lz4" ;;
        lz4_legacy)
            lz4 -d --no-frame-crc "$TMP/rd.bin" "$D/ramdisk.cpio" 2>/dev/null \
                || lz4 -d "$TMP/rd.bin" "$D/ramdisk.cpio" 2>/dev/null \
                || cp "$TMP/rd.bin" "$D/ramdisk.cpio.lz4" ;;
        gzip)
            gzip -dc "$TMP/rd.bin" > "$D/ramdisk.cpio" 2>/dev/null \
                || cp "$TMP/rd.bin" "$D/ramdisk.cpio.gz" ;;
        zstd)
            zstd -dc "$TMP/rd.bin" > "$D/ramdisk.cpio" 2>/dev/null \
                || cp "$TMP/rd.bin" "$D/ramdisk.cpio.zst" ;;
    esac
    if [ -f "$D/ramdisk.cpio" ]; then
        ( cd "$D" && cpio -idmu --no-absolute-filenames < ramdisk.cpio ) 2>/dev/null
        echo "[+] $D: 压缩=$C, 已生成 ramdisk.cpio 并解包"
    else
        echo "[!] $D: 解压失败, 已保留原始压缩文件 (装 lz4 或 magiskboot 可解)"
    fi
}

if [ -n "$COUNT" ] && [ "$COUNT" -ge 1 ] && [ "$COUNT" -le 64 ] 2>/dev/null; then
    i=0
    while [ "$i" -lt "$COUNT" ]; do
        OFF=$(( 108 + i*108 ))
        ESIZE=$(read_u32 "$TMP/vr.bin" "$OFF")
        EOFF=$(read_u32 "$TMP/vr.bin" $((OFF+4)))
        ETYPE=$(read_u32 "$TMP/vr.bin" $((OFF+8)))
        ENAME=$(dd if="$TMP/vr.bin" bs=1 skip=$((OFF+12)) count=32 2>/dev/null | tr -d '\000')
        echo "[*] 条目 $i: name='$ENAME' type=$ETYPE offset=$EOFF size=$ESIZE"
        process_ramdisk "$ENAME" "$ETYPE" "$EOFF" "$ESIZE"
        i=$((i+1))
    done
else
    echo "[*] 未检测到 ramdisk 表, 按单个 ramdisk 处理"
    process_ramdisk "platform" 1 0 "$VRSIZE"
fi

# ---------- recovery_dtbo / dtb ----------
if [ "$RDTBOSIZE" -gt 0 ] 2>/dev/null; then
    dd if="$IMG" of="$OUT/recovery_dtbo" bs="$PAGE" skip=$((RDTBOOFF/PAGE)) count=$(( (RDTBOSIZE+PAGE-1)/PAGE )) 2>/dev/null
    truncate -s "$RDTBOSIZE" "$OUT/recovery_dtbo" 2>/dev/null
    echo "[+] recovery_dtbo: $RDTBOSIZE B"
    DTBOFF=$(align $((RDTBOOFF+RDTBOSIZE)) "$PAGE")
else
    DTBOFF=$(align $((VRSTART+VRSIZE)) "$PAGE")
fi
if [ "$DTBSIZE" -gt 0 ] 2>/dev/null; then
    dd if="$IMG" of="$OUT/dtb" bs="$PAGE" skip=$((DTBOFF/PAGE)) count=$(( (DTBSIZE+PAGE-1)/PAGE )) 2>/dev/null
    truncate -s "$DTBSIZE" "$OUT/dtb" 2>/dev/null
    echo "[+] dtb: $DTBSIZE B"
fi

echo "=============================================="
echo "[*] 完成:"
echo "    $OUT/platform_ramdisk/   原厂 first_stage/vendor ramdisk"
echo "    $OUT/recovery_ramdisk/   原厂 recovery ramdisk"
exit 0

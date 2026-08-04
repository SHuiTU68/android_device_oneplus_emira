#!/system/bin/sh
# ============================================================================
# unpack_vendor_boot.sh  (MTK vendor_boot 解包脚本 - 修正版)
# ----------------------------------------------------------------------------
# 适用: OnePlus Ace5 Ultra (emira) / MT6991 (Dimensity 9400) / Android 15+
#       及其他 MTK 平台 vendor_boot v4 (VNDRBOOT, header_size=2128)
#
# 背景: MTK 的 vendor_boot v4 与 AOSP 标准布局完全不同!
#   * 无 vendor_ramdisk table (旧脚本把 lz4 magic 当表头解析 -> 条目数
#     407642370 = 0x184C2102 就是 lz4 legacy magic 的小端值!)
#   * vendor_ramdisk 段 = 两个 lz4 legacy 流顺序拼接:
#        流1: platform ramdisk   (系统/所有启动模式共用, 本例 114349312 B)
#        流2: recovery ramdisk   (recovery 模式专用, 本例 11506688 B)
#   * lz4 legacy 块头是 小端 u32 (标准 LZ4 legacy 是大端! 因此 magiskboot /
#     标准 lz4 工具直接解会失败)
#   * 无 dtb 段 (dtb_size/dtb_addr 字段是无效兼容字段, dtb 在 boot 分区)
#   * header_size=2128, 无 bootconfig[2048] 和 recovery_dtbo 字段区
#
# 用法:
#   sh unpack_vendor_boot.sh [输入.img] [输出目录]
#   默认: 输入 /sdcard/Download/vendor_boot.img, 输出 /sdcard/Download/vb_out
#
# 输出:
#   <输出>/ramdisk.cpio    = 流1 platform ramdisk (解压后 cpio)
#   <输出>/recovery.cpio   = 流2 recovery ramdisk (解压后 cpio, TWRP 替换它)
#
# 依赖: python3 (Termux 可装, pkg install python3; 或系统自带)
#       解压速度: platform ~15s, recovery ~2s (天玑9400 更快)
# ============================================================================

IMG="${1:-/sdcard/Download/vendor_boot.img}"
OUT="${2:-/sdcard/Download/vb_out}"

echo "== MTK vendor_boot 解包 (修正版) =="
echo "[*] 镜像: $IMG"
echo "[*] 输出: $OUT"

# --- 检测 python3 ----------------------------------------------------------
PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
fi
if [ -z "$PY" ]; then
    echo "[!] 未找到 python3。"
    echo "    方案1: Termux 安装 -> pkg install python3"
    echo "    方案2: 安装 magiskboot 后重试 (自动降级为 magiskboot 路径)"
    if command -v magiskboot >/dev/null 2>&1; then
        echo "[*] 使用 magiskboot 降级路径 (注意: 若 MTK 小端块头, magiskboot 可能失败)"
    else
        echo "[!] 请先安装 python3 (Termux) 或 magiskboot"
        exit 1
    fi
fi

# --- 主流程: python3 路径 ---------------------------------------------------
if [ -n "$PY" ]; then
    "$PY" - "$IMG" "$OUT" <<'PYEOF'
import struct, sys, os, time

img_path, out_dir = sys.argv[1], sys.argv[2]
data = open(img_path, 'rb').read()
t0 = time.time()

# ---- 1. 校验 header ----
assert data[:8] == b'VNDRBOOT', '不是 vendor_boot 镜像 (magic != VNDRBOOT)'
hdr_ver   = struct.unpack_from('<I', data, 8)[0]
page_size = struct.unpack_from('<I', data, 12)[0]
hdr_size  = struct.unpack_from('<I', data, 2096)[0]
vram_size = struct.unpack_from('<I', data, 24)[0]
dtb_size  = struct.unpack_from('<I', data, 2100)[0]
print('[*] header_version = %d, page_size = %d, header_size = %d' % (hdr_ver, page_size, hdr_size))
print('[*] vendor_ramdisk_size(字段) = %d (含 padding, 仅供参考)' % vram_size)
print('[*] dtb_size(字段) = %d (MTK 无效字段, 实际无 dtb 段)' % dtb_size)

# ---- 2. lz4 legacy 解压 (优化版: bytes 切片+乘法, 兼容尾部 0 填充) ----
def lz4_dec_block(src):
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]; i += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = src[i]; i += 1
                lit_len += b
                if b != 255: break
        if i + lit_len > n:
            out += src[i:n]; i = n; break
        out += src[i:i+lit_len]; i += lit_len
        if i + 2 > n: break
        offset = src[i] | (src[i+1] << 8); i += 2
        if offset == 0 or offset > len(out): break
        mlen = (token & 0xF) + 4
        if (token & 0xF) == 15:
            while True:
                b = src[i]; i += 1
                mlen += b
                if b != 255: break
        seg = bytes(out[-offset:])
        full, rem = divmod(mlen, offset)
        out += seg * full
        out += seg[:rem]
    return bytes(out)

def parse_stream(start):
    """解析 lz4 legacy 流 (magic 02214c18 + 小端块头序列), 返回 (输出, 流结束位置, 块数)"""
    assert data[start:start+4] == b'\x02\x21\x4c\x18', '流起点无 lz4 legacy magic @%d' % start
    p = start + 4
    out = bytearray()
    blocks = 0
    while p + 4 <= len(data):
        n = struct.unpack_from('<I', data, p)[0]
        if n == 0:                       # 0 块头 = 流结束
            return bytes(out), p + 4, blocks
        if n > 0x08000000:               # 超大 = 下一个流的 magic (小端 0x184C2102) 或垃圾
            return bytes(out), p, blocks
        out += lz4_dec_block(data[p+4:p+4+n])
        p += 4 + n
        blocks += 1
    return bytes(out), p, blocks

# ---- 3. 定位并解压两个流 ----
# 流1 (platform) 起点 = header 对齐 page_size (本例 4096)
s1_off = ((hdr_size + page_size - 1) // page_size) * page_size
ramdisk, s1_end, b1 = parse_stream(s1_off)
print('[*] 流1 platform: off=%d end=%d blocks=%d -> %d B' % (s1_off, s1_end, b1, len(ramdisk)))

# 流2 (recovery) 起点 = 流1 解析结束处 (MTK 布局: 两流顺序拼接, 无表)
if s1_end < len(data) and data[s1_end:s1_end+4] == b'\x02\x21\x4c\x18':
    recovery, s2_end, b2 = parse_stream(s1_end)
    print('[*] 流2 recovery: off=%d end=%d blocks=%d -> %d B' % (s1_end, s2_end, b2, len(recovery)))
else:
    print('[!] 未在流1 结束后发现第二个 lz4 流, recovery 解包跳过')
    recovery = b''

# ---- 4. 校验 + 输出 ----
os.makedirs(out_dir, exist_ok=True)
ok1 = ramdisk[:6] == b'070701'
ok2 = recovery[:6] == b'070701'

ramdisk_path = os.path.join(out_dir, 'ramdisk.cpio')
rec_path     = os.path.join(out_dir, 'recovery.cpio')
open(ramdisk_path, 'wb').write(ramdisk)
if recovery:
    open(rec_path, 'wb').write(recovery)

print('[*] cpio 校验: ramdisk.cpio=%s recovery.cpio=%s' % ('OK' if ok1 else 'FAIL', 'OK' if ok2 else 'N/A'))
print('[*] 输出:')
print('      %s  (%d B, platform, 保留不动)' % (ramdisk_path, len(ramdisk)))
if recovery:
    print('      %s  (%d B, recovery, TWRP 替换这个)' % (rec_path, len(recovery)))
print('[*] 总耗时 %.1f 秒' % (time.time() - t0))
print()
print('== 说明 ==')
print('1. 原厂 recovery ramdisk 已被解出 -> recovery.cpio')
print('2. 做 TWRP: 把你的 recovery.cpio (TWRP ramdisk) 替换 recovery.cpio,')
print('   platform ramdisk.cpio 保持原厂不动 (系统启动依赖它)')
print('3. 打包回 vendor_boot 时注意: MTK 要求 lz4 legacy 小端块头!')
print('   标准 lz4 工具 (大端 legacy) 打包可能不被 bootloader 接受, 建议用')
print('   python-lz4 (pip install lz4) 或保留原流结构只替换压缩内容')
PYEOF
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "[!] python 解包失败 (rc=$rc)"
        exit $rc
    fi
    exit 0
fi

# --- 降级: magiskboot 路径 (尽力而为, 偏移按本机实测布局硬编码) -------------
echo "[*] magiskboot 路径: 提取两流后调用 magiskboot decompress"
mkdir -p "$OUT"
dd if="$IMG" of="$OUT/stream1.bin" bs=1 skip=4096 count=40341510 2>/dev/null
dd if="$IMG" of="$OUT/stream2.bin" bs=1 skip=40345606 count=7986951 2>/dev/null
magiskboot decompress "$OUT/stream1.bin" "$OUT/ramdisk.cpio" 2>/dev/null
magiskboot decompress "$OUT/stream2.bin" "$OUT/recovery.cpio" 2>/dev/null
if head -c 6 "$OUT/ramdisk.cpio" 2>/dev/null | grep -q '070701'; then
    echo "[*] ramdisk.cpio OK"
else
    echo "[!] ramdisk.cpio 校验失败 (magiskboot 不支持 MTK 小端块头, 请改用 python3)"
fi
if head -c 6 "$OUT/recovery.cpio" 2>/dev/null | grep -q '070701'; then
    echo "[*] recovery.cpio OK"
else
    echo "[!] recovery.cpio 校验失败 (请改用 python3)"
fi
exit 0

#!/system/bin/sh
# ============================================================================
# unpack_vendor_boot.sh  (MTK vendor_boot 解包脚本 v2 - 支持 dtb 段)
# ----------------------------------------------------------------------------
# 适用: OnePlus Ace5 Ultra (emira) / MT6991 (Dimensity 9400) / Android 15+
#       及其他 MTK 平台 vendor_boot v4 (VNDRBOOT, header_size=2128)
#
# MTK vendor_boot v4 布局 (与 AOSP 标准完全不同, 实测两种变体):
#   [旧版变体 - 无 dtb]
#     header(2128B) + 流1 platform ramdisk + 流2 recovery ramdisk + 0 padding
#   [新版变体 - 带 dtb]
#     header(2128B) + 流1 platform + 流2 recovery(4块) + dtb段 + 0 padding
#     dtb段 = 64B DT_TABLE 容器头(magic d7b7ab1e) + 标准 FDT(d00dfeed)
#            位置 = align(4096 + vendor_ramdisk_size, 4096)
#
# 共同点:
#   * 无 AOSP vendor_ramdisk table (旧脚本把 lz4 magic 当表头 -> 407642370)
#   * 两个 lz4 legacy 流顺序拼接, 块头为小端 u32 (标准 legacy 是大端!)
#   * dtb_size 字段 = 容器头(64B) + FDT 大小, 是有效字段(新版)
#
# 用法:
#   sh unpack_vendor_boot.sh [输入.img] [输出目录]
#   默认: 输入 /sdcard/Download/vendor_boot.img, 输出 /sdcard/Download/vb_out
#
# 输出:
#   ramdisk.cpio    = 流1 platform ramdisk
#   recovery.cpio   = 流2 recovery ramdisk (TWRP 替换它)
#   dtb             = (若存在) 标准 FDT 设备树
#
# 依赖: python3 (Termux: pkg install python3)
# ============================================================================

IMG="${1:-/sdcard/Download/vendor_boot.img}"
OUT="${2:-/sdcard/Download/vb_out}"

echo "== MTK vendor_boot 解包 v2 (支持 dtb 段) =="
echo "[*] 镜像: $IMG"
echo "[*] 输出: $OUT"

PY=""
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
fi
if [ -z "$PY" ]; then
    echo "[!] 未找到 python3, 请安装: Termux -> pkg install python3"
    exit 1
fi

"$PY" - "$IMG" "$OUT" <<'PYEOF'
import struct, sys, os, time

img_path, out_dir = sys.argv[1], sys.argv[2]
data = open(img_path, 'rb').read()
t0 = time.time()

# ---- 1. header ----
assert data[:8] == b'VNDRBOOT', '不是 vendor_boot (magic != VNDRBOOT)'
hdr_ver   = struct.unpack_from('<I', data, 8)[0]
page_size = struct.unpack_from('<I', data, 12)[0]
hdr_size  = struct.unpack_from('<I', data, 2096)[0]
vram_size = struct.unpack_from('<I', data, 24)[0]
dtb_size  = struct.unpack_from('<I', data, 2100)[0]
print('[*] header_version=%d page_size=%d header_size=%d' % (hdr_ver, page_size, hdr_size))
print('[*] vendor_ramdisk_size=%d dtb_size字段=%d' % (vram_size, dtb_size))

# ---- 2. lz4 legacy 解压 (小端块头, 优化版, 兼容尾部0填充) ----
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

def parse_stream(start, maxend):
    assert data[start:start+4] == b'\x02\x21\x4c\x18', '无 lz4 magic @%d' % start
    p = start + 4
    out = bytearray()
    blocks = 0
    while p + 4 <= maxend:
        n = struct.unpack_from('<I', data, p)[0]
        if n == 0: return bytes(out), p + 4, blocks
        if n > 0x08000000: return bytes(out), p, blocks
        out += lz4_dec_block(data[p+4:p+4+n])
        p += 4 + n
        blocks += 1
    return bytes(out), p, blocks

# ---- 3. 定位并解压两个流 ----
s1_off = ((hdr_size + page_size - 1) // page_size) * page_size
ramdisk, s1_end, b1 = parse_stream(s1_off, len(data))
print('[*] 流1 platform: off=%d end=%d blocks=%d -> %d B' % (s1_off, s1_end, b1, len(ramdisk)))

if s1_end < len(data) and data[s1_end:s1_end+4] == b'\x02\x21\x4c\x18':
    recovery, s2_end, b2 = parse_stream(s1_end, len(data))
    print('[*] 流2 recovery: off=%d end=%d blocks=%d -> %d B' % (s1_end, s2_end, b2, len(recovery)))
else:
    print('[!] 未找到流2')
    recovery = b''

# ---- 4. dtb 段检测 (新版布局: DT_TABLE 容器或直接 FDT) ----
fdt = b''
dtb_off = ((s1_off + vram_size + page_size - 1) // page_size) * page_size
if dtb_off + 4 <= len(data):
    m = data[dtb_off:dtb_off+4]
    if m == b'\xd7\xb7\xab\x1e':   # DT_TABLE 容器
        total = struct.unpack_from('>I', data, dtb_off+4)[0]
        count = struct.unpack_from('>I', data, dtb_off+16)[0]
        eoff  = struct.unpack_from('>I', data, dtb_off+20)[0]
        if count >= 1:
            ds, doff = struct.unpack_from('>II', data, dtb_off+eoff)[0:2]
            fdt = data[dtb_off+doff:dtb_off+doff+ds]
        print('[*] dtb 段 @%d: DT_TABLE(total=%d, entries=%d) -> %d B' % (dtb_off, total, count, len(fdt)))
    elif m == b'\xd0\x0d\xfe\xed':  # 直接 FDT
        ts = struct.unpack_from('>I', data, dtb_off+4)[0]
        fdt = data[dtb_off:dtb_off+ts]
        print('[*] dtb 段 @%d: 直接 FDT %d B' % (dtb_off, len(fdt)))
    else:
        print('[*] dtb 段 @%d: 无 (旧版布局)' % dtb_off)
else:
    print('[*] dtb 段: 无')

# ---- 5. 输出 ----
os.makedirs(out_dir, exist_ok=True)
open(os.path.join(out_dir, 'ramdisk.cpio'), 'wb').write(ramdisk)
if recovery:
    open(os.path.join(out_dir, 'recovery.cpio'), 'wb').write(recovery)
if fdt:
    open(os.path.join(out_dir, 'dtb'), 'wb').write(fdt)

print('[*] cpio 校验: ramdisk=%s recovery=%s' % ('OK' if ramdisk[:6]==b'070701' else 'FAIL', 'OK' if recovery[:6]==b'070701' else 'N/A'))
print('[*] 输出:')
print('      %s/ramdisk.cpio   (%d B, platform, 保留)' % (out_dir, len(ramdisk)))
if recovery:
    print('      %s/recovery.cpio  (%d B, recovery, TWRP 替换它)' % (out_dir, len(recovery)))
if fdt:
    print('      %s/dtb           (%d B, 设备树)' % (out_dir, len(fdt)))
print('[*] 耗时 %.1fs' % (time.time() - t0))
print('== 说明 ==')
print('1. platform ramdisk 保持原厂不动 (系统启动依赖)')
print('2. recovery.cpio 是 TWRP 替换目标')
print('3. 重新打包时: MTK 要求 lz4 legacy 小端块头, dtb 段原样保留')
PYEOF
rc=$?
if [ $rc -ne 0 ]; then
    echo "[!] 解包失败 (rc=$rc)"
    exit $rc
fi
exit 0

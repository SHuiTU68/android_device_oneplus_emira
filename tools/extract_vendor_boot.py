#!/usr/bin/env python3
"""
extract_vendor_boot.py — 从设备提取【完整原厂 vendor_boot】并校验结构

用途: repack_vendor_boot_v2.py 要求输入完整原厂 vendor_boot.img
      (同时含 platform+recovery 两段 ramdisk)。本脚本从设备分区提取并验证。

用法:
  python3 extract_vendor_boot.py [输出路径] [槽位 a|b]
  默认: ./vendor_boot_stock.img, 槽位 a (当前活动槽)
  (在设备上需 root; 或用 adb push 后执行)

校验项:
  - magic = "VNDRBOOT" (vendor_boot v4)
  - 流1(platform) lz4 legacy, 解压为 cpio (070701)
  - 流2(recovery) lz4 legacy, 解压为 cpio (070701)
  - dtb 段 magic d7b7ab1e (DT表容器) / d00dfeed (裸FDT)
"""
import struct, sys, os, subprocess, lz4.block

LZ4 = b'\x02\x21\x4c\x18'
PAGE = 4096
DT_MAGICS = (b'\xd7\xb7\xab\x1e', b'\xd0\x0d\xfe\xed')

def stream_end(d, off):
    """链式扫描 lz4 legacy 流结束 (兼容无 0 end marker、直接拼接下一 magic)"""
    p = off + 4
    while p + 4 <= len(d):
        if d[p:p+4] == LZ4:
            return p
        n = struct.unpack_from('<I', d, p)[0]
        p += 4
        if n == 0:
            return p
        if p + n > len(d):
            return p
        p += n
    return p

def decomp(d, off, end):
    p = off + 4
    out = b''
    while p + 4 <= end:
        n = struct.unpack_from('<I', d, p)[0]
        p += 4
        if n == 0:
            break
        try:
            out += lz4.block.decompress(d[p:p+n], uncompressed_size=256*1024*1024)
        except Exception:
            return b''
        p += n
    return out

def find_dtb(d, after):
    i = after
    while i + 4 <= len(d):
        if d[i:i+4] in DT_MAGICS:
            return i
        i += 1
    return -1

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'vendor_boot_stock.img'
    slot = sys.argv[2] if len(sys.argv) > 2 else 'a'
    dev = f'/dev/block/by-name/vendor_boot_{slot}'

    if not os.path.exists(dev):
        print(f'错误: {dev} 不存在 (需要 root, 或先 adb root)')
        sys.exit(1)

    size = os.path.getsize(dev) if hasattr(os.path, 'getsize') else 0
    if size == 0:
        # block device: 用 blockdev 查询
        try:
            size = int(subprocess.check_output(['blockdev', '--getsize64', dev]).strip())
        except Exception:
            print('错误: 无法获取分区大小')
            sys.exit(1)
    print(f'分区: {dev} = {size} B ({size/1024/1024:.1f} MB)')

    d = open(dev, 'rb').read()
    open(out, 'wb').write(d)
    print(f'已提取: {out}')

    # ---- 校验 ----
    ok = True
    if d[:8] != b'VNDRBOOT':
        print('❌ magic 不是 VNDRBOOT')
        sys.exit(1)
    print('✅ magic = VNDRBOOT')
    vrs = struct.unpack_from('<I', d, 24)[0]
    print(f'   vendor_ramdisk_size = {vrs}')

    s1e = stream_end(d, PAGE)
    s2e = stream_end(d, s1e)
    c1, c2 = decomp(d, PAGE, s1e), decomp(d, s1e, s2e)
    print(f'   流1(platform): {PAGE}..{s1e} ({s1e-PAGE} B) -> 解压 {len(c1)} B, '
          f'cpio={"✅" if c1[:6]==b"070701" else "❌"}')
    print(f'   流2(recovery): {s1e}..{s2e} ({s2e-s1e} B) -> 解压 {len(c2)} B, '
          f'cpio={"✅" if c2[:6]==b"070701" else "❌"}')
    if c1[:6] != b'070701' or c2[:6] != b'070701':
        ok = False

    doff = find_dtb(d, s2e)
    if doff < 0:
        print('❌ 流2 后未找到 dtb magic')
        sys.exit(1)
    tot = struct.unpack_from('>I', d, doff+4)[0]
    print(f'   dtb @ {doff} (magic {d[doff:doff+4].hex()}, total {tot} B) {"✅" if 0<tot<=16*1024*1024 else "❌"}')
    if not (0 < tot <= 16*1024*1024):
        ok = False

    print()
    print('结论:', '✅ 完整原厂 vendor_boot, 可直接作为 repack_vendor_boot_v2.py 输入' if ok
          else '❌ 结构异常, 请检查来源 (勿用于 repack)')
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
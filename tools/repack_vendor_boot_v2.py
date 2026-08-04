#!/usr/bin/env python3
"""
repack_vendor_boot_v2.py — 修复版 vendor_boot 重打包脚本 (MTK 兼容)
路线: TWRP ramdisk -> 流1(platform位置), 空cpio -> 流2(recovery位置), dtb保留
基于【原厂 vendor_boot】修改, 模仿原厂布局语义, 同步更新 header 字段。

MTK 布局语义 (实测当前系统 1e9a0743):
  header(2128B, 页4096) + 流1(lz4, 末尾无0end, 直接拼接流2 magic)
  + 流2(lz4, 末尾0end) + dtb段(64B容器+FDT, 位置=align_up(4096+vendor_ramdisk_size))
  vendor_ramdisk_size = 流2数据结束(不含0end) - 4096

用法:
  python3 repack_vendor_boot_v2.py <原厂vendor_boot.img> <twrp.cpio> <输出.img> [target_size]
"""
import struct, sys, lz4.block

LZ4_LEGACY_MAGIC = b'\x02\x21\x4c\x18'
PAGE = 4096

def align_up(v, a=PAGE):
    return (v + a - 1) // a * a

def create_empty_cpio():
    header = b'070701'
    header += b'00000000' * 2   # ino, mode
    header += b'00000000' * 2   # uid, gid
    header += b'00000001'       # nlink
    header += b'00000000' * 6   # mtime, filesize, devmajor, devminor, rdevmajor, rdevminor
    name = b'TRAILER!!!\x00'
    header += f'{len(name):08X}'.encode()
    header += b'00000000'
    header += name
    header += b'\x00' * ((4 - len(header) % 4) % 4)
    return header

def lz4_legacy_compress(data, with_end=True):
    """lz4 legacy 压缩: 小端块头, 末尾 0 end marker (with_end=True)"""
    result = bytearray(LZ4_LEGACY_MAGIC)
    bs = 8 * 1024 * 1024
    off = 0
    while off < len(data):
        chunk = data[off:off+bs]
        c = lz4.block.compress(chunk, mode='high_compression', compression=12, store_size=False)
        result += struct.pack('<I', len(c))
        result += c
        off += bs
    if with_end:
        result += struct.pack('<I', 0)
    return bytes(result)

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    src, twrp_cpio, out = sys.argv[1], sys.argv[2], sys.argv[3]
    target = int(sys.argv[4]) if len(sys.argv) > 4 else 100663296

    d = open(src, 'rb').read()
    print(f'原厂 vendor_boot: {len(d)} B')

    # --- 解析原厂 ---
    vrs_old = struct.unpack_from('<I', d, 24)[0]
    dtb_size = struct.unpack_from('<I', d, 2100)[0]
    dtb_off = align_up(PAGE + vrs_old)
    dtb_data = d[dtb_off:dtb_off+dtb_size]
    print(f'原厂: vendor_ramdisk_size={vrs_old} dtb@{dtb_off} size={dtb_size}')
    if dtb_data[:4] == b'\xd7\xb7\xab\x1e':
        print(f'  dtb = 64B容器 + FDT {dtb_size-64} B (完整保留)')
    elif dtb_data[:4] == b'\xd0\x0d\xfe\xed':
        print(f'  dtb = 裸 FDT {dtb_size} B')
    else:
        print(f'  警告: dtb 头={dtb_data[:8].hex()}')

    # --- TWRP / 空 cpio 压缩 ---
    twrp = open(twrp_cpio, 'rb').read()
    twrp_lz4 = lz4_legacy_compress(twrp, with_end=True)   # 流1: 带0end(遇end停, 兼容两种解析)
    empty_lz4 = lz4_legacy_compress(create_empty_cpio(), with_end=True)  # 流2: 带0end(与原厂流2一致)
    print(f'TWRP cpio: {len(twrp)} B -> lz4 {len(twrp_lz4)} B ({len(twrp_lz4)/1024/1024:.1f}MB)')
    print(f'空cpio lz4: {len(empty_lz4)} B')

    # --- 新布局 (模仿原厂语义) ---
    new_s1_off = PAGE
    new_s1_end = new_s1_off + len(twrp_lz4)
    new_s2_off = new_s1_end                       # 流2紧跟流1 (原厂: 紧跟, 不对齐)
    new_s2_data_end = new_s2_off + len(empty_lz4) - 4   # 不含0end
    new_vrs = new_s2_data_end - PAGE
    new_dtb_off = align_up(PAGE + new_vrs)        # = align_up(new_s2_data_end)
    print(f'新布局: 流1@{new_s1_off}({len(twrp_lz4)}B) 流2@{new_s2_off}({len(empty_lz4)}B)')
    print(f'新 vendor_ramdisk_size = {new_vrs}  dtb@{new_dtb_off}')

    total_needed = new_dtb_off + dtb_size
    if total_needed > target:
        print(f'错误: 所需 {total_needed} > target {target}')
        sys.exit(1)

    result = bytearray(target)
    result[:PAGE] = d[:PAGE]                      # header 原样
    struct.pack_into('<I', result, 24, new_vrs)   # 更新 vendor_ramdisk_size
    result[new_s1_off:new_s1_off+len(twrp_lz4)] = twrp_lz4
    result[new_s2_off:new_s2_off+len(empty_lz4)] = empty_lz4
    result[new_dtb_off:new_dtb_off+dtb_size] = dtb_data
    # dtb_size / dtb_addr 保持原值不变

    open(out, 'wb').write(result)
    print(f'已写出: {out} ({len(result)} B)')
    print(f'验证: 4096+new_vrs={PAGE+new_vrs} align={new_dtb_off} (dtb实际@{new_dtb_off})')

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
"""
repack_vendor_boot_v2.py — MTK Dimensity 9400+ (mt6991) vendor_boot 重打包 (修复 bootloop)

MTK 布局 (实测当前系统 1e9a0743):
  header(2128B, 页对齐到 4096) + 流1(lz4, platform ramdisk) + 流2(lz4, recovery ramdisk) + dtb段
  流1 = 正常开机用的 vendor ramdisk (巨大, 含全部 ko 模块)
  流2 = recovery ramdisk (进 recovery 时 bootloader 加载的就是这一段)

bootloop 根因 (已修复):
  旧逻辑把 TWRP 放进 流1(platform)、把 流2(recovery) 留成空 cpio。
  进 recovery 时 bootloader 加载的是 流2(空) -> 无 init -> 重启循环。
  而且旧 CI 喂的是只有 header+dtb 的模板, 根本没有 platform 段可保留。

正确做法:
  - 输入必须是【完整原厂 vendor_boot.img】(含 platform+recovery 两段 ramdisk)
  - 流1(platform) 原样保留 (正常开机不受影响, 且 bootloader 看到合法布局)
  - TWRP recovery.cpio 压缩后放入 流2(recovery) 位置
  - dtb 段原样保留, 仅重算 vendor_ramdisk_size

用法:
  python3 repack_vendor_boot_v2.py <完整原厂vendor_boot.img> <twrp_recovery.cpio> <输出.img> [target_size]
"""
import struct, sys, lz4.block

LZ4_LEGACY_MAGIC = b'\x02\x21\x4c\x18'
PAGE = 4096

def align_up(v, a=PAGE):
    return (v + a - 1) // a * a

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

def find_stream_end(data, off):
    """从 off(LZ4 magic) 开始逐块扫描, 返回 (stream_bytes_including_end_marker, next_off)。

    兼容三种结束方式:
      1) 标准 0 end marker
      2) 无 end marker, 最后一块后直接拼接下一个 LZ4 magic (MTK 流1=platform 实测如此)
      3) 块链越界 (防御)
    """
    assert data[off:off+4] == LZ4_LEGACY_MAGIC, f'流起始不是 LZ4 magic @ {off:#x}'
    p = off + 4
    while p + 4 <= len(data):
        if data[p:p+4] == LZ4_LEGACY_MAGIC:
            # 直接接另一个流 magic -> 当前流无 end marker, 在此截断
            return data[off:p], p
        n = struct.unpack_from('<I', data, p)[0]
        p += 4
        if n == 0:
            break
        if p + n > len(data):
            break  # 越界防御
        p += n
    return data[off:p], p

def detect_dtb_size(d, dtb_off, header):
    """鲁棒地确定 dtb 段长度。优先解析 dtb 自身头部 (权威), 失败再回退到 vendor_boot header 常见偏移。

    - DT 表容器 magic d7b7ab1e / 单 FDT magic d00dfeed: 头部 offset 4 均为大端 total_size
    - MTK 实测自定义 header: dtb_size 在 offset 2100
    - 标准 vendor_boot v4 header: dtb_size 在 offset 4144
    """
    magic = d[dtb_off:dtb_off+4]
    if magic in (b'\xd7\xb7\xab\x1e', b'\xd0\x0d\xfe\xed'):
        tot = struct.unpack_from('>I', d, dtb_off+4)[0]
        if 0 < tot <= 16 * 1024 * 1024:
            return tot
    for off in (2100, 4144, 2096):
        try:
            sz = struct.unpack_from('<I', header, off)[0]
        except Exception:
            continue
        if 0 < sz <= 16 * 1024 * 1024:
            return sz
    # 最后兜底: 复制到文件末尾 (vendor_boot 末尾通常是零填充, 无害)
    return len(d) - dtb_off

def find_dtb_start(d, after_off):
    """在 after_off 之后逐字节搜索第一个 DTB 段起点 (DT 表 magic 或 FDT magic)。

    兼容「流2 之后直接跟 dtb」与「流2 之后有页对齐零填充再跟 dtb」两种情况。
    """
    i = after_off
    n = len(d)
    while i + 4 <= n:
        m = d[i:i+4]
        if m in (b'\xd7\xb7\xab\x1e', b'\xd0\x0d\xfe\xed'):
            return i
        i += 1
    raise ValueError(f'在 {after_off:#x} 之后未找到 DTB magic (d7b7ab1e / d00dfeed)')

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    src, twrp_cpio, out = sys.argv[1], sys.argv[2], sys.argv[3]
    target = int(sys.argv[4]) if len(sys.argv) > 4 else 100663296

    d = open(src, 'rb').read()
    print(f'输入原厂 vendor_boot: {len(d)} B')

    if len(d) < 1024 * 1024:
        print('错误: 请用【完整原厂 vendor_boot.img】(含 platform+recovery 两段 ramdisk), '
              '不要用只有 header+dtb 的模板。模板模式会导致 recovery 段为空 -> bootloop。')
        sys.exit(1)

    header = d[:PAGE]                       # 2128B header + 填充到 4096
    vrs_old = struct.unpack_from('<I', header, 24)[0]

    # 解析两段 ramdisk (均从 4096 页开始, 紧跟无对齐)
    s1_bytes, s2_off = find_stream_end(d, PAGE)          # 流1 = platform (原厂)
    s2_bytes, _ = find_stream_end(d, s2_off)             # 流2 = recovery (原厂)
    # dtb 段起点: 在流2 之后搜索 DT 表 / FDT magic (兼容有/无对齐填充)
    dtb_off = find_dtb_start(d, s2_off)
    # 鲁棒确定 dtb 长度 (解析 dtb 自身头部, 失败回退 header 偏移)
    dtb_size = detect_dtb_size(d, dtb_off, header)
    dtb_data = d[dtb_off:dtb_off + dtb_size]
    print(f'原厂: 流1(platform)={len(s1_bytes)}B  流2(recovery)={len(s2_bytes)}B  '
          f'dtb@{dtb_off}({dtb_size}B)  vrs={vrs_old}')

    # TWRP 作为 recovery 段 (流2), platform 段(流1) 原样保留
    twrp = open(twrp_cpio, 'rb').read()
    twrp_lz4 = lz4_legacy_compress(twrp, with_end=True)
    print(f'TWRP recovery.cpio: {len(twrp)} B -> lz4 {len(twrp_lz4)} B')

    new_s1 = s1_bytes          # 原厂 platform, 含自身 end marker
    new_s2 = twrp_lz4          # TWRP 作为 recovery
    # MTK 定义: vendor_ramdisk_size = 流2 数据结束(不含 end marker) - 4096
    new_vrs = len(new_s1) + len(new_s2) - 4
    new_dtb_off = align_up(PAGE + new_vrs)
    print(f'新: 流1(platform)={len(new_s1)}B  流2(recovery=TWRP)={len(new_s2)}B  '
          f'vrs={new_vrs}  dtb@{new_dtb_off}')

    total = new_dtb_off + dtb_size
    if total > target:
        print(f'错误: 所需 {total} > target {target}')
        sys.exit(1)

    result = bytearray(target)
    result[:PAGE] = header
    struct.pack_into('<I', result, 24, new_vrs)   # 更新 vendor_ramdisk_size
    result[PAGE:PAGE+len(new_s1)] = new_s1
    result[PAGE+len(new_s1):PAGE+len(new_s1)+len(new_s2)] = new_s2
    result[new_dtb_off:new_dtb_off+dtb_size] = dtb_data

    open(out, 'wb').write(result)
    print(f'已写出: {out} ({len(result)} B)')
    print(f'验证: 4096+vrs={PAGE+new_vrs}  align(dtboff)={new_dtb_off} (dtb实际@{new_dtb_off})')

if __name__ == '__main__':
    main()

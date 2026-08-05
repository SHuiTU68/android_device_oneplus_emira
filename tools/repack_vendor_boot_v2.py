#!/usr/bin/env python3
"""
repack_vendor_boot_v2.py — MTK Dimensity 9400+ (mt6991) vendor_boot 重打包 (修复 bootloop)

MTK 布局 (实测原厂 1e9a0743):
  header(2128B, 页对齐到 4096) + 流1(lz4 legacy, platform ramdisk) + 流2(lz4 legacy, recovery ramdisk) + dtb段
  流1 = platform ramdisk (无 end marker, 直接接流2 magic)
  流2 = recovery ramdisk (有 end marker)
  vendor_ramdisk_size(VRS) = 流1字节数 + 流2字节数 - 4  (即两段数据不含流2的 end marker)
  dtb 偏移 = page_align_up(4096 + VRS)
  vendor_ramdisk_table 全为 0 (MTK bootloader 不依赖它, 顺序解析两段 LZ4 流)

bootloop 根因 (本次修复):
  旧方案把 TWRP 放进 流2(recovery), 原厂 流1(platform) 原样保留。
  进 recovery 时 bootloader 加载 流1 + 流2。流1 是原厂 platform ramdisk, 含
  first_stage_ramdisk/fstab.mt6991 (挂载 system/vendor/odm, 带 avb+logical+
  first_stage_mount)。该 fstab 在 recovery 环境下挂载失败 -> first_stage init
  panic -> 重启循环 = bootloop。

  实测原厂 流2(recovery) 并没有 fstab.mt6991 (只有 recovery 专用的 fstab.emmc),
  证明 recovery 不应挂载 system/vendor/odm 这些 avb+logical 分区。

正确做法 (用户实测验证可行):
  - 流1 = TWRP/OrangeFox recovery.cpio (替换原厂 platform)
    进 recovery 时 bootloader 加载 流1, 用 TWRP 自己的 recovery init/fstab,
    不执行原厂的 first_stage_mount -> 能进 TWRP。
    正常开机不加载 vendor_boot ramdisk (用 init_boot ramdisk) -> 能进系统。
  - 流2 = 合法空 cpio (只含 TRAILER!!!), 占位用, 不影响。
  - 布局完全模仿原厂: 流1 无 end marker, 流2 有 end marker, VRS 公式不变。
  - dtb 段从原厂原样保留, 仅重算 VRS。

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

def make_empty_cpio():
    """构造合法的空 cpio (newc 格式, 只含 TRAILER!!! entry)。

    bootloader 解压流2 后得到这个空 cpio, 解析到 TRAILER!!! 即结束, 不产生任何文件,
    不会干扰 流1(TWRP) 的 rootfs。比"0 字节"或"随机字节"安全: 0 字节会被 cpio
    解析器视为损坏 -> 某些 bootloader 报错; 合法 TRAILER 则被静默接受。
    """
    hdr = b'070701'          # magic (newc)
    hdr += b'00000000'        # ino
    hdr += b'00000000'        # mode
    hdr += b'00000000'        # uid
    hdr += b'00000000'        # gid
    hdr += b'00000001'        # nlink=1
    hdr += b'00000000'        # mtime
    hdr += b'00000000'        # filesize=0
    hdr += b'00000000'        # devmajor
    hdr += b'00000000'        # devminor
    hdr += b'00000000'        # rdevmajor
    hdr += b'00000000'        # rdevminor
    hdr += b'0000000b'        # namesize=11 ("TRAILER!!!" + \0)
    hdr += b'00000000'        # check
    # 110 字节头 + 11 字节 name, 对齐到 4 字节 -> 补 3 字节
    name = b'TRAILER!!!\x00\x00\x00\x00'
    return hdr + name

def find_stream_end(data, off):
    """从 off(LZ4 magic) 开始逐块扫描, 返回 (stream_bytes, next_off)。

    兼容三种结束方式:
      1) 标准 0 end marker
      2) 无 end marker, 最后一块后直接拼接下一个 LZ4 magic (MTK 流1=platform 实测如此)
      3) 块链越界 (防御)
    返回的 stream_bytes: 若遇 0 marker 则含 marker, 若遇下一个 magic 则不含 marker。
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
    """鲁棒地确定 dtb 段长度。优先解析 dtb 自身头部 (权威), 失败再回退 header 偏移。

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
              '不要用只有 header+dtb 的模板。模板模式无法获取原厂 dtb 段。')
        sys.exit(1)

    header = d[:PAGE]                       # 2128B header + 填充到 4096
    vrs_old = struct.unpack_from('<I', header, 24)[0]

    # 解析原厂两段 ramdisk (仅为了定位 dtb 段; 流1/流2 内容将被替换)
    s1_bytes, s2_off = find_stream_end(d, PAGE)          # 流1 = platform (原厂)
    s2_bytes, _ = find_stream_end(d, s2_off)             # 流2 = recovery (原厂)
    dtb_off = find_dtb_start(d, s2_off)
    dtb_size = detect_dtb_size(d, dtb_off, header)
    dtb_data = d[dtb_off:dtb_off + dtb_size]
    print(f'原厂: 流1(platform)={len(s1_bytes)}B  流2(recovery)={len(s2_bytes)}B  '
          f'dtb@{dtb_off}({dtb_size}B)  vrs={vrs_old}')

    # 方案B (用户实测验证): TWRP 放 流1, 空 cpio 放 流2
    # 流1 = TWRP recovery.cpio (无 end marker, 模仿原厂流1, 后面直接接流2 magic)
    # 流2 = 合法空 cpio (有 end marker, 标准结束)
    twrp = open(twrp_cpio, 'rb').read()
    new_s1 = lz4_legacy_compress(twrp, with_end=False)   # 流1 无 end marker (模仿原厂)
    new_s2 = lz4_legacy_compress(make_empty_cpio(), with_end=True)  # 流2 空 cpio + end marker
    print(f'TWRP recovery.cpio: {len(twrp)} B -> 流1 lz4 {len(new_s1)} B (无 end marker)')
    print(f'空 cpio -> 流2 lz4 {len(new_s2)} B (有 end marker)')

    # VRS 公式与原厂一致: 流1(无marker) + 流2(含marker) - 4
    new_vrs = len(new_s1) + len(new_s2) - 4
    new_dtb_off = align_up(PAGE + new_vrs)
    ramdisk_end = PAGE + len(new_s1) + len(new_s2)
    print(f'新: 流1(TWRP)={len(new_s1)}B  流2(空cpio)={len(new_s2)}B  '
          f'vrs={new_vrs}  ramdisk_end={ramdisk_end}  dtb@{new_dtb_off}')

    # 安全检查: dtb 必须在 ramdisk 之后, 不能重叠
    if new_dtb_off < ramdisk_end:
        print(f'警告: dtb@{new_dtb_off} < ramdisk_end={ramdisk_end} (流2末尾刚好跨页边界)')
        print(f'      强制 dtb 到下一页对齐位置 {align_up(ramdisk_end)}')
        new_dtb_off = align_up(ramdisk_end)
        # 同步调整 VRS 使 bootloader 计算的 dtb 偏移与实际一致:
        # bootloader 用 page_align_up(4096+VRS) 定位 dtb, 需等于 new_dtb_off
        # page_align_up(4096+VRS) = new_dtb_off  =>  4096+VRS > new_dtb_off - 4096
        # 取 VRS = new_dtb_off - 4096 (此时 4096+VRS 已页对齐, align_up 不变)
        new_vrs = new_dtb_off - PAGE
        print(f'      调整后 vrs={new_vrs} (使 page_align_up(4096+vrs)={align_up(PAGE+new_vrs)} == dtb@{new_dtb_off})')

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
    print(f'验证: 4096+vrs={PAGE+new_vrs}  align={align_up(PAGE+new_vrs)}  dtb实际@{new_dtb_off}  '
          f'ramdisk_end={ramdisk_end}  不重叠={"OK" if new_dtb_off>=ramdisk_end else "FAIL"}')

if __name__ == '__main__':
    main()

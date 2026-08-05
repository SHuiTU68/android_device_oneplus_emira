#!/usr/bin/env python3
"""
merge_ko_into_cpio.py — 把原厂 vendor_boot 流1(platform ramdisk) 里的 .ko + modules.***
合并进 TWRP/OrangeFox recovery.cpio, 解决方案B 流1 模块缺失导致的开机/功能残废。

背景:
  方案B 把 TWRP recovery.cpio 放进流1 (替换原厂 platform)。但 TWRP cpio 里只有
  约 55 个基础 .ko, 缺少显示/触摸/充电/存储/oplus 全系列 (145+ 个), 且没有
  modules.load / modules.dep / modules.alias / modules.load.recovery / modules.softdep。
  正常开机时 first_stage init 从流1 加载 .ko 初始化硬件, 缺失会 bootloop 或残废
  (data 挂载失败、触摸/显示/充电异常等)。

策略:
  - 解包原厂 vendor_boot.img, 定位流1(platform), lz4 legacy 解压得到原厂 cpio
  - 解包 TWRP recovery.cpio
  - 合并规则:
      * 原厂 lib/modules/*.ko  → 全部注入 (同名时原厂覆盖 TWRP, 因原厂匹配 kernel)
      * 原厂 lib/modules/modules.load / .dep / .alias / .load.recovery / .softdep
        → 全部注入 (TWRP 通常没有这些元数据)
      * 其余 TWRP 文件 (init, *.rc, twres, sbin, system 等) 全部保留
  - 重打包成 newc cpio (与 TWRP 同格式), 供 repack_vendor_boot_v2.py 作为流1

用法:
  python3 merge_ko_into_cpio.py <原厂vendor_boot.img> <twrp_recovery.cpio> <输出.cpio>
"""
import struct
import sys
import lz4.block

LZ4_LEGACY_MAGIC = b'\x02\x21\x4c\x18'
PAGE = 4096


def align4(v):
    return (v + 3) & ~3


def lz4_legacy_decompress(data):
    """lz4 legacy 解压: magic(4) + [块头(4, 小端) + 块数据]*, 末尾 0 end marker"""
    if data[:4] != LZ4_LEGACY_MAGIC:
        raise ValueError(f'非 lz4 legacy magic: {data[:4]!r}')
    out = bytearray()
    p = 4
    while p + 4 <= len(data):
        n = struct.unpack_from('<I', data, p)[0]
        p += 4
        if n == 0:
            break
        if p + n > len(data):
            raise ValueError(f'块越界 @ {p}: n={n} 剩余={len(data)-p}')
        out += lz4.block.decompress(data[p:p + n], uncompressed_size=64 * 1024 * 1024)
        p += n
    return bytes(out)


def find_stream_end(data, off):
    """从 off(LZ4 magic) 开始逐块扫描, 返回 (stream_bytes, next_off)。
    兼容无 end marker (直接接下一流 magic) 与标准 0 end marker。"""
    assert data[off:off + 4] == LZ4_LEGACY_MAGIC, f'流起始非 LZ4 magic @ {off:#x}'
    p = off + 4
    while p + 4 <= len(data):
        if data[p:p + 4] == LZ4_LEGACY_MAGIC:
            return data[off:p], p
        n = struct.unpack_from('<I', data, p)[0]
        p += 4
        if n == 0:
            break
        if p + n > len(data):
            break
        p += n
    return data[off:p], p


def cpio_parse(data):
    """解析 newc cpio, 返回 [(name, mode, content), ...]。
    name 已去除前导 '/'。保留 TRAILER!!! 作为结束标记。"""
    entries = []
    p = 0
    n = len(data)
    while p + 110 <= n:
        if data[p:p + 6] != b'070701':
            # 尾部零填充, 跳过
            if set(data[p:]) <= {0}:
                break
            raise ValueError(f'坏 cpio magic @ {p}: {data[p:p+6]!r}')
        hdr = data[p:p + 110]
        namesize = int(hdr[94:102], 16)
        filesize = int(hdr[54:62], 16)
        mode = int(hdr[14:22], 16)
        nlink = int(hdr[46:54], 16)
        name_start = p + 110
        name_end = name_start + namesize
        if name_end > n:
            raise ValueError(f'name 越界 @ {p}')
        name = data[name_start:name_end - 1].decode('utf-8', 'replace')  # 去末尾 \0
        data_start = align4(name_end)
        data_end = data_start + filesize
        if data_end > n:
            raise ValueError(f'data 越界 @ {p} name={name}')
        content = data[data_start:data_end]
        if name == 'TRAILER!!!':
            break
        entries.append((name, mode, content))
        p = align4(data_end)
    return entries


def cpio_build(entries):
    """打包 newc cpio。entries: [(name, mode, content), ...]。
    目录/符号链接等无 content 项传 b''。追加 TRAILER!!! 结束。"""
    out = bytearray()
    ino = 1  # 简单递增 ino (cpio 不强制唯一, kernel 解包容忍)
    for name, mode, content in entries:
        nb = name.encode('utf-8') + b'\x00'
        namesize = len(nb)
        filesize = len(content) if content else 0
        hdr = b'070701'
        hdr += b'%08x' % ino
        hdr += b'%08x' % mode
        hdr += b'%08x' % 0      # uid
        hdr += b'%08x' % 0      # gid
        hdr += b'%08x' % 1      # nlink (目录用 2 也行, 这里统一 1, kernel 不强制)
        hdr += b'%08x' % 0      # mtime
        hdr += b'%08x' % filesize
        hdr += b'%08x' % 0      # devmajor
        hdr += b'%08x' % 0      # devminor
        hdr += b'%08x' % 0      # rdevmajor
        hdr += b'%08x' % 0      # rdevminor
        hdr += b'%08x' % namesize
        hdr += b'%08x' % 0      # check
        out += hdr
        out += nb
        # name 对齐到 4
        pad = align4(len(hdr) + namesize) - (len(hdr) + namesize)
        out += b'\x00' * pad
        if content:
            out += content
            pad = align4(filesize) - filesize
            out += b'\x00' * pad
        ino += 1
    # TRAILER!!!
    name = b'TRAILER!!!\x00'
    hdr = b'070701'
    hdr += b'%08x' % ino
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 1
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % 0
    hdr += b'%08x' % len(name)
    hdr += b'%08x' % 0
    out += hdr + name
    return bytes(out)


def extract_stock_platform_cpio(stock_vb_path):
    """从原厂 vendor_boot 提取流1(platform) 的 cpio (已 lz4 解压)。"""
    d = open(stock_vb_path, 'rb').read()
    if len(d) < 1024 * 1024:
        raise ValueError('原厂 vendor_boot 太小, 非完整镜像')
    s1_bytes, _ = find_stream_end(d, PAGE)
    print(f'  原厂流1(platform): lz4 {len(s1_bytes)} B')
    cpio = lz4_legacy_decompress(s1_bytes)
    print(f'  原厂流1 解压: {len(cpio)} B, magic={cpio[:6]!r}')
    if cpio[:6] != b'070701':
        raise ValueError(f'原厂流1 解压结果非 cpio: {cpio[:6]!r}')
    return cpio


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    stock_vb, twrp_cpio_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

    # 1. 原厂 platform cpio
    print('=== 1. 从原厂 vendor_boot 提取流1(platform) cpio ===')
    stock_cpio = extract_stock_platform_cpio(stock_vb)
    stock_entries = cpio_parse(stock_cpio)
    print(f'  原厂 entries: {len(stock_entries)}')

    # 2. TWRP cpio
    print('=== 2. 解析 TWRP recovery.cpio ===')
    twrp_cpio = open(twrp_cpio_path, 'rb').read()
    if twrp_cpio[:6] != b'070701':
        raise ValueError(f'TWRP cpio magic 错误: {twrp_cpio[:6]!r}')
    twrp_entries = cpio_parse(twrp_cpio)
    print(f'  TWRP entries: {len(twrp_entries)}')

    # 3. 收集原厂需要注入的文件 (lib/modules/ 下全部)
    inject = {}  # name -> (mode, content)
    for name, mode, content in stock_entries:
        if name.startswith('lib/modules/'):
            inject[name] = (mode, content)
    print(f'=== 3. 原厂 lib/modules/ 注入项: {len(inject)} ===')
    ko_count = sum(1 for n in inject if n.endswith('.ko'))
    meta_count = len(inject) - ko_count
    print(f'    .ko: {ko_count}  modules.*: {meta_count}')

    # 4. 合并: TWRP 为基底, 原厂 lib/modules/ 覆盖/补充
    print('=== 4. 合并 (TWRP 基底 + 原厂 lib/modules 覆盖) ===')
    merged = {}
    for name, mode, content in twrp_entries:
        merged[name] = (mode, content)
    overwritten = 0
    added = 0
    for name, (mode, content) in inject.items():
        if name in merged:
            overwritten += 1
        else:
            added += 1
        merged[name] = (mode, content)
    print(f'    覆盖 TWRP 同名: {overwritten}  新增: {added}')
    print(f'    合并后 entries: {len(merged)}')

    # 5. 重建目录结构: lib/ 和 lib/modules/ 目录项需存在 (kernel cpio 解包要求)
    #    从合并项里推断需要的目录
    dirs_needed = set()
    for name in merged:
        parts = name.split('/')
        for i in range(1, len(parts)):
            d = '/'.join(parts[:i])
            if d:
                dirs_needed.add(d)
    # 补缺失的目录项 (mode 040755)
    for d in sorted(dirs_needed, key=len):
        if d not in merged:
            merged[d] = (0o040755, b'')

    # 6. 排序: 目录在前, 文件在后 (按路径长度+字典序, 确保父目录先于子项)
    def sort_key(item):
        name, (mode, _) = item
        is_dir = (mode & 0o170000) == 0o040000
        return (0 if is_dir else 1, name)
    sorted_entries = sorted(merged.items(), key=sort_key)
    final = [(name, mode, content) for name, (mode, content) in sorted_entries]

    # 7. 打包
    out = cpio_build(final)
    print(f'=== 5. 写出合并 cpio: {len(out)} B ===')
    # 自检
    chk = cpio_parse(out)
    ko_chk = sum(1 for n, _, _ in chk if n.endswith('.ko'))
    print(f'  自检: entries={len(chk)}  .ko={ko_chk}  magic={out[:6]!r}')
    if out[:6] != b'070701':
        raise ValueError('输出 cpio magic 错误')

    with open(out_path, 'wb') as f:
        f.write(out)
    print(f'已写出: {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

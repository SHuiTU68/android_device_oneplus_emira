#!/usr/bin/env python3
"""
重新打包 vendor_boot.img：
  1. 解析 VNDRBOOT v4 header
  2. 提取 platform ramdisk 和 recovery ramdisk
  3. 把 recovery ramdisk 作为新的 platform ramdisk
  4. 创建空的 CPIO 作为新的 recovery ramdisk
  5. 重新打包 vendor_boot.img

用法：python3 repack_vendor_boot.py <vendor_boot.img> <output.img>
"""

import struct
import sys
import os
import lz4.block
import io

VENDOR_BOOT_MAGIC = b'VNDRBOOT'
PAGE_SIZE_DEFAULT = 4096

# LZ4 Legacy 格式
LZ4_LEGACY_MAGIC = struct.pack('<I', 0x184C2102)


def create_empty_cpio():
    """创建一个空的 CPIO 归档（只包含 TRAILER!!!）"""
    # CPIO newc 格式的 TRAILER!!! 条目
    header = b'070701'  # magic
    header += b'00000000'  # ino
    header += b'000081A4'  # mode (040755 = directory... 不，用 0)
    header += b'00000000'  # uid
    header += b'00000000'  # gid
    header += b'00000001'  # nlink
    header += b'00000000'  # mtime
    header += b'00000000'  # filesize
    header += b'00000000'  # devmajor
    header += b'00000000'  # devminor
    header += b'00000000'  # rdevmajor
    header += b'00000000'  # rdevminor
    name = b'TRAILER!!!\x00'
    header += f'{len(name):08X}'.encode('ascii')  # namesize
    header += b'00000000'  # check
    header += name
    # 对齐到 4 字节
    total = len(header)
    pad = (4 - (total % 4)) % 4
    header += b'\x00' * pad
    return header


def lz4_legacy_compress(data):
    """用 LZ4 Legacy 格式压缩数据"""
    result = bytearray(LZ4_LEGACY_MAGIC)

    # 分块压缩（每块最大 8MB 解压后）
    block_size = 8 * 1024 * 1024
    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + block_size]
        compressed = lz4.block.compress(
            chunk,
            mode='high_compression',
            compression=12,
            store_size=False
        )
        # block_size 字段（最高位=0 表示压缩）
        result += struct.pack('<I', len(compressed))
        result += compressed
        offset += block_size

    # 结束标记
    result += struct.pack('<I', 0)
    return bytes(result)


def parse_vendor_boot(data):
    """解析 VNDRBOOT v4 vendor_boot.img"""
    magic = data[0:8]
    if magic != VENDOR_BOOT_MAGIC:
        raise ValueError(f"Invalid magic: {magic}")

    header_version = struct.unpack('<I', data[8:12])[0]
    page_size = struct.unpack('<I', data[12:16])[0]
    kernel_addr = struct.unpack('<I', data[16:20])[0]
    ramdisk_addr = struct.unpack('<I', data[20:24])[0]
    v_ramdisk_table_offset = struct.unpack('<I', data[24:28])[0]
    v_ramdisk_table_entry_num = struct.unpack('<I', data[28:32])[0]
    v_ramdisk_table_entry_size = struct.unpack('<I', data[32:36])[0]
    bootconfig_size = struct.unpack('<I', data[36:40])[0]
    dtb_size = struct.unpack('<I', data[40:44])[0]
    dtb_addr = struct.unpack('<Q', data[44:52])[0]

    print(f"VNDRBOOT v{header_version}")
    print(f"  page_size: {page_size}")
    print(f"  table_offset: {v_ramdisk_table_offset}")
    print(f"  table_entries: {v_ramdisk_table_entry_num}")
    print(f"  table_entry_size: {v_ramdisk_table_entry_size}")
    print(f"  dtb_size: {dtb_size}")
    print(f"  bootconfig_size: {bootconfig_size}")

    # 解析 ramdisk table
    ramdisks = []
    for i in range(v_ramdisk_table_entry_num):
        off = v_ramdisk_table_offset + i * v_ramdisk_table_entry_size
        entry = data[off:off + v_ramdisk_table_entry_size]
        v_type = struct.unpack('<I', entry[0:4])[0]
        # entry[4:8] 可能是 ramdisk_name index 或 padding
        v_size = struct.unpack('<Q', entry[8:16])[0]
        v_offset = struct.unpack('<Q', entry[16:24])[0]
        v_name = entry[24:32].rstrip(b'\x00').decode('ascii', errors='replace')

        type_name = {0: 'platform', 1: 'recovery', 2: 'dlkm'}.get(v_type, f'unknown({v_type})')
        print(f"  Ramdisk [{i}]: type={type_name}, size={v_size}, offset={v_offset}, name=\"{v_name}\"")

        # 提取 ramdisk 数据
        ramdisk_data = data[v_offset:v_offset + v_size]
        ramdisks.append({
            'type': v_type,
            'type_name': type_name,
            'size': v_size,
            'offset': v_offset,
            'name': v_name,
            'data': ramdisk_data
        })

    # 提取 DTB
    # DTB 通常在 header 之后（第一页之后）
    dtb_offset = page_size  # DTB 从第二页开始
    dtb_data = data[dtb_offset:dtb_offset + dtb_size]
    print(f"  DTB: offset={dtb_offset}, size={len(dtb_data)}")

    return {
        'header_version': header_version,
        'page_size': page_size,
        'kernel_addr': kernel_addr,
        'ramdisk_addr': ramdisk_addr,
        'v_ramdisk_table_offset': v_ramdisk_table_offset,
        'v_ramdisk_table_entry_num': v_ramdisk_table_entry_num,
        'v_ramdisk_table_entry_size': v_ramdisk_table_entry_size,
        'bootconfig_size': bootconfig_size,
        'dtb_size': dtb_size,
        'dtb_addr': dtb_addr,
        'header_data': data[:page_size],  # 完整 header（第一页）
        'ramdisks': ramdisks,
        'dtb_data': dtb_data,
        'raw_data': data,
    }


def repack_vendor_boot(info, output_path, target_size=None):
    """
    重新打包 vendor_boot.img：
    - 把 recovery ramdisk 作为新的 platform ramdisk
    - 用空 CPIO 作为新的 recovery ramdisk
    """
    page_size = info['page_size']
    header = bytearray(info['header_data'])

    # 创建空 CPIO 并 LZ4 压缩
    empty_cpio = create_empty_cpio()
    empty_cpio_lz4 = lz4_legacy_compress(empty_cpio)
    print(f"\n空 CPIO 大小: {len(empty_cpio)} bytes")
    print(f"空 CPIO LZ4 压缩后: {len(empty_cpio_lz4)} bytes")

    # 找到 recovery ramdisk 和 platform ramdisk
    recovery_ramdisk = None
    for r in info['ramdisks']:
        if r['type'] == 1:  # recovery
            recovery_ramdisk = r
            break

    if not recovery_ramdisk:
        raise ValueError("找不到 recovery ramdisk")

    print(f"\n原始 recovery ramdisk 大小: {recovery_ramdisk['size']} bytes")
    print(f"将作为新的 platform ramdisk")

    # 计算新的 ramdisk 布局
    # 布局: header(1页) + DTB(对齐到页) + ramdisk0(新platform=recovery) + ramdisk1(新recovery=空)
    dtb_pages = (info['dtb_size'] + page_size - 1) // page_size

    # 新 platform ramdisk = 原 recovery ramdisk
    new_platform_data = recovery_ramdisk['data']
    new_platform_size = len(new_platform_data)

    # 新 recovery ramdisk = 空 CPIO LZ4
    new_recovery_data = empty_cpio_lz4
    new_recovery_size = len(new_recovery_data)

    # ramdisk 起始偏移（DTB 之后）
    ramdisk_start = page_size + dtb_pages * page_size

    # 新 platform ramdisk 偏移
    new_platform_offset = ramdisk_start
    # 对齐到页
    platform_pages = (new_platform_size + page_size - 1) // page_size
    new_recovery_offset = new_platform_offset + platform_pages * page_size

    print(f"\n新布局:")
    print(f"  header: 0x0 - 0x{page_size:X}")
    print(f"  DTB: 0x{page_size:X} - 0x{ramdisk_start:X}")
    print(f"  platform (原 recovery): 0x{new_platform_offset:X} - 0x{new_recovery_offset:X} ({new_platform_size} bytes)")
    print(f"  recovery (空 CPIO): 0x{new_recovery_offset:X} ({new_recovery_size} bytes)")

    # 更新 ramdisk table
    table_offset = info['v_ramdisk_table_offset']
    entry_size = info['v_ramdisk_table_entry_size']

    # Entry 0: platform (用 recovery 的数据)
    entry0 = bytearray(entry_size)
    struct.pack_into('<I', entry0, 0, 0)  # type = platform
    struct.pack_into('<I', entry0, 4, 0)  # name index = 0
    struct.pack_into('<Q', entry0, 8, new_platform_size)  # size
    struct.pack_into('<Q', entry0, 16, new_platform_offset)  # offset
    # name (24:32) = 空（platform ramdisk 无名称）
    entry0[24:32] = b'\x00' * 8

    # Entry 1: recovery (空 CPIO)
    entry1 = bytearray(entry_size)
    struct.pack_into('<I', entry1, 0, 1)  # type = recovery
    struct.pack_into('<I', entry1, 4, 0)  # name index
    struct.pack_into('<Q', entry1, 8, new_recovery_size)  # size
    struct.pack_into('<Q', entry1, 16, new_recovery_offset)  # offset
    # name = "recovery"
    name_bytes = b'recovery\x00'
    entry1[24:24 + len(name_bytes)] = name_bytes

    # 写入 header 中的 table
    header[table_offset:table_offset + entry_size] = entry0
    header[table_offset + entry_size:table_offset + 2 * entry_size] = entry1

    # 构建完整的 vendor_boot.img
    result = bytearray()

    # 1. Header (1 页)
    result += header
    # 确保到 page_size
    if len(result) < page_size:
        result += b'\x00' * (page_size - len(result))
    elif len(result) > page_size:
        result = result[:page_size]

    # 2. DTB
    result += info['dtb_data']
    # 对齐到页
    dtb_padded = dtb_pages * page_size
    if len(info['dtb_data']) < dtb_padded:
        result += b'\x00' * (dtb_padded - len(info['dtb_data']))

    # 3. Platform ramdisk (原 recovery)
    result += new_platform_data
    # 对齐到页
    platform_padded = platform_pages * page_size
    if len(new_platform_data) < platform_padded:
        result += b'\x00' * (platform_padded - len(new_platform_data))

    # 4. Recovery ramdisk (空 CPIO)
    result += new_recovery_data
    # 对齐到页
    recovery_pages = (new_recovery_size + page_size - 1) // page_size
    recovery_padded = recovery_pages * page_size
    if len(new_recovery_data) < recovery_padded:
        result += b'\x00' * (recovery_padded - len(new_recovery_data))

    # 如果指定了目标大小，填充到目标大小
    if target_size and len(result) < target_size:
        result += b'\xFF' * (target_size - len(result))

    print(f"\n最终大小: {len(result)} bytes ({len(result) / 1024 / 1024:.1f} MB)")

    with open(output_path, 'wb') as f:
        f.write(result)
    print(f"已保存到: {output_path}")

    return len(result)


def main():
    if len(sys.argv) < 3:
        print(f"用法: {sys.argv[0]} <vendor_boot.img> <output.img> [target_size]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    target_size = int(sys.argv[3]) if len(sys.argv) > 3 else None

    with open(input_path, 'rb') as f:
        data = f.read()

    print(f"输入文件: {input_path} ({len(data)} bytes, {len(data) / 1024 / 1024:.1f} MB)")
    print()

    info = parse_vendor_boot(data)
    repack_vendor_boot(info, output_path, target_size)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
重新打包 vendor_boot.img（不依赖 header 解析）：
  1. 搜索 LZ4 legacy magic 定位 ramdisk 数据
  2. 搜索 ramdisk table（type=0 platform + type=1 recovery）
  3. 把 recovery ramdisk 作为新的 platform ramdisk
  4. 创建空的 CPIO 作为新的 recovery ramdisk
  5. 重新打包 vendor_boot.img

用法：python3 repack_vendor_boot.py <vendor_boot.img> <output.img> [target_size]
"""

import struct
import sys
import os
import lz4.block

PAGE_SIZE = 4096
LZ4_LEGACY_MAGIC = b'\x02\x21\x4c\x18'


def create_empty_cpio():
    """创建一个空的 CPIO 归档（只包含 TRAILER!!!）"""
    header = b'070701'
    header += b'00000000'  # ino
    header += b'000081A4'  # mode
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
    header += f'{len(name):08X}'.encode('ascii')
    header += b'00000000'  # check
    header += name
    total = len(header)
    pad = (4 - (total % 4)) % 4
    header += b'\x00' * pad
    return header


def lz4_legacy_compress(data):
    """用 LZ4 Legacy 格式压缩数据"""
    result = bytearray(LZ4_LEGACY_MAGIC)
    block_size = 8 * 1024 * 1024
    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + block_size]
        compressed = lz4.block.compress(
            chunk, mode='high_compression', compression=12, store_size=False
        )
        result += struct.pack('<I', len(compressed))
        result += compressed
        offset += block_size
    result += struct.pack('<I', 0)  # end marker
    return bytes(result)


def align_up(val, alignment):
    return (val + alignment - 1) // alignment * alignment


def find_ramdisk_table(data):
    """
    搜索 ramdisk table。
    table 包含连续的 32 字节 entries：
      Entry 0: type=0 (platform), name_idx=0, body_size, ramdisk_offset, name
      Entry 1: type=1 (recovery), name_idx=0, body_size, ramdisk_offset, name
    """
    # 搜索范围：从第二页开始到文件末尾
    search_start = PAGE_SIZE
    data_len = len(data)

    for pos in range(search_start, data_len - 64, 4):
        # 检查 entry 0: type=0 (platform)
        e0_type = struct.unpack('<I', data[pos:pos+4])[0]
        if e0_type != 0:
            continue

        e0_name_idx = struct.unpack('<I', data[pos+4:pos+8])[0]
        if e0_name_idx != 0:
            continue

        e0_size = struct.unpack('<Q', data[pos+8:pos+16])[0]
        e0_offset = struct.unpack('<Q', data[pos+16:pos+24])[0]

        # 验证：size 和 offset 应该在合理范围内
        if e0_size < 100 or e0_size > data_len:
            continue
        if e0_offset < PAGE_SIZE or e0_offset >= data_len:
            continue

        # 检查 entry 1: type=1 (recovery)
        e1_off = pos + 32
        e1_type = struct.unpack('<I', data[e1_off:e1_off+4])[0]
        if e1_type != 1:
            continue

        e1_name_idx = struct.unpack('<I', data[e1_off+4:e1_off+8])[0]
        e1_size = struct.unpack('<Q', data[e1_off+8:e1_off+16])[0]
        e1_offset = struct.unpack('<Q', data[e1_off+16:e1_off+24])[0]
        e1_name = data[e1_off+24:e1_off+32].rstrip(b'\x00').decode('ascii', errors='replace')

        # 验证 entry 1
        if e1_size < 100 or e1_size > data_len:
            continue
        if e1_offset < PAGE_SIZE or e1_offset >= data_len:
            continue

        # 检查 ramdisk 数据是否是 LZ4 legacy 格式
        e0_data_start = data[e0_offset:e0_offset+4]
        e1_data_start = data[e1_offset:e1_offset+4]

        is_lz4_e0 = (e0_data_start == LZ4_LEGACY_MAGIC)
        is_lz4_e1 = (e1_data_start == LZ4_LEGACY_MAGIC)

        if not is_lz4_e0 and not is_lz4_e1:
            continue

        print(f"找到 ramdisk table @ offset 0x{pos:X}")
        print(f"  Entry 0: type=platform, size={e0_size}, offset=0x{e0_offset:X}, lz4={is_lz4_e0}")
        print(f"  Entry 1: type=recovery, size={e1_size}, offset=0x{e1_offset:X}, name=\"{e1_name}\", lz4={is_lz4_e1}")

        return {
            'table_offset': pos,
            'entry0': {
                'type': 0,
                'size': e0_size,
                'offset': e0_offset,
            },
            'entry1': {
                'type': 1,
                'size': e1_size,
                'offset': e1_offset,
                'name': e1_name,
            },
        }

    return None


def find_dtb(data, ramdisk_end):
    """
    搜索 DTB。
    DTB 以 FDT magic (0xD00DFEED) 开头，在 MTK 格式中可能在 offset+64 处。
    DTB 通常在 ramdisk table 之后。
    """
    fdt_magic = b'\xd0\x0d\xfe\xed'
    pos = ramdisk_end

    while pos < len(data) - 4:
        idx = data.find(fdt_magic, pos)
        if idx == -1:
            break

        # 验证：DTB 的 totalsize 字段（在 magic 后 4 字节）
        if idx + 8 <= len(data):
            dtb_size = struct.unpack('>I', data[idx+4:idx+8])[0]
            if 100000 < dtb_size < 2000000:  # DTB 大小通常在 100KB-2MB
                print(f"找到 DTB @ offset 0x{idx:X}, size={dtb_size}")
                return idx, dtb_size

        pos = idx + 1

    return -1, 0


def repack_vendor_boot(input_path, output_path, target_size=None):
    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    print(f"输入文件: {input_path} ({len(data)} bytes, {len(data)/1024/1024:.1f} MB)")

    # 验证 VNDRBOOT magic
    if data[:8] != b'VNDRBOOT':
        raise ValueError(f"Invalid magic: {data[:8]}")

    page_size = struct.unpack('<I', data[12:16])[0]
    print(f"page_size: {page_size}")

    # 1. 搜索 ramdisk table
    table_info = find_ramdisk_table(data)
    if not table_info:
        raise ValueError("找不到 ramdisk table")

    e0 = table_info['entry0']  # platform
    e1 = table_info['entry1']  # recovery
    table_offset = table_info['table_offset']

    # 2. 提取 ramdisk 数据
    platform_data = data[e0['offset']:e0['offset'] + e0['size']]
    recovery_data = data[e1['offset']:e1['offset'] + e1['size']]

    print(f"\n原始 platform ramdisk: offset=0x{e0['offset']:X}, size={e0['size']} ({e0['size']/1024/1024:.1f} MB)")
    print(f"原始 recovery ramdisk: offset=0x{e1['offset']:X}, size={e1['size']} ({e1['size']/1024/1024:.1f} MB)")

    # 3. 创建空 CPIO 并 LZ4 压缩
    empty_cpio = create_empty_cpio()
    empty_cpio_lz4 = lz4_legacy_compress(empty_cpio)
    print(f"\n空 CPIO: {len(empty_cpio)} bytes -> LZ4: {len(empty_cpio_lz4)} bytes")

    # 4. 计算 DTB 位置
    # DTB 通常在 ramdisk table 之后
    table_end = table_offset + 64  # 2 entries * 32 bytes
    table_end_aligned = align_up(table_end, page_size)

    dtb_offset, dtb_size = find_dtb(data, table_end_aligned)

    if dtb_offset < 0:
        # 如果找不到 DTB，从 header 读取 dtb_size
        # 尝试不同的偏移
        for off in [32, 40, 44, 48]:
            val = struct.unpack('<I', data[off:off+4])[0]
            if 100000 < val < 2000000:
                dtb_size = val
                print(f"从 header offset {off} 读取 dtb_size: {dtb_size}")
                break

        if dtb_size == 0:
            print("警告: 找不到 DTB，将搜索整个文件")
            # 搜索 FDT magic
            fdt_magic = b'\xd0\x0d\xfe\xed'
            for i in range(page_size, len(data) - 4):
                if data[i:i+4] == fdt_magic:
                    dtb_size = struct.unpack('>I', data[i+4:i+8])[0]
                    if 100000 < dtb_size < 2000000:
                        dtb_offset = i
                        print(f"找到 DTB @ 0x{i:X}, size={dtb_size}")
                        break

        if dtb_offset < 0:
            raise ValueError("找不到 DTB")

    dtb_data = data[dtb_offset:dtb_offset + dtb_size]

    # 5. 计算新布局
    # 新布局: header(1页) + platform_ramdisk(原recovery) + recovery_ramdisk(空cpio) + table + dtb
    # 保持与原厂相同的布局顺序

    new_platform_offset = page_size  # ramdisk 从第二页开始
    new_platform_size = len(recovery_data)  # 原 recovery 作为新 platform
    new_platform_pages = (new_platform_size + page_size - 1) // page_size

    new_recovery_offset = new_platform_offset + new_platform_pages * page_size
    new_recovery_size = len(empty_cpio_lz4)
    new_recovery_pages = (new_recovery_size + page_size - 1) // page_size

    new_table_offset = new_recovery_offset + new_recovery_pages * page_size
    # table 占 1 页（64 bytes，对齐到 4096）

    new_dtb_offset = new_table_offset + page_size
    new_dtb_pages = (dtb_size + page_size - 1) // page_size

    print(f"\n新布局:")
    print(f"  header:      0x0 - 0x{page_size:X}")
    print(f"  platform:    0x{new_platform_offset:X} - 0x{new_recovery_offset:X} ({new_platform_size} bytes)")
    print(f"  recovery:    0x{new_recovery_offset:X} - 0x{new_table_offset:X} ({new_recovery_size} bytes)")
    print(f"  table:       0x{new_table_offset:X} - 0x{new_dtb_offset:X}")
    print(f"  dtb:         0x{new_dtb_offset:X} ({dtb_size} bytes)")

    # 6. 构建新的 vendor_boot
    result = bytearray(target_size if target_size else len(data))

    # 复制 header（第一页）
    result[:page_size] = data[:page_size]

    # 写入 platform ramdisk（原 recovery）
    result[new_platform_offset:new_platform_offset + new_platform_size] = recovery_data

    # 写入 recovery ramdisk（空 CPIO）
    result[new_recovery_offset:new_recovery_offset + new_recovery_size] = empty_cpio_lz4

    # 写入 ramdisk table
    # Entry 0: platform (原 recovery 的数据)
    entry0 = bytearray(32)
    struct.pack_into('<I', entry0, 0, 0)  # type = platform
    struct.pack_into('<I', entry0, 4, 0)  # name_idx = 0
    struct.pack_into('<Q', entry0, 8, new_platform_size)  # body_size
    struct.pack_into('<Q', entry0, 16, new_platform_offset)  # ramdisk_offset
    # name (24:32) = 空

    # Entry 1: recovery (空 CPIO)
    entry1 = bytearray(32)
    struct.pack_into('<I', entry1, 0, 1)  # type = recovery
    struct.pack_into('<I', entry1, 4, 0)  # name_idx = 0
    struct.pack_into('<Q', entry1, 8, new_recovery_size)  # body_size
    struct.pack_into('<Q', entry1, 16, new_recovery_offset)  # ramdisk_offset
    name_bytes = b'recovery\x00'
    entry1[24:24 + len(name_bytes)] = name_bytes

    result[new_table_offset:new_table_offset + 32] = entry0
    result[new_table_offset + 32:new_table_offset + 64] = entry1

    # 写入 DTB
    result[new_dtb_offset:new_dtb_offset + dtb_size] = dtb_data

    # 7. 更新 header 中的 table_offset
    # 尝试在多个可能的偏移写入 table_offset
    # 标准 AOSP v4: offset 24 (entry_num), 但这里可能不同
    # 安全做法：搜索 header 中的旧 table_offset 值并替换
    old_table_offset_bytes = struct.pack('<I', table_offset)
    new_table_offset_bytes = struct.pack('<I', new_table_offset)

    # 在 header 区域搜索旧 table_offset 并替换
    replaced = False
    for off in range(8, page_size - 3):
        if data[off:off+4] == old_table_offset_bytes:
            result[off:off+4] = new_table_offset_bytes
            print(f"  替换 table_offset: header offset {off}: {table_offset} -> {new_table_offset}")
            replaced = True
            break

    if not replaced:
        print(f"  警告: 未在 header 中找到 table_offset={table_offset}")
        # 尝试写入标准位置
        # 不确定正确偏移，跳过

    # 8. 更新 header 中的 entry_num 和 entry_size（如果存在）
    # 搜索旧 entry 的 offset 值并替换为新值
    old_e0_offset_bytes = struct.pack('<Q', e0['offset'])
    new_e0_offset_bytes = struct.pack('<Q', new_platform_offset)
    old_e1_offset_bytes = struct.pack('<Q', e1['offset'])
    new_e1_offset_bytes = struct.pack('<Q', new_recovery_offset)

    old_e0_size_bytes = struct.pack('<Q', e0['size'])
    new_e0_size_bytes = struct.pack('<Q', new_platform_size)
    old_e1_size_bytes = struct.pack('<Q', e1['size'])
    new_e1_size_bytes = struct.pack('<Q', new_recovery_size)

    # 不需要更新 header 中的这些值，因为 table 本身已经更新了
    # header 中只有 table_offset 需要更新

    # 如果指定了目标大小，确保填充
    if target_size and len(result) < target_size:
        result += b'\xFF' * (target_size - len(result))
    elif target_size and len(result) > target_size:
        result = result[:target_size]

    print(f"\n最终大小: {len(result)} bytes ({len(result)/1024/1024:.1f} MB)")

    with open(output_path, 'wb') as f:
        f.write(result)
    print(f"已保存到: {output_path}")


def main():
    if len(sys.argv) < 3:
        print(f"用法: {sys.argv[0]} <vendor_boot.img> <output.img> [target_size]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    target_size = int(sys.argv[3]) if len(sys.argv) > 3 else None

    repack_vendor_boot(input_path, output_path, target_size)


if __name__ == '__main__':
    main()

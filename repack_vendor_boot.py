#!/usr/bin/env python3
"""
重新打包 vendor_boot.img：
  在原 platform ramdisk 位置写入空 CPIO，然后交换 table 指针。

  用户验证过的方案：
  - platform ramdisk → TWRP recovery.cpio（原 recovery ramdisk 数据）
  - recovery ramdisk → 空 CPIO

用法：python3 repack_vendor_boot.py <vendor_boot.img> <output.img>
"""

import struct
import sys
import lz4.block

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
    pad = (4 - (len(header) % 4)) % 4
    header += b'\x00' * pad
    return header


def lz4_legacy_compress(data):
    """用 LZ4 Legacy 格式压缩数据"""
    result = bytearray(LZ4_LEGACY_MAGIC)
    compressed = lz4.block.compress(
        data, mode='high_compression', compression=12, store_size=False
    )
    result += struct.pack('<I', len(compressed))
    result += compressed
    result += struct.pack('<I', 0)  # end marker
    return bytes(result)


def calculate_lz4_size(data, start):
    """计算 LZ4 legacy 流的总大小。返回 -1 表示无效。"""
    file_len = len(data)
    if start + 8 > file_len:
        return -1
    if data[start:start+4] != LZ4_LEGACY_MAGIC:
        return -1

    offset = start + 4
    total = 4

    while offset + 4 <= file_len:
        bsize = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        total += 4

        if bsize == 0:
            return total  # end marker

        actual_size = bsize & 0x7FFFFFFF
        if offset + actual_size > file_len:
            return -1  # 无效

        offset += actual_size
        total += actual_size

    return -1


def find_ramdisks(data):
    """
    搜索所有 LZ4 legacy magic，计算每个的 size。
    只保留 size > 100000 的（过滤假 magic）。
    不跳过数据，逐个 magic 检查。
    """
    file_len = len(data)
    ramdisks = []
    pos = 0

    while pos < file_len - 4:
        pos = data.find(LZ4_LEGACY_MAGIC, pos)
        if pos == -1:
            break

        size = calculate_lz4_size(data, pos)
        if size > 100000 and size < file_len:
            ramdisks.append((pos, size))

        pos += 4  # 只跳过 magic，不跳过整个 ramdisk

    return ramdisks


def parse_header_v4(data):
    """
    解析 VNDRBOOT v4 header。
    返回 (table_offset, entry_num, entry_size, dtb_size) 或 None。
    """
    if data[:8] != b'VNDRBOOT':
        return None

    header_version = struct.unpack('<I', data[8:12])[0]
    page_size = struct.unpack('<I', data[12:16])[0]

    print(f"  VNDRBOOT v{header_version}, page_size={page_size}")

    # dump header 前 64 字节
    for i in range(0, 64, 16):
        hex_str = ' '.join(f'{b:02x}' for b in data[i:i+16])
        print(f"  header 0x{i:02X}: {hex_str}")

    # 标准 AOSP v4: offset 24 = table_offset, 28 = entry_num, 32 = entry_size, 36 = dtb_size
    table_offset = struct.unpack('<I', data[24:28])[0]
    entry_num = struct.unpack('<I', data[28:32])[0]
    entry_size = struct.unpack('<I', data[32:36])[0]
    dtb_size = struct.unpack('<I', data[36:40])[0]

    print(f"  header v4: table_offset={table_offset}, entry_num={entry_num}, entry_size={entry_size}, dtb_size={dtb_size}")

    # 验证
    if 0 < entry_num < 10 and 0 < entry_size < 100 and 0 < table_offset < len(data):
        return table_offset, entry_num, entry_size, dtb_size

    return None


def repack_vendor_boot(input_path, output_path):
    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    print(f"输入文件: {input_path} ({len(data)} bytes, {len(data)/1024/1024:.1f} MB)")

    if data[:8] != b'VNDRBOOT':
        raise ValueError(f"Invalid magic: {data[:8]}")

    page_size = struct.unpack('<I', data[12:16])[0]

    # 方案 1：从 header 读取 table_offset
    header_info = parse_header_v4(data)

    platform_offset = None
    platform_size = None
    recovery_offset = None
    recovery_size = None
    table_offset = None
    use_8byte = False

    if header_info:
        table_offset, entry_num, entry_size, dtb_size = header_info
        print(f"\n从 header 读取 table @ 0x{table_offset:X}, {entry_num} entries")

        # 解析 table entries
        for i in range(entry_num):
            off = table_offset + i * entry_size
            entry = data[off:off + entry_size]
            if len(entry) < entry_size:
                break

            # 尝试 4 字节格式: name_size(4) + type(4) + name_offset(4) + size(4) + offset(4) + flags(4) + name(8)
            e_type_4 = struct.unpack('<I', entry[4:8])[0]
            e_size_4 = struct.unpack('<I', entry[12:16])[0]
            e_offset_4 = struct.unpack('<I', entry[16:20])[0]

            # 尝试 8 字节格式: type(4) + name_idx(4) + size(8) + offset(8) + name(8)
            e_type_8 = struct.unpack('<I', entry[0:4])[0]
            e_size_8 = struct.unpack('<Q', entry[8:16])[0]
            e_offset_8 = struct.unpack('<Q', entry[16:24])[0]

            # 验证 4 字节格式
            if e_type_4 <= 2 and 100 < e_size_4 < len(data) and page_size <= e_offset_4 < len(data):
                # 检查 offset 处是否是 LZ4 magic
                if data[e_offset_4:e_offset_4+4] == LZ4_LEGACY_MAGIC:
                    actual_size = calculate_lz4_size(data, e_offset_4)
                    if actual_size > 0:
                        print(f"  Entry {i}: type={e_type_4}, size={e_size_4}, offset=0x{e_offset_4:X} (4字节格式, lz4_size={actual_size})")
                        if e_type_4 == 0:
                            platform_offset = e_offset_4
                            platform_size = e_size_4
                            use_8byte = False
                        elif e_type_4 == 1:
                            recovery_offset = e_offset_4
                            recovery_size = e_size_4
                            use_8byte = False

            # 验证 8 字节格式
            elif e_type_8 <= 2 and 100 < e_size_8 < len(data) and page_size <= e_offset_8 < len(data):
                if data[e_offset_8:e_offset_8+4] == LZ4_LEGACY_MAGIC:
                    actual_size = calculate_lz4_size(data, e_offset_8)
                    if actual_size > 0:
                        print(f"  Entry {i}: type={e_type_8}, size={e_size_8}, offset=0x{e_offset_8:X} (8字节格式, lz4_size={actual_size})")
                        if e_type_8 == 0:
                            platform_offset = e_offset_8
                            platform_size = e_size_8
                            use_8byte = True
                        elif e_type_8 == 1:
                            recovery_offset = e_offset_8
                            recovery_size = e_size_8
                            use_8byte = True

    # 方案 2：搜索 LZ4 magic
    if platform_offset is None or recovery_offset is None:
        print(f"\nheader 解析未找到所有 ramdisk，搜索 LZ4 magic...")
        ramdisks = find_ramdisks(data)
        print(f"找到 {len(ramdisks)} 个有效的 LZ4 ramdisk:")
        for i, (offset, size) in enumerate(ramdisks):
            print(f"  [{i}] offset=0x{offset:X}, size={size} ({size/1024/1024:.1f} MB)")

        if len(ramdisks) >= 2:
            platform_offset, platform_size = ramdisks[0]
            recovery_offset, recovery_size = ramdisks[1]
            # 还需要找到 table
            table_offset = None

    if platform_offset is None or recovery_offset is None:
        raise ValueError("找不到两个 ramdisk")

    print(f"\nPlatform ramdisk: offset=0x{platform_offset:X}, size={platform_size} ({platform_size/1024/1024:.1f} MB)")
    print(f"Recovery ramdisk: offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB)")

    # 搜索 table
    if table_offset is None:
        table_offset = find_table_by_values(data, platform_offset, platform_size, recovery_offset, recovery_size, use_8byte)
        if table_offset < 0:
            # 尝试另一种格式
            use_8byte = not use_8byte
            table_offset = find_table_by_values(data, platform_offset, platform_size, recovery_offset, recovery_size, use_8byte)
            if table_offset >= 0:
                print(f"  table 格式切换为 {'8' if use_8byte else '4'} 字节")

    if table_offset < 0:
        raise ValueError("找不到 ramdisk table")

    print(f"Table @ 0x{table_offset:X}, 格式: {'8' if use_8byte else '4'} 字节")

    # 创建空 CPIO 并压缩
    empty_cpio = create_empty_cpio()
    empty_cpio_lz4 = lz4_legacy_compress(empty_cpio)
    print(f"\n空 CPIO: {len(empty_cpio)} bytes -> LZ4: {len(empty_cpio_lz4)} bytes")

    # 在原 platform ramdisk 位置写入空 CPIO
    print(f"在原 platform 位置 0x{platform_offset:X} 写入空 CPIO ({len(empty_cpio_lz4)} bytes)")
    data[platform_offset:platform_offset + len(empty_cpio_lz4)] = empty_cpio_lz4
    remaining = platform_size - len(empty_cpio_lz4)
    if remaining > 0:
        data[platform_offset + len(empty_cpio_lz4):platform_offset + platform_size] = b'\x00' * remaining

    # 修改 table 指针
    entry0 = bytearray(data[table_offset:table_offset + 32])
    entry1 = bytearray(data[table_offset + 32:table_offset + 64])

    if use_8byte:
        # 8字节格式: type(4) + name_idx(4) + size(8) + offset(8) + name(8)
        struct.pack_into('<Q', entry0, 8, recovery_size)
        struct.pack_into('<Q', entry0, 16, recovery_offset)
        struct.pack_into('<Q', entry1, 8, len(empty_cpio_lz4))
        struct.pack_into('<Q', entry1, 16, platform_offset)
    else:
        # 4字节格式: name_size(4) + type(4) + name_offset(4) + size(4) + offset(4) + flags(4) + name(8)
        struct.pack_into('<I', entry0, 12, recovery_size)
        struct.pack_into('<I', entry0, 16, recovery_offset)
        struct.pack_into('<I', entry1, 12, len(empty_cpio_lz4))
        struct.pack_into('<I', entry1, 16, platform_offset)

    data[table_offset:table_offset + 32] = entry0
    data[table_offset + 32:table_offset + 64] = entry1

    print(f"\n交换完成:")
    print(f"  Entry 0 (platform): offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB) [TWRP 完整内容]")
    print(f"  Entry 1 (recovery): offset=0x{platform_offset:X}, size={len(empty_cpio_lz4)} [空 CPIO]")

    with open(output_path, 'wb') as f:
        f.write(data)

    print(f"\n最终大小: {len(data)} bytes ({len(data)/1024/1024:.1f} MB)")
    print(f"已保存到: {output_path}")


def find_table_by_values(data, platform_offset, platform_size, recovery_offset, recovery_size, use_8byte):
    """通过已知的 offset/size 值搜索 table 位置"""
    file_len = len(data)

    if use_8byte:
        # 8字节格式: offset 在 entry position 16-23
        search_val = struct.pack('<Q', platform_offset)
    else:
        # 4字节格式: offset 在 entry position 16-19
        search_val = struct.pack('<I', platform_offset)

    pos = 0
    while pos < file_len - 64:
        found = data.find(search_val, pos)
        if found == -1:
            break
        pos = found + 1

        entry_start = found - 16  # offset 字段在 entry 中 position 16
        if entry_start < 0:
            continue

        entry = data[entry_start:entry_start + 32]
        if len(entry) < 32:
            continue

        if use_8byte:
            e_type = struct.unpack('<I', entry[0:4])[0]
            e_size = struct.unpack('<Q', entry[8:16])[0]
            e_offset = struct.unpack('<Q', entry[16:24])[0]

            if e_type == 0 and e_size == platform_size and e_offset == platform_offset:
                next_entry = data[entry_start + 32:entry_start + 64]
                if len(next_entry) >= 32:
                    n_type = struct.unpack('<I', next_entry[0:4])[0]
                    n_size = struct.unpack('<Q', next_entry[8:16])[0]
                    n_offset = struct.unpack('<Q', next_entry[16:24])[0]
                    if n_type == 1 and n_size == recovery_size and n_offset == recovery_offset:
                        print(f"  找到 table @ 0x{entry_start:X} (8字节格式)")
                        return entry_start
        else:
            e_type = struct.unpack('<I', entry[4:8])[0]
            e_size = struct.unpack('<I', entry[12:16])[0]
            e_offset = struct.unpack('<I', entry[16:20])[0]

            if e_type == 0 and e_size == platform_size and e_offset == platform_offset:
                next_entry = data[entry_start + 32:entry_start + 64]
                if len(next_entry) >= 32:
                    n_type = struct.unpack('<I', next_entry[4:8])[0]
                    n_size = struct.unpack('<I', next_entry[12:16])[0]
                    n_offset = struct.unpack('<I', next_entry[16:20])[0]
                    if n_type == 1 and n_size == recovery_size and n_offset == recovery_offset:
                        print(f"  找到 table @ 0x{entry_start:X} (4字节格式)")
                        return entry_start

    return -1


def main():
    if len(sys.argv) < 3:
        print(f"用法: {sys.argv[0]} <vendor_boot.img> <output.img>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    repack_vendor_boot(input_path, output_path)


if __name__ == '__main__':
    main()

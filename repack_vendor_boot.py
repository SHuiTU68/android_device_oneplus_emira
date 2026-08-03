#!/usr/bin/env python3
"""
重新打包 vendor_boot.img：
  方案：在原 platform ramdisk 位置写入空 CPIO，然后交换 table 指针。

  用户验证过的方案：
  - platform ramdisk → TWRP recovery.cpio（原 recovery ramdisk 数据）
  - recovery ramdisk → 空 CPIO

  实现：
  1. 找到两个有效的 LZ4 legacy ramdisk（platform + recovery）
  2. 搜索 ramdisk table
  3. 在原 platform ramdisk 位置写入空 CPIO（LZ4 压缩，约 56 bytes）
  4. 修改 table：
     - entry 0 (platform): offset→原recovery offset, size→原recovery size
     - entry 1 (recovery): offset→原platform offset, size→空CPIO size

  这样数据移动最小（只在原 platform 位置写 56 bytes），recovery 是真正的空 CPIO。

用法：python3 repack_vendor_boot.py <vendor_boot.img> <output.img>
"""

import struct
import sys
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
    """
    计算 LZ4 legacy 流的总大小（从 magic 开始到 end marker）。
    返回 -1 表示无效。
    """
    file_len = len(data)
    if start + 4 > file_len:
        return -1
    if data[start:start+4] != LZ4_LEGACY_MAGIC:
        return -1

    offset = start + 4
    total = 4
    max_remaining = file_len - start

    while offset < file_len - 3:
        bsize = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        total += 4

        if bsize == 0:
            return total

        actual_size = bsize & 0x7FFFFFFF
        if actual_size > max_remaining or offset + actual_size > file_len:
            return -1

        offset += actual_size
        total += actual_size

    return -1


def find_ramdisks(data):
    """找到文件中所有有效的 LZ4 legacy ramdisk"""
    file_len = len(data)
    ramdisks = []
    pos = 0

    while pos < file_len - 4:
        pos = data.find(LZ4_LEGACY_MAGIC, pos)
        if pos == -1:
            break

        size = calculate_lz4_size(data, pos)
        if size > 0 and size < file_len:
            ramdisks.append((pos, size))
            pos += size
        else:
            pos += 1

    return ramdisks


def find_table(data, platform_offset, platform_size, recovery_offset, recovery_size):
    """
    搜索 ramdisk table。
    支持 4 字节和 8 字节偏移两种格式。
    """
    file_len = len(data)

    # 尝试 4 字节格式: name_size(4) + type(4) + name_offset(4) + size(4) + offset(4) + flags(4) + name(8)
    platform_offset_bytes_4 = struct.pack('<I', platform_offset)
    search_pos = 0
    while search_pos < file_len - 64:
        found = data.find(platform_offset_bytes_4, search_pos)
        if found == -1:
            break
        search_pos = found + 1

        entry_start = found - 16  # offset 字段在 entry 中 position 16
        if entry_start < 0:
            continue

        entry = data[entry_start:entry_start + 32]
        if len(entry) < 32:
            continue

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
                    return entry_start, 32, False  # table_offset, entry_size, use_8byte

    # 尝试 8 字节格式: type(4) + name_idx(4) + size(8) + offset(8) + name(8)
    platform_offset_bytes_8 = struct.pack('<Q', platform_offset)
    search_pos = 0
    while search_pos < file_len - 64:
        found = data.find(platform_offset_bytes_8, search_pos)
        if found == -1:
            break
        search_pos = found + 1

        entry_start = found - 16  # offset 字段在 entry 中 position 16
        if entry_start < 0:
            continue

        entry = data[entry_start:entry_start + 32]
        if len(entry) < 32:
            continue

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
                    return entry_start, 32, True  # table_offset, entry_size, use_8byte

    return -1, 0, False


def repack_vendor_boot(input_path, output_path):
    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    print(f"输入文件: {input_path} ({len(data)} bytes, {len(data)/1024/1024:.1f} MB)")

    if data[:8] != b'VNDRBOOT':
        raise ValueError(f"Invalid magic: {data[:8]}")

    # 1. 找到所有有效的 LZ4 ramdisk
    ramdisks = find_ramdisks(data)
    print(f"\n找到 {len(ramdisks)} 个有效的 LZ4 ramdisk:")
    for i, (offset, size) in enumerate(ramdisks):
        print(f"  [{i}] offset=0x{offset:X}, size={size} ({size/1024/1024:.1f} MB)")

    if len(ramdisks) < 2:
        raise ValueError(f"需要至少 2 个 ramdisk，找到 {len(ramdisks)} 个")

    platform_offset, platform_size = ramdisks[0]
    recovery_offset, recovery_size = ramdisks[1]

    print(f"\nPlatform ramdisk: offset=0x{platform_offset:X}, size={platform_size} ({platform_size/1024/1024:.1f} MB)")
    print(f"Recovery ramdisk: offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB)")

    # 2. 搜索 ramdisk table
    table_offset, entry_size, use_8byte = find_table(
        data, platform_offset, platform_size, recovery_offset, recovery_size
    )

    if table_offset < 0:
        print("\n错误: 找不到 ramdisk table!")
        raise ValueError("找不到 ramdisk table")

    # 3. 创建空 CPIO 并 LZ4 压缩
    empty_cpio = create_empty_cpio()
    empty_cpio_lz4 = lz4_legacy_compress(empty_cpio)
    print(f"\n空 CPIO: {len(empty_cpio)} bytes -> LZ4: {len(empty_cpio_lz4)} bytes")

    # 4. 在原 platform ramdisk 位置写入空 CPIO
    # 原 platform ramdisk 是 16.8MB，空 CPIO LZ4 只有 56 bytes，完全够用
    print(f"\n在原 platform 位置 0x{platform_offset:X} 写入空 CPIO ({len(empty_cpio_lz4)} bytes)")
    data[platform_offset:platform_offset + len(empty_cpio_lz4)] = empty_cpio_lz4
    # 清零剩余空间（避免残留数据被误读）
    remaining = platform_size - len(empty_cpio_lz4)
    if remaining > 0:
        data[platform_offset + len(empty_cpio_lz4):platform_offset + platform_size] = b'\x00' * remaining

    # 5. 修改 table 指针
    entry0 = bytearray(data[table_offset:table_offset + entry_size])
    entry1 = bytearray(data[table_offset + entry_size:table_offset + 2 * entry_size])

    if use_8byte:
        # 8字节格式: type(4) + name_idx(4) + size(8) + offset(8) + name(8)
        # Entry 0 (platform) → 指向原 recovery ramdisk
        struct.pack_into('<Q', entry0, 8, recovery_size)
        struct.pack_into('<Q', entry0, 16, recovery_offset)
        # Entry 1 (recovery) → 指向空 CPIO（在原 platform 位置）
        struct.pack_into('<Q', entry1, 8, len(empty_cpio_lz4))
        struct.pack_into('<Q', entry1, 16, platform_offset)
    else:
        # 4字节格式: name_size(4) + type(4) + name_offset(4) + size(4) + offset(4) + flags(4) + name(8)
        # Entry 0 (platform) → 指向原 recovery ramdisk
        struct.pack_into('<I', entry0, 12, recovery_size)
        struct.pack_into('<I', entry0, 16, recovery_offset)
        # Entry 1 (recovery) → 指向空 CPIO（在原 platform 位置）
        struct.pack_into('<I', entry1, 12, len(empty_cpio_lz4))
        struct.pack_into('<I', entry1, 16, platform_offset)

    data[table_offset:table_offset + entry_size] = entry0
    data[table_offset + entry_size:table_offset + 2 * entry_size] = entry1

    print(f"\n交换完成:")
    print(f"  Entry 0 (platform): offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB) [TWRP 完整内容]")
    print(f"  Entry 1 (recovery): offset=0x{platform_offset:X}, size={len(empty_cpio_lz4)} [空 CPIO]")

    # 6. 写出文件
    with open(output_path, 'wb') as f:
        f.write(data)

    print(f"\n最终大小: {len(data)} bytes ({len(data)/1024/1024:.1f} MB)")
    print(f"已保存到: {output_path}")


def main():
    if len(sys.argv) < 3:
        print(f"用法: {sys.argv[0]} <vendor_boot.img> <output.img>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    repack_vendor_boot(input_path, output_path)


if __name__ == '__main__':
    main()

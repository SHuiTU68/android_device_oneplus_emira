#!/usr/bin/env python3
"""
重新打包 vendor_boot.img：
  方案：只交换 ramdisk table 中的 offset/size 指针，不移动数据。

  1. 找到两个有效的 LZ4 legacy ramdisk（platform + recovery）
  2. 在文件中搜索 ramdisk table（包含这两个 ramdisk 的 offset/size）
  3. 交换 table 中两个 entry 的 offset 和 size
  4. 写回

  这样数据位置完全不变，只是 table 指向换了：
  - platform ramdisk → 指向原 recovery ramdisk 数据（TWRP 完整内容）
  - recovery ramdisk → 指向原 platform ramdisk 数据（first_stage_ramdisk + lib/modules）

用法：python3 repack_vendor_boot.py <vendor_boot.img> <output.img>
"""

import struct
import sys

PAGE_SIZE = 4096
LZ4_LEGACY_MAGIC = b'\x02\x21\x4c\x18'


def calculate_lz4_size(data, start):
    """
    计算 LZ4 legacy 流的总大小（从 magic 开始到 end marker）。
    返回 -1 表示无效（block_size 超出文件范围）。
    """
    file_len = len(data)
    if start + 4 > file_len:
        return -1
    if data[start:start+4] != LZ4_LEGACY_MAGIC:
        return -1

    offset = start + 4  # skip magic
    total = 4
    max_remaining = file_len - start

    while offset < file_len - 3:
        bsize = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        total += 4

        if bsize == 0:
            # end marker
            return total

        actual_size = bsize & 0x7FFFFFFF
        # 验证 block_size 合理性
        if actual_size > max_remaining or offset + actual_size > file_len:
            return -1  # 无效的 LZ4 流

        offset += actual_size
        total += actual_size

    return -1  # 没有找到 end marker


def find_ramdisks(data):
    """
    找到文件中所有有效的 LZ4 legacy ramdisk。
    返回 [(offset, size), ...] 列表，按 offset 排序。
    """
    file_len = len(data)
    ramdisks = []
    pos = 0

    while pos < file_len - 4:
        pos = data.find(LZ4_LEGACY_MAGIC, pos)
        if pos == -1:
            break

        size = calculate_lz4_size(data, pos)
        if size > 0 and size < file_len:  # 有效且不超过文件大小
            ramdisks.append((pos, size))
            # 跳过这个 ramdisk 的数据
            pos += size
        else:
            pos += 1

    return ramdisks


def find_table(data, platform_offset, platform_size, recovery_offset, recovery_size):
    """
    在文件中搜索 ramdisk table。

    AOSP v4 table entry 格式（32 字节）:
      offset 0:  ramdisk_name_size (uint32)
      offset 4:  ramdisk_type (uint32): 0=platform, 1=recovery, 2=dlkm
      offset 8:  ramdisk_name_offset (uint32)
      offset 12: ramdisk_size (uint32)
      offset 16: ramdisk_offset (uint32)
      offset 20: ramdisk_flags (uint32)
      offset 24: ramdisk_name (8 bytes, padded)

    搜索策略：在文件中搜索包含 platform_offset 和 platform_size 的 32 字节块。
    """
    file_len = len(data)

    # platform entry 中应该包含:
    #   offset 4: type = 0 (platform)
    #   offset 12: size = platform_size
    #   offset 16: offset = platform_offset

    # 搜索 platform_size 的 4 字节 LE 表示
    platform_size_bytes = struct.pack('<I', platform_size)
    platform_offset_bytes = struct.pack('<I', platform_offset)

    # 也尝试 8 字节表示（某些格式可能用 uint64）
    platform_size_bytes_8 = struct.pack('<Q', platform_size)
    platform_offset_bytes_8 = struct.pack('<Q', platform_offset)

    search_pos = 0
    while search_pos < file_len - 32:
        # 搜索 platform_offset 的 4 字节表示
        found = data.find(platform_offset_bytes, search_pos)
        if found == -1:
            break
        search_pos = found + 1

        # 检查这是否是 table entry 中的 offset 字段 (entry offset 16)
        entry_start = found - 16
        if entry_start < 0:
            continue

        entry = data[entry_start:entry_start + 32]
        if len(entry) < 32:
            continue

        # 读取 entry 字段
        e_name_size = struct.unpack('<I', entry[0:4])[0]
        e_type = struct.unpack('<I', entry[4:8])[0]
        e_name_offset = struct.unpack('<I', entry[8:12])[0]
        e_size = struct.unpack('<I', entry[12:16])[0]
        e_offset = struct.unpack('<I', entry[16:20])[0]
        e_flags = struct.unpack('<I', entry[20:24])[0]

        # 验证：这是 platform entry
        if e_type == 0 and e_size == platform_size and e_offset == platform_offset:
            # 检查下一个 entry 是否是 recovery
            next_entry = data[entry_start + 32:entry_start + 64]
            if len(next_entry) >= 32:
                n_type = struct.unpack('<I', next_entry[4:8])[0]
                n_size = struct.unpack('<I', next_entry[12:16])[0]
                n_offset = struct.unpack('<I', next_entry[16:20])[0]

                if n_type == 1 and n_size == recovery_size and n_offset == recovery_offset:
                    print(f"  找到 table @ 0x{entry_start:X} (4字节偏移格式)")
                    return entry_start, 32  # table_offset, entry_size

    # 尝试 8 字节偏移格式
    search_pos = 0
    while search_pos < file_len - 64:
        found = data.find(platform_offset_bytes_8, search_pos)
        if found == -1:
            break
        search_pos = found + 1

        # entry 中 offset 在 position 16-23 (8 bytes)
        entry_start = found - 16
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
                    print(f"  找到 table @ 0x{entry_start:X} (8字节偏移格式)")
                    return entry_start, 32

    return -1, 0


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

    # 前两个是 platform 和 recovery
    platform_offset, platform_size = ramdisks[0]
    recovery_offset, recovery_size = ramdisks[1]

    print(f"\nPlatform ramdisk: offset=0x{platform_offset:X}, size={platform_size} ({platform_size/1024/1024:.1f} MB)")
    print(f"Recovery ramdisk: offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB)")

    # 2. 搜索 ramdisk table
    table_offset, entry_size = find_table(
        data, platform_offset, platform_size, recovery_offset, recovery_size
    )

    if table_offset < 0:
        print("\n错误: 找不到 ramdisk table!")
        print("无法安全交换 ramdisk 指针。")
        raise ValueError("找不到 ramdisk table")

    # 3. 读取 table entries
    entry0 = bytearray(data[table_offset:table_offset + entry_size])
    entry1 = bytearray(data[table_offset + entry_size:table_offset + 2 * entry_size])

    # 判断 entry 格式（4字节还是8字节偏移）
    e0_type = struct.unpack('<I', entry0[4:8])[0]  # 4字节格式: type at offset 4
    e0_size_4 = struct.unpack('<I', entry0[12:16])[0]
    e0_offset_4 = struct.unpack('<I', entry0[16:20])[0]

    e0_type_8 = struct.unpack('<I', entry0[0:4])[0]  # 8字节格式: type at offset 0
    e0_size_8 = struct.unpack('<Q', entry0[8:16])[0]
    e0_offset_8 = struct.unpack('<Q', entry0[16:24])[0]

    use_8byte = False
    if e0_type_8 == 0 and e0_size_8 == platform_size and e0_offset_8 == platform_offset:
        use_8byte = True
        print(f"\nTable 格式: 8字节偏移")
    elif e0_type == 0 and e0_size_4 == platform_size and e0_offset_4 == platform_offset:
        use_8byte = False
        print(f"\nTable 格式: 4字节偏移")
    else:
        print(f"\n无法确定 table 格式!")
        print(f"  4字节: type={e0_type}, size={e0_size_4}, offset=0x{e0_offset_4:X}")
        print(f"  8字节: type={e0_type_8}, size={e0_size_8}, offset=0x{e0_offset_8:X}")
        raise ValueError("无法确定 table 格式")

    # 4. 交换 table 中的 offset 和 size
    if use_8byte:
        # 8字节格式: type(4) + name_idx(4) + size(8) + offset(8) + name(8)
        # Entry 0 (platform): 改为 recovery 的 offset 和 size
        struct.pack_into('<Q', entry0, 8, recovery_size)
        struct.pack_into('<Q', entry0, 16, recovery_offset)
        # Entry 1 (recovery): 改为 platform 的 offset 和 size
        struct.pack_into('<Q', entry1, 8, platform_size)
        struct.pack_into('<Q', entry1, 16, platform_offset)
    else:
        # 4字节格式: name_size(4) + type(4) + name_offset(4) + size(4) + offset(4) + flags(4) + name(8)
        # Entry 0 (platform): 改为 recovery 的 offset 和 size
        struct.pack_into('<I', entry0, 12, recovery_size)
        struct.pack_into('<I', entry0, 16, recovery_offset)
        # Entry 1 (recovery): 改为 platform 的 offset 和 size
        struct.pack_into('<I', entry1, 12, platform_size)
        struct.pack_into('<I', entry1, 16, platform_offset)

    # 5. 写回 table
    data[table_offset:table_offset + entry_size] = entry0
    data[table_offset + entry_size:table_offset + 2 * entry_size] = entry1

    print(f"\n交换完成:")
    if use_8byte:
        print(f"  Entry 0 (platform): offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB)")
        print(f"  Entry 1 (recovery): offset=0x{platform_offset:X}, size={platform_size} ({platform_size/1024/1024:.1f} MB)")
    else:
        print(f"  Entry 0 (platform): offset=0x{recovery_offset:X}, size={recovery_size} ({recovery_size/1024/1024:.1f} MB)")
        print(f"  Entry 1 (recovery): offset=0x{platform_offset:X}, size={platform_size} ({platform_size/1024/1024:.1f} MB)")

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

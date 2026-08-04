#!/usr/bin/env python3
"""
重新打包 vendor_boot.img：
  1. 搜索 LZ4 legacy magic 定位 ramdisk 数据块
  2. 通过 ramdisk 数据块的偏移反查 ramdisk table
  3. 把 recovery ramdisk 作为新的 platform ramdisk
  4. 创建空的 CPIO 作为新的 recovery ramdisk
  5. 重新打包 vendor_boot.img

用法：python3 repack_vendor_boot.py <vendor_boot.img> <output.img> [target_size]
"""

import struct
import sys
import lz4.block

PAGE_SIZE = 4096
LZ4_LEGACY_MAGIC = b'\x02\x21\x4c\x18'
FDT_MAGIC = b'\xd0\x0d\xfe\xed'


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


def find_lz4_blocks(data):
    """搜索文件中所有 LZ4 legacy magic 的位置"""
    positions = []
    pos = 0
    while True:
        pos = data.find(LZ4_LEGACY_MAGIC, pos)
        if pos == -1:
            break
        positions.append(pos)
        pos += 1
    return positions


def lz4_legacy_decompress(data):
    """解压 LZ4 legacy 格式数据"""
    offset = 4  # skip magic
    decompressed = b''
    while offset < len(data) - 4:
        bsize = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        if bsize == 0:
            break
        actual_size = bsize & 0x7FFFFFFF
        if offset + actual_size > len(data):
            break
        block_data = data[offset:offset+actual_size]
        offset += actual_size
        try:
            dec = lz4.block.decompress(block_data, uncompressed_size=8388608)
            decompressed += dec
        except:
            try:
                dec = lz4.block.decompress(block_data, uncompressed_size=4194304)
                decompressed += dec
            except:
                break
    return decompressed


def find_ramdisk_table(data, lz4_positions):
    """
    通过 LZ4 块的偏移反查 ramdisk table。
    table entry 格式: type(4) + name_idx(4) + body_size(8) + ramdisk_offset(8) + name(8) = 32 bytes
    """
    data_len = len(data)

    for lz4_off in lz4_positions:
        # 搜索 header 区域（第一页）中包含此偏移的 8 字节值
        offset_bytes = struct.pack('<Q', lz4_off)
        for header_pos in range(8, PAGE_SIZE - 7):
            if data[header_pos:header_pos+8] == offset_bytes:
                # 找到了！这可能是 table entry 中的 ramdisk_offset 字段
                # table entry 中 offset 在 position 16-23
                table_entry_start = header_pos - 16
                if table_entry_start < 0:
                    continue

                # 读取 entry
                entry = data[table_entry_start:table_entry_start+32]
                if len(entry) < 32:
                    continue

                v_type = struct.unpack('<I', entry[0:4])[0]
                v_size = struct.unpack('<Q', entry[8:16])[0]

                # 验证 type (0=platform, 1=recovery)
                if v_type > 2:
                    continue
                # 验证 size
                if v_size < 100 or v_size > data_len:
                    continue

                print(f"  可能的 table entry @ header offset {table_entry_start}: type={v_type}, size={v_size}, offset=0x{lz4_off:X}")

                # 检查是否是 table 的一部分（相邻 32 字节应该是另一个 entry）
                # Entry 0 在前，Entry 1 在后
                if v_type == 0:
                    # 这是 platform entry，检查下一个 entry
                    next_entry = data[table_entry_start+32:table_entry_start+64]
                    if len(next_entry) >= 32:
                        next_type = struct.unpack('<I', next_entry[0:4])[0]
                        if next_type == 1:
                            next_size = struct.unpack('<Q', next_entry[8:16])[0]
                            next_offset = struct.unpack('<Q', next_entry[16:24])[0]
                            next_name = next_entry[24:32].rstrip(b'\x00').decode('ascii', errors='replace')
                            if 100 < next_size < data_len and PAGE_SIZE < next_offset < data_len:
                                print(f"  确认! table @ offset {table_entry_start}")
                                return table_entry_start

                elif v_type == 1:
                    # 这是 recovery entry，检查前一个 entry
                    prev_entry = data[table_entry_start-32:table_entry_start]
                    if len(prev_entry) >= 32 and table_entry_start >= 32:
                        prev_type = struct.unpack('<I', prev_entry[0:4])[0]
                        if prev_type == 0:
                            prev_size = struct.unpack('<Q', prev_entry[8:16])[0]
                            prev_offset = struct.unpack('<Q', prev_entry[16:24])[0]
                            if 100 < prev_size < data_len and PAGE_SIZE < prev_offset < data_len:
                                print(f"  确认! table @ offset {table_entry_start-32}")
                                return table_entry_start - 32

    # 如果在 header 中没找到，搜索整个文件
    for lz4_off in lz4_positions:
        offset_bytes = struct.pack('<Q', lz4_off)
        search_pos = 0
        while True:
            found = data.find(offset_bytes, search_pos)
            if found == -1 or found > data_len - 32:
                break
            search_pos = found + 1

            # 检查 found 是否是 entry 中的 offset 字段 (offset 16-23 in entry)
            entry_start = found - 16
            if entry_start < 0:
                continue

            entry = data[entry_start:entry_start+32]
            if len(entry) < 32:
                continue

            v_type = struct.unpack('<I', entry[0:4])[0]
            v_size = struct.unpack('<Q', entry[8:16])[0]

            if v_type > 2:
                continue
            if v_size < 100 or v_size > data_len:
                continue

            # 验证 LZ4 magic 在 offset 处
            if data[lz4_off:lz4_off+4] != LZ4_LEGACY_MAGIC:
                continue

            print(f"  全文搜索: entry @ 0x{entry_start:X}: type={v_type}, size={v_size}, offset=0x{lz4_off:X}")

            if v_type == 0:
                next_entry = data[entry_start+32:entry_start+64]
                if len(next_entry) >= 32:
                    next_type = struct.unpack('<I', next_entry[0:4])[0]
                    if next_type == 1:
                        next_size = struct.unpack('<Q', next_entry[8:16])[0]
                        next_offset = struct.unpack('<Q', next_entry[16:24])[0]
                        if 100 < next_size < data_len and PAGE_SIZE < next_offset < data_len:
                            print(f"  确认! table @ 0x{entry_start:X}")
                            return entry_start

            elif v_type == 1:
                if entry_start >= 32:
                    prev_entry = data[entry_start-32:entry_start]
                    prev_type = struct.unpack('<I', prev_entry[0:4])[0]
                    if prev_type == 0:
                        prev_size = struct.unpack('<Q', prev_entry[8:16])[0]
                        prev_offset = struct.unpack('<Q', prev_entry[16:24])[0]
                        if 100 < prev_size < data_len and PAGE_SIZE < prev_offset < data_len:
                            print(f"  确认! table @ 0x{entry_start-32:X}")
                            return entry_start - 32

    return -1


def repack_vendor_boot(input_path, output_path, target_size=None):
    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    print(f"输入文件: {input_path} ({len(data)} bytes, {len(data)/1024/1024:.1f} MB)")

    # 验证 VNDRBOOT magic
    if data[:8] != b'VNDRBOOT':
        raise ValueError(f"Invalid magic: {data[:8]}")

    page_size = struct.unpack('<I', data[12:16])[0]
    print(f"page_size: {page_size}")

    # 调试：dump header 前 128 字节
    print("\n=== Header dump (前 128 字节) ===")
    for i in range(0, min(128, len(data)), 16):
        hex_str = ' '.join(f'{b:02x}' for b in data[i:i+16])
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
        print(f"  0x{i:04X}: {hex_str}  {ascii_str}")

    # 1. 搜索 LZ4 legacy magic
    lz4_positions = find_lz4_blocks(data)
    print(f"\n找到 {len(lz4_positions)} 个 LZ4 legacy magic:")
    for pos in lz4_positions[:10]:
        print(f"  0x{pos:08X} ({pos})")

    if len(lz4_positions) < 2:
        print("错误: 找不到足够的 LZ4 块")
        # 尝试搜索其他压缩格式
        # gzip
        gzip_pos = data.find(b'\x1f\x8b\x08')
        if gzip_pos != -1:
            print(f"找到 gzip @ 0x{gzip_pos:X}")
        # 搜索 CPIO magic
        cpio_pos = data.find(b'070701')
        if cpio_pos != -1:
            print(f"找到 CPIO @ 0x{cpio_pos:X}")
        raise ValueError("找不到足够的 LZ4 块")

    # 2. 通过 LZ4 块偏移反查 table
    table_offset = find_ramdisk_table(data, lz4_positions)
    if table_offset < 0:
        print("\n无法通过反查找到 table，尝试直接解析 header...")

        # 尝试标准 AOSP v4 header 格式
        # offset 24: v_ramdisk_table_offset
        table_offset = struct.unpack('<I', data[24:28])[0]
        entry_num = struct.unpack('<I', data[28:32])[0]
        entry_size = struct.unpack('<I', data[32:36])[0]

        print(f"  header v4: table_offset={table_offset}, entry_num={entry_num}, entry_size={entry_size}")

        # 验证
        if entry_num > 0 and entry_num < 10 and entry_size > 0 and entry_size < 100:
            if table_offset > 0 and table_offset < len(data):
                entry = data[table_offset:table_offset+32]
                v_type = struct.unpack('<I', entry[0:4])[0]
                if v_type <= 2:
                    print(f"  header v4 格式有效!")
                else:
                    print(f"  header v4 格式无效 (type={v_type})")
                    table_offset = -1
            else:
                table_offset = -1
        else:
            table_offset = -1

        if table_offset < 0:
            # 最后手段：假设 LZ4 块就是 ramdisk，按顺序排列
            print("\n使用 LZ4 块直接作为 ramdisk...")
            if len(lz4_positions) >= 2:
                # 第一个 LZ4 块 = platform, 第二个 = recovery
                platform_offset = lz4_positions[0]
                recovery_offset = lz4_positions[1]

                # 读取 platform size（LZ4 legacy: 压缩数据从 magic 后开始）
                # 需要计算整个 LZ4 流的大小
                platform_size = calculate_lz4_size(data, platform_offset)
                recovery_size = calculate_lz4_size(data, recovery_offset)

                print(f"  platform: offset=0x{platform_offset:X}, size={platform_size}")
                print(f"  recovery: offset=0x{recovery_offset:X}, size={recovery_size}")

                # 直接交换
                do_swap(data, platform_offset, platform_size,
                        recovery_offset, recovery_size,
                        output_path, target_size, page_size, table_offset=-1)
                return

    if table_offset >= 0:
        # 读取 table entries
        entry0 = data[table_offset:table_offset+32]
        entry1 = data[table_offset+32:table_offset+64]

        e0_type = struct.unpack('<I', entry0[0:4])[0]
        e0_size = struct.unpack('<Q', entry0[8:16])[0]
        e0_offset = struct.unpack('<Q', entry0[16:24])[0]

        e1_type = struct.unpack('<I', entry1[0:4])[0]
        e1_size = struct.unpack('<Q', entry1[8:16])[0]
        e1_offset = struct.unpack('<Q', entry1[16:24])[0]
        e1_name = entry1[24:32].rstrip(b'\x00').decode('ascii', errors='replace')

        print(f"\nTable @ 0x{table_offset:X}:")
        print(f"  Entry 0: type={e0_type}, size={e0_size}, offset=0x{e0_offset:X}")
        print(f"  Entry 1: type={e1_type}, size={e1_size}, offset=0x{e1_offset:X}, name=\"{e1_name}\"")

        # 提取数据
        platform_data = data[e0_offset:e0_offset + e0_size]
        recovery_data = data[e1_offset:e1_offset + e1_size]

        do_swap(data, e0_offset, e0_size, e1_offset, e1_size,
                output_path, target_size, page_size, table_offset,
                platform_data, recovery_data)


def calculate_lz4_size(data, start):
    """计算 LZ4 legacy 流的总大小"""
    offset = start + 4  # skip magic
    total = 4
    while offset < len(data) - 4:
        bsize = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        total += 4
        if bsize == 0:
            break
        actual_size = bsize & 0x7FFFFFFF
        offset += actual_size
        total += actual_size
    return total


def do_swap(data, e0_offset, e0_size, e1_offset, e1_size,
            output_path, target_size, page_size, table_offset,
            platform_data=None, recovery_data=None):
    """执行交换并写出"""

    if platform_data is None:
        platform_data = data[e0_offset:e0_offset + e0_size]
    if recovery_data is None:
        recovery_data = data[e1_offset:e1_offset + e1_size]

    print(f"\n原始 platform ramdisk: offset=0x{e0_offset:X}, size={e0_size} ({e0_size/1024/1024:.1f} MB)")
    print(f"原始 recovery ramdisk: offset=0x{e1_offset:X}, size={e1_size} ({e1_size/1024/1024:.1f} MB)")

    # 创建空 CPIO 并压缩
    empty_cpio = create_empty_cpio()
    empty_cpio_lz4 = lz4_legacy_compress(empty_cpio)
    print(f"空 CPIO: {len(empty_cpio)} bytes -> LZ4: {len(empty_cpio_lz4)} bytes")

    # 查找 DTB
    dtb_offset = -1
    dtb_size = 0
    pos = 0
    while pos < len(data) - 4:
        idx = data.find(FDT_MAGIC, pos)
        if idx == -1:
            break
        if idx + 8 <= len(data):
            dsize = struct.unpack('>I', data[idx+4:idx+8])[0]
            if 100000 < dsize < 2000000:
                dtb_offset = idx
                dtb_size = dsize
                print(f"找到 DTB @ 0x{idx:X}, size={dsize}")
                break
        pos = idx + 1

    if dtb_offset < 0:
        print("警告: 找不到 DTB，将不包含 DTB")
        dtb_data = b''
    else:
        dtb_data = data[dtb_offset:dtb_offset + dtb_size]

    # 计算新布局
    new_platform_offset = page_size
    new_platform_size = len(recovery_data)  # 原 recovery 作为新 platform
    new_platform_pages = (new_platform_size + page_size - 1) // page_size

    new_recovery_offset = new_platform_offset + new_platform_pages * page_size
    new_recovery_size = len(empty_cpio_lz4)
    new_recovery_pages = (new_recovery_size + page_size - 1) // page_size

    new_table_offset = new_recovery_offset + new_recovery_pages * page_size

    new_dtb_offset = new_table_offset + page_size

    print(f"\n新布局:")
    print(f"  header:   0x0 - 0x{page_size:X}")
    print(f"  platform: 0x{new_platform_offset:X} ({new_platform_size} bytes)")
    print(f"  recovery: 0x{new_recovery_offset:X} ({new_recovery_size} bytes)")
    print(f"  table:    0x{new_table_offset:X}")
    print(f"  dtb:      0x{new_dtb_offset:X} ({dtb_size} bytes)")

    # 构建结果
    total_needed = new_dtb_offset + align_up(dtb_size, page_size)
    if target_size:
        result_size = max(target_size, total_needed)
    else:
        result_size = max(len(data), total_needed)

    result = bytearray(result_size)

    # 复制 header
    result[:page_size] = data[:page_size]

    # 写入 platform ramdisk (原 recovery)
    result[new_platform_offset:new_platform_offset + new_platform_size] = recovery_data

    # 写入 recovery ramdisk (空 CPIO)
    result[new_recovery_offset:new_recovery_offset + new_recovery_size] = empty_cpio_lz4

    # 写入 ramdisk table
    entry0 = bytearray(32)
    struct.pack_into('<I', entry0, 0, 0)  # type = platform
    struct.pack_into('<I', entry0, 4, 0)  # name_idx = 0
    struct.pack_into('<Q', entry0, 8, new_platform_size)
    struct.pack_into('<Q', entry0, 16, new_platform_offset)

    entry1 = bytearray(32)
    struct.pack_into('<I', entry1, 0, 1)  # type = recovery
    struct.pack_into('<I', entry1, 4, 0)  # name_idx = 0
    struct.pack_into('<Q', entry1, 8, new_recovery_size)
    struct.pack_into('<Q', entry1, 16, new_recovery_offset)
    name_bytes = b'recovery\x00'
    entry1[24:24 + len(name_bytes)] = name_bytes

    result[new_table_offset:new_table_offset + 32] = entry0
    result[new_table_offset + 32:new_table_offset + 64] = entry1

    # 写入 DTB
    if dtb_data:
        result[new_dtb_offset:new_dtb_offset + dtb_size] = dtb_data

    # 更新 header 中的 table_offset
    if table_offset >= 0:
        old_table_offset_bytes = struct.pack('<I', table_offset)
        new_table_offset_bytes = struct.pack('<I', new_table_offset)
        for off in range(8, page_size - 3):
            if data[off:off+4] == old_table_offset_bytes:
                result[off:off+4] = new_table_offset_bytes
                print(f"  替换 table_offset: header offset {off}: {table_offset} -> {new_table_offset}")
                break
        else:
            # 尝试标准 v4 位置 (offset 24)
            result[24:28] = new_table_offset_bytes
            print(f"  写入 table_offset 到标准位置 (offset 24): {new_table_offset}")

    # 填充到目标大小
    if target_size and len(result) > target_size:
        result = result[:target_size]
    elif target_size and len(result) < target_size:
        result += b'\xFF' * (target_size - len(result))

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

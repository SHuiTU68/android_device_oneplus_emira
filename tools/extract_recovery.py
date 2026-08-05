#!/usr/bin/env python3
"""
extract_recovery.py — 从 vendor_boot.img 中健壮提取 recovery (TWRP/OrangeFox) ramdisk cpio

背景: 一加 Ace5 Ultra (MTK) 的 OrangeFox CI 产物中, platform 流可能是 lz4 frame 格式
(0x184D2204), recovery 流可能是 lz4 legacy 格式 (0x184C2102), 且 dtb 前可能混有
"假 lz4 magic" (8B 空流等)。旧逻辑"搜索最后一个 legacy magic"会命中假流而失败。

本脚本策略 (对任何布局都健壮):
  1. 全文件扫描所有"合法" lz4 流 (legacy + frame, 均做格式/边界校验)
  2. 对每个候选流, 用命令行 lz4 解压 (legacy 用 -l, frame 不用)
  3. 校验解压结果是 cpio (magic 070701), 并按 TWRP/OrangeFox 特征评分
  4. 输出评分最高的流作为 recovery cpio

用法: python3 extract_recovery.py <vendor_boot.img> <输出.cpio>
"""

import struct
import subprocess
import sys
import os

LEGACY_MAGIC = b'\x02\x21\x4c\x18'   # 0x184C2102
FRAME_MAGIC = b'\x04\x22\x4d\x18'    # 0x184D2204

TWRP_MARKERS = [
    b'twres', b'twrp', b'OrangeFox', b'tw_', b'sbin/recovery',
    b'prebuilt_file_contexts', b'ueventd.recovery', b'etc/tw_',
]


def try_legacy(d, p):
    """解析 legacy lz4 流边界, 非法返回 None。支持无 end marker(直接拼接下一流magic)场景。"""
    q = p + 4
    while q + 4 <= len(d):
        # 下一流 magic 处截断 (原厂流1 无 end marker 场景)
        if q > p + 4 and d[q:q + 4] in (LEGACY_MAGIC, FRAME_MAGIC):
            return q
        sz = struct.unpack_from('<I', d, q)[0]
        if sz == 0:
            return q + 4  # end marker
        raw = bool(sz & 0x80000000)
        sz &= 0x7FFFFFFF
        if sz & 0x40000000:
            sz &= 0xFFFF
        if sz == 0 or q + 4 + sz > len(d):
            return None  # 非法块 -> 不是合法流
        q += 4 + sz
    return None


def try_frame(d, p):
    """用 lz4.frame 精确定位 lz4 frame 流边界, 非法返回 None。"""
    if d[p:p + 4] != FRAME_MAGIC:
        return None
    try:
        import lz4.frame
        info = lz4.frame.get_frame_info(d[p:p + 19])
        if info.get('skippable'):
            return None
        dec = lz4.frame.LZ4FrameDecompressor()
        pos = p
        while True:
            seg = d[pos:pos + 262144]
            if not seg:
                return None
            out = dec.decompress(seg)
            unused = dec.unused_data or b''
            used = len(seg) - len(unused)
            pos += used
            if dec.eof:
                return pos
            if used == 0:
                return None
    except Exception:
        return None


def scan_streams(d):
    """扫描所有合法 lz4 流, 返回 [(fmt, start, end), ...] 按位置排序 (find 加速)"""
    cands = []
    p = 0
    while p < len(d) - 8:
        i1 = d.find(LEGACY_MAGIC, p)
        i2 = d.find(FRAME_MAGIC, p)
        if i1 < 0 and i2 < 0:
            break
        if i1 < 0:
            nxt = i2
        elif i2 < 0:
            nxt = i1
        else:
            nxt = min(i1, i2)
        if d[nxt:nxt + 4] == LEGACY_MAGIC:
            end = try_legacy(d, nxt)
            if end and end - nxt >= 8:
                cands.append(('legacy', nxt, end))
                p = end
            else:
                p = nxt + 4
        else:
            end = try_frame(d, nxt)
            if end and end - nxt >= 12:
                cands.append(('frame', nxt, end))
                p = end
            else:
                p = nxt + 4
    return cands


def decompress(d, fmt, s, e):
    """解压候选流, 返回 (rc, 输出字节)"""
    tmp_in = '/tmp/_ext_rec.lz4'
    tmp_out = '/tmp/_ext_rec.out'
    for f in (tmp_in, tmp_out):
        if os.path.exists(f):
            os.remove(f)
    with open(tmp_in, 'wb') as f:
        f.write(d[s:e])
    if fmt == 'legacy':
        r = subprocess.run(['lz4', '-d', '-l', '-f', tmp_in, tmp_out],
                           capture_output=True, text=True)
    else:
        r = subprocess.run(['lz4', '-d', '-f', tmp_in, tmp_out],
                           capture_output=True, text=True)
    if os.path.exists(tmp_out):
        data = open(tmp_out, 'rb').read()
    else:
        data = b''
    return r.returncode, data


def is_valid_cpio(data):
    """宽松校验 cpio newc: magic + 至少2个合法 entry 且无坏 magic。
    (允许无 TRAILER!!! 的截断 cpio, 如 TWRP 11.5MB; 排除假流误判)"""
    if len(data) < 512 or data[:6] != b'070701':
        return False
    p = 0
    entries = 0
    try:
        while True:
            if p + 110 > len(data):
                return entries >= 2
            if data[p:p + 6] != b'070701':
                # 尾部可能有零填充
                if entries >= 2 and set(data[p:]) <= {0}:
                    return True
                return False
            hdr = data[p:p + 110]
            try:
                namesize = int(hdr[94:102], 16)
                filesize = int(hdr[54:62], 16)
            except ValueError:
                return False
            if namesize <= 0 or namesize > 4096:
                return False
            if p + 110 + namesize > len(data):
                return entries >= 2
            name = data[p + 110:p + 110 + namesize]
            if b'TRAILER!!!' in name:
                return True
            dpos = (p + 110 + namesize + 3) & ~3
            dpos += (filesize + 3) & ~3
            p = dpos
            entries += 1
    except Exception:
        return False


def score(data):
    """TWRP/OrangeFox 特征评分"""
    s = 0
    low = data.lower()
    for m in TWRP_MARKERS:
        if m.lower() in low:
            s += 1
    return s


def main():
    if len(sys.argv) < 3:
        print('用法: python3 extract_recovery.py <vendor_boot.img> <输出.cpio>')
        return 2
    vb_path, out_path = sys.argv[1], sys.argv[2]
    d = open(vb_path, 'rb').read()
    cands = scan_streams(d)
    print(f'扫描到 {len(cands)} 个合法 lz4 流候选:')
    results = []
    for i, (fmt, s, e) in enumerate(cands):
        print(f'  [{i}] {fmt} @0x{s:x} -> 0x{e:x} ({e - s} B)')
        rc, data = decompress(d, fmt, s, e)
        is_cpio = is_valid_cpio(data)
        sc = score(data) if is_cpio else -1
        print(f'      解压rc={rc} out={len(data)}B cpio={is_cpio} score={sc}')
        if is_cpio:
            results.append((sc, len(data), fmt, s, e, data))
    if not results:
        print('错误: 未找到任何有效 cpio ramdisk 流')
        return 1
    # mkbootimg v4 标准布局: 流1=PLATFORM, 流2=RECOVERY, (流3=DLKM)
    # 因此优先取【位置排序后的第2个 cpio 流】作为 recovery;
    # 若只有 1 个 cpio 流, 则退回 TWRP 特征评分最高的那个。
    results.sort(key=lambda x: x[3])  # 按位置排序
    if len(results) >= 2:
        chosen = results[1]
        print(f'\n标准布局: 流1=platform({results[0][2]}@0x{results[0][3]:x}), 取流2 作为 recovery')
    else:
        sc, sz, fmt, s, e, data = results[0]
        print(f'\n警告: 仅发现 1 个 cpio 流 (score={sc}), 按特征评分选它')
        chosen = results[0]
    sc, sz, fmt, s, e, data = chosen
    print(f'选中: {fmt} @0x{s:x}->0x{e:x} ({e - s}B) 解压 {sz}B score={sc}')
    with open(out_path, 'wb') as f:
        f.write(data)
    print(f'已写出 {out_path} ({sz} B)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

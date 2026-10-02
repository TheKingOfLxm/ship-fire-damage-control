# -*- coding: utf-8 -*-
"""读取 FDS 的 SMV 切片文件（Fortran unformatted binary）。

只取温度剖面，用途是回答一个问题：主机舱那个 6.5 MW 的火，
火源附近的羽流温度到底有多少。读得到就不用为了一个探头再跑 3.5 小时。

文件结构（每个块）：
    256B 记录头 -> 30B label + 80B units + 4B 值
  之后每帧：
    'time'          + 4B float
    'index bounds'  + 4B × 6 (i1,j1,k1,i2,j2,k2)
    三组 float 数组（i,j,k 顺序，C 风格展平）
所有长度都是 4 的倍数且不足 4 的用 0 补齐。
"""
import os
import re
import struct
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")


def read_blocks(path):
    """逐条读取 SMV 记录，返回 (标签, 负载)。

    FDS 6.x 写出的 SMV 没有单位前缀：标签固定占满 30 字节，其余全是数据。
    记录结构 = 4 字节记录长 + 负载 + 4 字节记录长。
    帧内顺序是 index bounds -> time -> 数据数组。
    边界是 I1,I2,J1,J2,K1,K2（不是 I1,J1,K1,I2,J2,K2），
    1x63x33 这种能对上就说明是这个顺序。
    """
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        while f.tell() < size:
            hdr = f.read(4)
            if len(hdr) < 4:
                break
            n = struct.unpack("<i", hdr)[0]
            if n <= 0 or n > (1 << 24):
                break
            payload = f.read(n)
            if len(payload) < n:
                break
            f.read(4)
            if n == 30:
                yield payload[:30].decode("ascii", "replace").strip(), payload
            else:
                yield None, payload


def read_sm_temperature(path):
    """返回 {时刻: 温度数组}，数组形状 (I, J, K) 已还原。"""
    out = {}
    cur_t = None
    bounds = None
    for lab, payload in read_blocks(path):
        if lab is not None:
            continue
        if len(payload) == 24:
            i1, i2, j1, j2, k1, k2 = struct.unpack("<6i", payload)
            bounds = (i1, i2, j1, j2, k1, k2)
        elif len(payload) == 4:
            cur_t = struct.unpack("<f", payload)[0]
        elif bounds and len(payload) % 4 == 0 and len(payload) > 4:
            i1, i2, j1, j2, k1, k2 = bounds
            nx, ny, nz = i2 - i1 + 1, j2 - j1 + 1, k2 - k1 + 1
            if nx * ny * nz * 4 != len(payload):
                bounds = None
                continue
            a = np.frombuffer(payload, dtype="<f4").reshape((nx, ny, nz))
            if cur_t is not None:
                out[cur_t] = a
    return out


def main():
    path = sys.argv[1]
    prof = read_sm_temperature(path)
    if not prof:
        print("没读到温度数据")
        return
    times = sorted(prof)
    print(f"{os.path.basename(path)}: {len(times)} 帧")
    i1 = prof[times[0]].shape
    print(f"网格 {i1}")
    print()
    print(" 时刻    最大温度   最高点位置(i,j,k)")
    for t in times[::max(1, len(times) // 18)]:
        a = prof[t]
        idx = int(np.argmax(a))
        i, j, k = np.unravel_index(idx, a.shape)
        print("  %5.0fs  %8.1f   (%d,%d,%d)" % (t, a.max(), i, j, k))
    t = times[-1]
    a = prof[t]
    print()
    print(f"末帧 t={t:.0f}s 竖直剖面 (i 为最热列, 沿 k 向上):")
    i = int(np.unravel_index(int(np.argmax(a)), a.shape)[0])
    for k in range(a.shape[2]):
        col = a[i, :, k]
        if col.max() > 25 or k % 5 == 0:
            print("   z-index %2d  max %7.1f" % (k, col.max()))


if __name__ == "__main__":
    main()

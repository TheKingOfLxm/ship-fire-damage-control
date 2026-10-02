"""
把 PyroSim 导出的 .fds 工况改写成适合训练长时程火灾演化的版本。

要解决两个已确认的问题：

1. **时域太短**：原始工况写死 `&TIME T_END=20.0`，训练数据自然只有 30 秒。
   这里把 T_END 提到指定时长，覆盖 起火 → 增长 → 充分发展 → 衰减。

2. **温度通道无信号**：原工况的热电偶离火源约 3 米，20 秒内根本没被加热，
   于是温度全程恒为 20℃。这里把热电偶和气体探头重新布置到火源附近 ——
   热电偶在火焰上方约 0.6 m，CO/CO2 在人员呼吸高度约 1.6 m。

用法：
    py -3 tools/prep_fds_case.py <输入.fds> <输出.fds> <T_END秒> [CHID]

传感器布置遵循 FDS 舱室火灾的常规做法，不是随意取值。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 探头相对火源中心的偏移
# 热电偶：火焰正上方 0.6 m，测羽流温度 —— 必须近，否则 20 秒内根本读不到升温
# 气体探头：地板上方 1.6 m（人员呼吸高度），水平偏离火源 2.5 m
#   不能放在火源正上方：那里在羽流核心，CO 会被进一步烧成 CO2，CO 读数恒为 0。
TC_OFFSET = (0.0, 0.0, 0.6)
GAS_HEIGHT = 1.6          # 距地板：人员呼吸高度
GAS_STANDOFF = 2.5        # 水平离火源距离
SMOKE_LAYER = 0.85        # 天花板下方 85% 高度：烟气最先在此聚集


def parse_floats(s):
    return [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', s)]


def find_fire_center(text):
    """从工况里定位火源中心。

    优先取带 HRRPUA 的 SURF 所对应的 VENT 的中心点 —— 那才是真正在放热的面。
    找不到就退回第一个非 OPEN 的 VENT。
    """
    # 1) 找定义了 HRRPUA 的 SURF id
    burner_ids = set()
    for m in re.finditer(r"&SURF\b(.*?)(?:/|$)", text, re.S | re.I):
        body = m.group(1)
        if re.search(r"HRRPUA\s*=", body, re.I):
            sid = re.search(r"ID\s*=\s*'([^']+)'", body)
            if sid:
                burner_ids.add(sid.group(1))

    best = None
    for m in re.finditer(r"&VENT\b(.*?)(?:/\s*\n|/|$)", text, re.S | re.I):
        body = m.group(1)
        if re.search(r"SURF_ID\s*=\s*'OPEN'", body, re.I):
            continue
        xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body)
        if not xb:
            continue
        v = parse_floats(xb.group(1))
        if len(v) < 6:
            continue
        sid = re.search(r"SURF_IDS?\s*=\s*'([^']+)'", body)
        is_burner = sid and sid.group(1) in burner_ids
        c = ((v[0] + v[1]) / 2, (v[2] + v[3]) / 2, (v[4] + v[5]) / 2)
        if is_burner and best is None:
            return c, (v, sid.group(1) if sid else None, True)
        if best is None:
            best = (c, (v, sid.group(1) if sid else None, False))
    if best:
        return best
    return None, None


def build_devices(center, sid, room, ceiling):
    cx, cy, cz = center
    tc = (cx + TC_OFFSET[0], cy + TC_OFFSET[1], cz + TC_OFFSET[2])
    # 气体探头：房间内、水平离火源 GAS_STANDOFF，两个高度各放一个 ——
    #   1.6m 人员呼吸高度：反映人员实际暴露
    #   天花板下烟气层：烟气最先在这里聚集，起火早期这里就有信号
    # 只放呼吸高度的话，起火初期烟气层还在天花板，采样点会一直是 0。
    fx, fy = room
    sx = -1.0 if cx > (fx[0] + fx[1]) / 2 else 1.0
    sy = -1.0 if cy > (fy[0] + fy[1]) / 2 else 1.0
    gx = cx + sx * GAS_STANDOFF
    gy = cy + sy * GAS_STANDOFF
    gx = min(max(gx, fx[0] + 0.5), fx[1] - 0.5)
    gy = min(max(gy, fy[0] + 0.5), fy[1] - 0.5)
    low = (gx, gy, GAS_HEIGHT)
    high = (gx, gy, ceiling * SMOKE_LAYER)

    lines = []
    lines.append("! ---- 探头布置：热电偶在火焰上方 %.1f m；气体探头水平离火源 %.1f m，"
                 "分别在呼吸高度 %.1f m 与烟气层 %.2f m ----"
                 % (TC_OFFSET[2], GAS_STANDOFF, GAS_HEIGHT, ceiling * SMOKE_LAYER))
    lines.append("&DEVC ID='TC', QUANTITY='TEMPERATURE', XYZ=%.4f,%.4f,%.4f/" % tc)
    # 物种 ID 必须用 FDS 的全名：'CARBON MONOXIDE' / 'CARBON DIOXIDE'。
    # 写 'CO' / 'CO2' 读不到任何值 —— FDS 里没有这两个短名，
    # 而 PyroSim 导出的工况一律用全名。
    # 气体浓度用 VOLUME FRACTION：FDS6 的 DEVC 没有 CONCENTRATION 这个量。
    for tag, pos in (("LO", low), ("HI", high)):
        lines.append(f"&DEVC ID='CO{tag}', QUANTITY='VOLUME FRACTION', "
                     f"SPEC_ID='CARBON MONOXIDE', XYZ=%.4f,%.4f,%.4f/" % pos)
        lines.append(f"&DEVC ID='CO2{tag}', QUANTITY='VOLUME FRACTION', "
                     f"SPEC_ID='CARBON DIOXIDE', XYZ=%.4f,%.4f,%.4f/" % pos)
    # 释热率密度，用来判断火灾是否进入充分发展阶段
    lines.append("&DEVC ID='HRRPUV', QUANTITY='HRRPUV', XYZ=%.4f,%.4f,%.4f/" % tc)
    return lines, tc, low, high


def find_ceiling(text, room):
    """从 OBST 里推断舱室净高（取贴顶的薄板），用于把气体探头放到烟气层。"""
    zs = []
    for m in re.finditer(r"&OBST\b(.*?)(?:/\s*\n|/|$)", text, re.S | re.I):
        body = m.group(1)
        xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body)
        if not xb:
            continue
        v = parse_floats(xb.group(1))
        if len(v) < 6:
            continue
        top = max(v[4], v[5])
        if 1.0 < top < 12.0:
            zs.append(top)
    if zs:
        return max(zs)
    return 3.0


def find_room(text):
    """从 OBST 里推断舱室平面范围（地面那一层），用于给气体探头找合理位置。"""
    xs, ys = [], []
    for m in re.finditer(r"&OBST\b(.*?)(?:/\s*\n|/|$)", text, re.S | re.I):
        body = m.group(1)
        xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body)
        if not xb:
            continue
        v = parse_floats(xb.group(1))
        if len(v) < 6:
            continue
        # 贴地的薄板（厚度 < 0.3 m）当作地板
        if v[5] - v[4] < 0.3 and v[4] < 0.5:
            xs += [v[0], v[1]]
            ys += [v[2], v[3]]
    if xs and ys:
        return ((min(xs), max(xs)), (min(ys), max(ys)))
    return ((0.0, 10.0), (0.0, 8.0))


def rewrite(text, t_end, chid, out_path):
    center, info = find_fire_center(text)
    if center is None:
        raise SystemExit("✗ 没能在工况里定位火源（找不到带 HRRPUA 的 VENT）")

    xb, sid, is_burner = info
    print(f"火源中心 = ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})"
          f"   SURF='{sid}'  {'(带 HRRPUA)' if is_burner else '(未确认带 HRRPUA)'}")

    # 1) 延长仿真时长
    text, n_time = re.subn(r"(&TIME\b[^/]*?)\s*T_END\s*=\s*[\d.]+",
                           lambda m: m.group(1).rstrip() + f" T_END={t_end}", text,
                           count=1, flags=re.S | re.I)
    if n_time == 0:
        text = text.replace("&HEAD", f"&HEAD", 1)
        text = re.sub(r"(&HEAD\b[^/]*/)", r"\1\n&TIME T_END=%s/" % t_end, text, count=1, flags=re.S)
    print(f"T_END -> {t_end}s   ({'已替换' if n_time else '新建 TIME'})")

    # 2) ASCII CHID —— 含中文会导致 FDS 写 .smv 文件名失败
    text, n_chid = re.subn(r"CHID\s*=\s*'[^']*'",
                           f"CHID='{chid}'", text, count=1, flags=re.I)

    # 3) 删除原有 DEVC，换成重新布置的探头
    #    原工况的 Device(V-VELOCITY) 对训练无用，保留会让列名对不上。
    text = re.sub(r"^&DEVC\b.*?/\s*$", "", text, flags=re.M | re.S)

    room = find_room(text)
    ceiling = find_ceiling(text, room)
    dev_lines, tc, low, high = build_devices(center, sid, room, ceiling)
    print(f"热电偶 -> ({tc[0]:.2f}, {tc[1]:.2f}, {tc[2]:.2f})")
    print(f"气体探头 呼吸高度 -> ({low[0]:.2f}, {low[1]:.2f}, {low[2]:.2f})")
    print(f"气体探头 烟气层   -> ({high[0]:.2f}, {high[1]:.2f}, {high[2]:.2f})  [净高 {ceiling:.2f}m]")

    # 4) 提高设备输出采样密度：长时程下默认间隔太粗
    text = re.sub(r"(&DUMP\b[^/]*?)\s*/",
                  lambda m: m.group(1).rstrip() + ", DT_DEVC=0.05 /", text,
                  count=1, flags=re.S | re.I)

    # 5) CO / CO2 用 FDS 内置全名物种，无需也不能再自定义 &SPEC：
    #    自定义 ID='CO' 会与内置的 'CARBON MONOXIDE' 并存却不参与反应产物，
    #    结果 DEVC 读到的永远是 0。FDS 对重复定义会直接报 ERROR(1004)。
    if not re.search(r"&SPEC\s+ID\s*=\s*'(CO|CO2|CARBON MONOXIDE|CARBON DIOXIDE)'", text, re.I):
        print("提示: 未发现 CO/CO2 物种定义，依赖 FDS 内置全名物种")
    else:
        # 工况里若已有自定义的 CO/CO2 定义，删掉，避免和内置全名冲突
        text = re.sub(r"^&SPEC\s+ID\s*=\s*'(CO|CO2)'\b.*?/\s*$", "", text,
                      flags=re.M | re.S | re.I)
        print("已移除与内置全名冲突的自定义 CO/CO2 物种")

    text = text.replace("&TAIL /", "\n".join(dev_lines) + "\n\n&TAIL /")
    # 去掉文件头里的中文注释行（FDS 读注释没问题，但 CHID 必须是 ASCII）
    with open(out_path, "w", encoding="ascii", errors="replace") as f:
        f.write(text)
    print(f"已写出 {out_path}")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        raise SystemExit(1)
    src, dst, t_end = sys.argv[1], sys.argv[2], float(sys.argv[3])
    chid = sys.argv[4] if len(sys.argv) > 4 else os.path.splitext(os.path.basename(dst))[0]
    with open(src, encoding="utf-8", errors="replace") as f:
        text = f.read()
    rewrite(text, t_end, chid, dst)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""在**已修正**的算例上生成多工况轨迹。

======================================================================
和当初 gen_conditions.py 的区别
======================================================================
当初的多工况是在有缺陷的算例上做的变体：没有释热斜坡、探针在羽流核心、
火源被遮挡。结果 5 组工况测出来的响应跨度反而比单轨迹更小（78℃ vs 97℃），
于是整条路线被否决。

那个结论建立在「数据是阶跃」这个前提上。现在前提变了：
  - 释热按 IMO FTP t^2 斜坡增长，有真实的起火→增长→充分发展→衰减
  - 探针测的是舱室上层烟气层均温
  - 规定的释热量用面积分测法验证过，与规格一致
  - 舱室是封闭的、只留门和受限排气，能形成烟气层

所以多工况重新变得有意义：它解决的是**单条轨迹无法覆盖所有火灾状态**
的问题 —— 一个舱只有一条轨迹时，时间序切分出来的验证段必然是训练段
没见过的物理状态（实测训练损失降而验证损失升）。
多工况让每个时间段都能见到不同的火势，模型才学得到「状态→演化」。

变的是什么：
  峰值释热率  控制是否闪燃、充分发展后的温度平台
  门/开口尺度 控制通风控制燃烧还是通风受限燃烧
  燃烧器位置 影响火焰形态与烟气层的形成路径
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# (标签, 峰值倍数, 门宽倍数)
# 峰值跨 0.35~2.0 倍，覆盖从通风受限小火到闪燃充分发展；
# 门宽跨 0.5~2.0 倍，覆盖欠通风到过通风。
CONDITIONS = [
    ("q035_v100", 0.35, 1.00),
    ("q070_v100", 0.70, 1.00),
    ("q100_v050", 1.00, 0.50),
    ("q100_v100", 1.00, 1.00),
    ("q100_v200", 1.00, 2.00),
    ("q150_v100", 1.50, 1.00),
    ("q200_v150", 2.00, 1.50),
]


def scale_hrr(text, factor):
    """把 FIRE 表面的 HRRPUA 按倍数缩放（只动释热，不动其它）。"""
    def repl(m):
        body = m.group(0)
        v = re.search(r"HRRPUA\s*=\s*([\d.]+)", body)
        if not v:
            return body
        return body.replace(v.group(0), f"HRRPUA={float(v.group(1)) * factor:.1f}")
    return re.sub(r"&SURF\s+ID\s*=\s*'FIRE'[^/]*/", repl, text, count=1)


def scale_vent(text, factor, span_limit):
    """把唯一的开门 VENT 按倍数缩放（保持高度，宽度变）。

    门是唯一的通风路径，宽度直接决定通风控制程度。
    只处理 y 向的竖直面上的那个 OPEN —— 封闭舱室里其它面都是 INERT。

    span_limit 是判断「这是门还是整个外侧面」的门槛：电站间被认到的
    OPEN 宽 16 m，那是整个网格外侧面而不是门，按倍数放大后会超出
    域范围，FDS 直接报错。这种情况要当作「没有门」处理。
    """
    hits = []

    def repl(m):
        body = m.group(0)
        if "SURF_ID='OPEN'" not in body:
            return body
        xb = re.search(r"XB\s*=\s*([-\d.,eE\s]+)", body)
        if not xb:
            return body
        v = [float(x) for x in re.findall(r'-?\d+\.?\d*', xb.group(1))]
        if len(v) < 6 or abs(v[2] - v[3]) > 1e-9:   # 只改 y 向的竖直面
            return body
        w = v[1] - v[0]
        if w > span_limit:                          # 整个外侧面，不是门
            return body
        nw = w * factor
        c = (v[0] + v[1]) / 2
        nv = [c - nw / 2, c + nw / 2, v[2], v[3], v[4], v[5]]
        # XB 的捕获类 [-\d.,eE\s]+ 里含逗号，会把 XB 后面的分隔逗号
        # 一起吃掉，替换时不补回去就生成 `2.0000SURF_ID='OPEN'`，
        # FDS 报 ERROR(101)。这里按原样补回尾逗号。
        tail = "," if xb.group(0).rstrip().endswith(",") else ""
        hits.append((w, nw))
        return body.replace(xb.group(0),
                            "XB=" + ",".join(f"{x:.4f}" for x in nv) + tail)
    out = re.sub(r"&VENT\b[^/]*/", repl, text)
    return out, hits


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    src = sys.argv[1]
    tag = sys.argv[2]
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(src)
    t_end = float(sys.argv[4]) if len(sys.argv) > 4 else 1800.0

    base = open(src, encoding="ascii", errors="replace").read()
    m = re.search(r"&SURF\s+ID\s*=\s*'FIRE'[^/]*?HRRPUA\s*=\s*([\d.]+)", base, re.S)
    if not m:
        raise SystemExit("✗ 基准算例里找不到 FIRE 表面的 HRRPUA")
    base_hrr = float(m.group(1))
    # 门宽上限：真实舱门在 0.8~2 m 量级。超过 3 m 的 OPEN 面基本可以
    # 断定是整个网格外侧面（电站间 16 m、灶炉间 7 m），不是门。
    span_limit = 3.0
    print(f"基准 {os.path.basename(src)}: HRRPUA={base_hrr:.1f} kW/m^2  "
          f"(门宽上限 {span_limit} m)")

    seen = {}
    for label, qf, vf in CONDITIONS:
        text = scale_hrr(base, qf)
        text, hits = scale_vent(text, vf, span_limit)
        if vf != 1.0 and not hits:
            # 门不是显式 OPEN VENT（灶炉间的门是内壁薄板之间的缝隙），
            # 或认到的 OPEN 是整个外侧面。缩放抓不到它，此时
            # q100_v050 / v100 / v200 会生成完全相同的文件，跑三遍纯浪费。
            print(f"  {label:<12} 跳过：无可缩放的门（内壁缝隙或整个外侧面）")
            continue
        # 按算例正文去重（忽略 CHID/T_END），避免跑重复算例
        body = re.sub(r"CHID\s*=\s*'[^']*'", "", text)
        body = re.sub(r"T_END\s*=\s*[\d.]+", "", body)
        h = hash(body)
        if h in seen:
            print(f"  {label:<12} 跳过：与 {seen[h]} 内容完全相同")
            continue
        seen[h] = label
        chid = f"{tag}_{label}".replace(".", "p")
        text = re.sub(r"CHID\s*=\s*'[^']*'", f"CHID='{chid}'", text, count=1)
        text = re.sub(r"T_END\s*=\s*[\d.]+", f"T_END={t_end:g}", text, count=1)
        dst = os.path.join(out_dir, f"{chid}.fds")
        open(dst, "w", encoding="ascii", errors="replace").write(text)
        door = ""
        if hits:
            w, nw = hits[0]
            door = f"   门宽 {w:.2f} -> {nw:.2f} m"
        print(f"  {label:<12} HRRPUA={base_hrr*qf:8.1f} 峰值约 "
              f"{base_hrr*qf*0.64:7.0f} kW{door}   -> {os.path.basename(dst)}")


if __name__ == "__main__":
    main()

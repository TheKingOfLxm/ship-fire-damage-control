# -*- coding: utf-8 -*-
"""生成**含灭火干预**的 FDS 训练算例（v7 干预数据）。

======================================================================
要解决的问题
======================================================================
v6 的训练数据全是"没人灭火"的火灾发展轨迹，模型原则上算不出
"投入灭火后火会怎么走"的反事实 —— 这正是损管三问里"这个舱还能扑吗"
缺失的那一半。本脚本在**已修正**的生产算例（IMO FTP t² 增长 + 上层
烟气层测点 + 显式围护）基础上，生成带灭火干预的变体。

======================================================================
物理编码方式（如实披露，不冒充水喷淋模拟）
======================================================================
灭火干预编码为**对火 ramp 乘一个包络**：

    HRR_eff(t) = HRR_base(t) * max(floor, exp(-(t - t_s) / tau))   (t >= t_s)
    HRR_eff(t) = HRR_base(t)                                        (t <  t_s)

  t_s    干预开始时刻（模拟船员/固定系统投入的时刻）
  tau    压制时间常数（灭火剂到位速度与强度的代理：强扑 60s，弱扑 300s）
  floor  残余释热下限（扑灭 0.02；扑晚了压不住 0.15）

这是消防工程里对"有效灭火"的标准简化（等效于 FDS &SURF 的
E_COEFFICIENT 指数衰减思想，但干预时刻由算例参数显式控制，而不是
耦合到喷淋探头温度）。FDS 仿真的是 HRR 降低的**后果**：层温回落、
CO/CO₂ 产率下降 —— 这正是模型要学的干预响应。它不是水滴蒸发冷却
的 CFD 模拟，这个边界会写进 README。

======================================================================
干预状态通道 S（模型的第 4 个输入）
======================================================================
模型输入从 (T, CO, CO₂) 扩为 (T, CO, CO₂, S)：

    S(t) = 0                          (t <  t_s)
    S(t) = 1 - exp(-(t - t_s) / 5)    (t >= t_s, 10 秒内到位)

S 是"操作员做了什么"（动作），温度/气体是"火怎么响应"（后果）。
因果方向干净：推理时后台把当前灭火状态填进 S，模型输出扑救后的
演化 —— 反事实由此成立。S 曲线由 manifest 里的 t_s 精确重构，
不依赖测量。

======================================================================
工况设计（每舱 3 个，t_s 对齐火灾阶段）
======================================================================
火 ramp：0~240s t² 增长期 → 240~1400s 平台 → 1400s 后燃尽衰减。

  s0180_t0060  增长期介入（HRR 约 56% 峰值），强扑 tau=60s，
               floor=0.02 —— 火被扑灭，层温快速回落
  s0300_t0150  充分发展初期介入，中效 tau=150s，floor=0.05 ——
               明显压制但拖尾长
  s0600_t0300  充分发展期才介入，弱效 tau=300s，floor=0.15 ——
               "扑晚了"：压不住，温度只降不灭

三档刻意拉开干预后果的跨度（扑灭 / 压制 / 无效），模型才能学到
"S 的值 → 响应强度"的映射，而不是单一"灭火=降温"。

用法:
    py -3 tools/gen_suppression.py            # 全部舱室
    py -3 tools/gen_suppression.py 灶炉间      # 单个舱室
    py -3 tools/gen_suppression.py --dry      # 只打印不写文件

产物: tools/fds/suppress/<tag>_s<tttt>_t<tttt>.fds + manifest.json
跑法: py -3 tools/fds_queue.py suppress
"""
import json
import math
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
GROWTH = os.path.join(HERE, "fds", "growth")
OUT_DIR = os.path.join(HERE, "fds", "suppress")
MANIFEST = os.path.join(OUT_DIR, "manifest.json")

# 与 retrain_v6/v7 的 COMPARTMENTS 保持一致：舱室 -> (基准 CHID, 条件前缀)
COMPARTMENTS = {
    "灶炉间": ("d5", "d5"),
    "机库": ("d6", "d6"),
    "士兵住舱": ("d8m", "d8"),
    "电站间": ("d4b", "d4"),
    "主机舱": ("zjc", "zjc"),
}

# (标签, t_s, tau, floor, 说明)
VARIANTS = [
    ("s0180_t0060", 180.0, 60.0, 0.02, "增长期介入·强扑（扑灭）"),
    ("s0300_t0150", 300.0, 150.0, 0.05, "充分发展初期·中效（压制）"),
    ("s0600_t0300", 600.0, 300.0, 0.15, "充分发展期·弱效（扑晚了）"),
]

# 包络在 t_s 之后按 tau/3 间隔打结点到 3*tau，之后每 60s 一个，
# 保证 FDS 分段线性插值跟得上指数衰减（结点太稀会把指数折成台阶）
def envelope_knots(ts, tau, t_end):
    knots = {ts}
    t = ts
    while t < ts + 3 * tau and t < t_end:
        t += tau / 3.0
        knots.add(round(t, 2))
    while t < t_end:
        t += 60.0
        knots.add(round(t, 2))
    return sorted(k for k in knots if k <= t_end)


def find_fire_ramp(text):
    """定位 FIRE 表面引用的 RAMP_Q 表：返回 (ramp_id, [(t, f), ...])。"""
    surf = re.search(r"&SURF\s+ID\s*=\s*'FIRE'[^/]*/", text)
    if not surf:
        raise SystemExit("✗ 找不到 &SURF ID='FIRE'")
    m = re.search(r"RAMP_Q\s*=\s*'([^']+)'", surf.group(0))
    if not m:
        raise SystemExit("✗ FIRE 表面没有 RAMP_Q（不是生产版算例？）")
    rid = m.group(1)
    pts = []
    for mm in re.finditer(
            rf"&RAMP\s+ID\s*=\s*'{re.escape(rid)}'\s*,\s*T\s*=\s*([-\d.eE+]+)\s*,\s*F\s*=\s*([-\d.eE+]+)", text):
        pts.append((float(mm.group(1)), float(mm.group(2))))
    if not pts:
        raise SystemExit(f"✗ 找不到 RAMP ID='{rid}' 的数据点")
    return rid, sorted(pts)


def interp_ramp(pts, t):
    """分段线性插值 ramp（与 FDS 的 RAMP 语义一致）。"""
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, f0), (t1, f1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return f0 if t1 == t0 else f0 + (f1 - f0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def build_suppressed_ramp(pts, ts, tau, floor, t_end):
    """原 ramp × 灭火包络 → 新结点表。

    结点 = 原结点 ∪ 包络拐点。原增长段（t < t_s）不受影响；
    t >= t_s 的每个结点 F 值乘 max(floor, exp(-(t-ts)/tau))。
    """
    times = {p[0] for p in pts}
    times.update(envelope_knots(ts, tau, t_end))
    times.add(0.0)
    out = []
    for t in sorted(times):
        f = interp_ramp(pts, t)
        if t >= ts:
            f *= max(floor, math.exp(-(t - ts) / tau))
        out.append((t, f))
    return out


def main():
    dry = "--dry" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    targets = args or list(COMPARTMENTS)

    manifest = {}
    if os.path.exists(MANIFEST):
        manifest = json.load(open(MANIFEST, encoding="utf-8"))

    t_end = 1800.0
    for comp in targets:
        if comp not in COMPARTMENTS:
            raise SystemExit(f"✗ 未知舱室 {comp}，可选 {list(COMPARTMENTS)}")
        base_chid, tag = COMPARTMENTS[comp]
        src = os.path.join(GROWTH, f"{base_chid}.fds")
        text = open(src, encoding="ascii", errors="replace").read()

        t_match = re.search(r"T_END\s*=\s*([\d.]+)", text)
        if t_match:
            t_end = float(t_match.group(1))

        rid, pts = find_fire_ramp(text)
        print(f"\n{comp}  基准 {base_chid}.fds  RAMP '{rid}'  "
              f"{len(pts)} 结点  峰值 F={max(f for _, f in pts):g}  T_END={t_end:g}s")

        for label, ts, tau, floor, note in VARIANTS:
            chid = f"{tag}_{label}"
            new_pts = build_suppressed_ramp(pts, ts, tau, floor, t_end)
            # 替换整段 RAMP 表：旧表的每一行都匹配模式，但新表只能插入
            # **一次** —— 直接 subn(replacement) 会把整张新表替换到每一行
            # 上（8 行旧表 → 8 份新表首尾拼接，T 回绕），FDS 报
            # ERROR(394) T must be monotonically increasing。
            pat = re.compile(rf"&RAMP\s+ID\s*=\s*'{re.escape(rid)}'[^/]*/\s*")
            table = "\n".join(
                f"&RAMP ID='{rid}', T={t:g}, F={max(f, 0):.5g} /" for t, f in new_pts)
            first = [True]

            def _repl(m):
                if first[0]:
                    first[0] = False
                    return table + "\n"
                return ""
            new_text, n = pat.subn(_repl, text)
            if n == 0:
                raise SystemExit(f"✗ {src} 里替换 RAMP 表失败")
            new_text = re.sub(r"CHID\s*=\s*'[^']*'", f"CHID='{chid}'", new_text, count=1)
            hrr0 = max(f for _, f in pts)
            peak_after = max(f for t, f in new_pts)
            print(f"  {label}  t_s={ts:g}s tau={tau:g}s floor={floor:g}  {note}")
            print(f"    峰值 F {hrr0:g} -> {peak_after:.5g}"
                  f"（t_s 时原 F={interp_ramp(pts, ts):.3g}）")
            manifest[chid] = {
                "compartment": comp, "base": base_chid, "tag": tag,
                "t_start": ts, "tau": tau, "floor": floor, "note": note,
            }
            if not dry:
                os.makedirs(OUT_DIR, exist_ok=True)
                dst = os.path.join(OUT_DIR, f"{chid}.fds")
                open(dst, "w", encoding="ascii", errors="replace").write(new_text)
                print(f"    -> {os.path.relpath(dst, HERE)}")

    if not dry:
        json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"\nmanifest: {os.path.relpath(MANIFEST, HERE)}（{len(manifest)} 条）")
        print(f"下一步: py -3 tools\\fds_queue.py suppress")


if __name__ == "__main__":
    main()

"""
为一个舱室生成多组不同工况的 FDS 输入文件。

为什么需要多工况：
    每个舱原本只有 **一条** 训练轨迹。用滑窗切出来的上万个样本，本质
    是同一条曲线的不同片段，模型学到的是"记住这条曲线"而不是"火灾规律"。
    证据：喂 200℃ 窗口进去，模型第一步就跳到 317℃ 起步，对起始条件完全不敏感
    ——而真实物理上，200℃ 的火和 400℃ 的火发展速率应该不同。

    补多工况后，模型必须同时拟合不同条件下的轨迹，才可能学出
    "条件 -> 发展过程" 的映射。

变化维度（按对火灾发展的影响排序）：
    1. HRRPUA 释热率     —— 决定火有多大，多工况收益最高
    2. 通风口开度         —— 舱室火灾的主导因素，决定供氧与烟气排出
    3. 起火位置           —— 影响火焰形态与蔓延路径

用法：
    py -3 tools/gen_conditions.py <源.fds> <算例前缀> <通风面积系数列表>
例：
    py -3 tools/gen_conditions.py ship_5_1800.fds ship_5_c 1.0,2.0,0.5
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HRRPPU_FACTORS = [0.5, 1.0, 2.0]     # 释热率倍数
# 通风倍数。实测 ×2 会在 232 秒时触发 FDS ERROR(374) 数值不稳定
# （大开口导致舱内超压），所以限制在温和区间。
VENT_FACTORS = [0.7, 1.5]


def vary_hrrpua(text, factor):
    """按倍数缩放所有 HRRPUA。"""
    def repl(m):
        try:
            v = float(m.group(1))
        except ValueError:
            return m.group(0)
        return f"HRRPUA={v * factor:.1f}"
    return re.sub(r"HRRPUA=([\d.]+)", repl, text)


def vary_vent(text, factor):
    """缩放通风口面积：把 VENT 的 XB 六个坐标朝各自中点收缩/扩张。

    做法：解析每条 VENT 的 XB，找中心点，把各坐标按 factor 朝中心靠拢
    （factor<1 缩小开口，factor>1 扩大）。只在 6 维盒式平面上操作，
    保留原有的平面朝向（哪两个坐标相同不动）。
    """
    def repl(m):
        nums = [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', m.group(1))]
        if len(nums) != 6:
            return m.group(0)
        center = [(nums[0] + nums[1]) / 2, (nums[2] + nums[3]) / 2, (nums[4] + nums[5]) / 2]
        new = []
        for axis in range(3):
            lo, hi = nums[axis * 2], nums[axis * 2 + 1]
            c = center[axis]
            if abs(hi - lo) < 1e-9:
                new += [lo, hi]            # 该轴退化 = 平面，保持不变
            else:
                half = (hi - lo) / 2 * factor
                new += [c - half, c + half]
        return "XB=" + ", ".join(f"{v:.4f}" for v in new)

    # 只改 SURF_ID='burner' 的 VENT（火源通风口），不动物理开口
    out = []
    for line in text.splitlines():
        if re.search(r"&VENT\b", line) and "burner" in line:
            line = re.sub(r"XB=([-\d.,eE\s]+)", repl, line, count=1)
        out.append(line)
    return "\n".join(out)


def move_fire(text, dz):
    """整体平移火源（燃烧面 VENT）的 z 坐标。"""
    out = []
    for line in text.splitlines():
        if re.search(r"&VENT\b", line) and "burner" in line:
            def repl(m):
                nums = [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', m.group(1))]
                if len(nums) == 6:
                    nums[4] += dz
                    nums[5] += dz
                return "XB=" + ", ".join(f"{v:.4f}" for v in nums)
            line = re.sub(r"XB=([-\d.,eE\s]+)", repl, line, count=1)
        out.append(line)
    return "\n".join(out)


def set_chid(text, chid):
    """改 CHID。

    两个必须遵守的约束：
    1. CHID 必须唯一 —— FDS 的所有输出文件都以 CHID 命名，
       多工况共用同一个 CHID 会互相覆盖，最后只剩一条轨迹。
    2. CHID 里不能有小数点 —— FDS 报 ERROR(108) "No periods allowed in CHID"，
       所以 'c5_h0.5' 这种名字是非法的，要转成 'c5_h05'。
    """
    safe = re.sub(r"[^0-9A-Za-z_]", "", chid.replace(".", ""))
    return re.sub(r"CHID\s*=\s*'[^']*'", f"CHID='{safe}'", text, count=1, flags=re.I)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    src, prefix = sys.argv[1], sys.argv[2]
    vent_factors = ([float(x) for x in sys.argv[3].split(",")]
                    if len(sys.argv) > 3 else VENT_FACTORS)

    with open(src, encoding="utf-8", errors="replace") as f:
        base = f.read()

    made = []

    def emit(name, text, desc):
        # CHID 必须唯一且不含小数点；文件名与 CHID 保持一致，
        # 否则后续按算例名去找 FDS 输出会找不到文件
        safe = re.sub(r"[^0-9A-Za-z_]", "", name.replace(".", ""))
        fname = safe + ".fds"
        with open(fname, "w", encoding="ascii", errors="replace") as f:
            f.write(set_chid(text, safe))
        made.append((fname, desc))

    # 维度 1：释热率
    for hf in HRRPPU_FACTORS:
        emit(f"{prefix}_h{hf:g}", vary_hrrpua(base, hf), f"HRRPUA x{hf:g}")
    # 维度 2：通风开度（在基准释热率下）
    for vf in vent_factors:
        if abs(vf - 1.0) < 1e-6:
            continue
        emit(f"{prefix}_v{vf:g}", vary_vent(base, vf), f"通风 x{vf:g}")

    print(f"生成 {len(made)} 组工况（CHID 唯一、无小数点，与文件名一致）:")
    for n, d in made:
        print(f"  {n:<16} {d}")


if __name__ == "__main__":
    main()

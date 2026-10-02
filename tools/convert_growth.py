# -*- coding: utf-8 -*-
"""
把带火灾发展过程的 FDS 探头数据转成 LSTM 训练格式。

和 convert_fds_30min.py 的区别：
  - 旧版读单个 TC（羽流核心）当温度 —— 那个信号 0.3 秒就饱和，
    温度曲线是个阶跃，没有增长段。
  - 这里读 TL1..TLn 上层烟气温度阵列求平均，等价于舱室上部均温，
    这才是损管关心的量。

温度到达时刻的报告是**验收指标**，不是装饰：
  只要 20℃ -> 400℃ 还在几秒内完成，就说明增长斜坡没起作用，
  这种数据不能拿去训练 —— 模型只会学出一个阶跃函数，
  然后在仿真里表现为「20℃ 一步跳到几百℃」。

输出列固定为 Time, temperature, CO, CO2，与 retrain.py / server.py 对齐。
"""
import os
import re
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
GROWTH_DIR = os.path.join(HERE, "fds", "growth")
OUT_DIR = os.path.join(HERE, "fds", "train30")

STEP = 0.5        # 与 prep_fds_growth.py 的 DT_DEVC 一致

# 舱名 -> FDS 算例 CHID（prep_fds_design.py 生成）
# 士兵住舱用 d8m：d8 那版 1000 kW 会在 390 秒发散崩溃，
# d8m 降到 400 kW（5 kW/m^3，非闪燃）才能跑完全程且仍有火灾发展。
CASES = {
    "灶炉间": "d5",
    "机库": "d6",
    "士兵住舱": "d8m",
    "电站间": "d4b",
    # 主机舱用 config/compartments.json 的权威舱段 1423 m^3（1 MW 油池火）。
    # 早先按 PyroSim 网格域 13.1 万 m^3 跑，火点不着（层温全程 20~28℃）。
    "主机舱": "zjc",
}

# 增长段验收：这些温度必须在「分钟级」到达，而不是几秒
MILESTONES = (50.0, 100.0, 200.0, 300.0)
# 只对大幅升温卡时间。舱壁和顶棚在起火后十几秒就到 50~100℃ 是正常的
# 初始受热；真正说明数据退化成阶跃的，是 200℃ 以上也在几秒内达到
# （旧数据 0.9 秒就冲到 415℃）。
FAST_SECONDS = 20.0
FAST_MILESTONE = 100.0
# 峰值温度下限。充分发展的舱室火灾上层烟气层应到 150℃ 以上；
# 低于这个数说明场景释热量相对舱容太小，火没发展起来。
MIN_PEAK_C = 150.0


def read_devc(path):
    """FDS 的 _devc.csv：第 1 行是**单位**行（s / C / mol/mol / kW/m3），
    第 2 行才是通道名（Time / TL1..TL9 / TC / ...）。
    跳过第 1 行让名称行升为表头；跳过第 2 行会把首个数据行当表头，
    通道名全变成 C / C.1 / mol/mol 之类。"""
    df = pd.read_csv(path, skiprows=[0])
    df.columns = [str(c).strip().strip('"') for c in df.columns]
    return df


def pick_temperature(df):
    """上层烟气温度阵列求平均；没有阵列才退回 TC。"""
    tl = sorted([c for c in df.columns if re.fullmatch(r"TL\d+", c)],
                key=lambda c: int(c[2:]))
    if tl:
        return df[tl].to_numpy(float).mean(axis=1), f"TL1..TL{len(tl)} 均值"
    if "TC" in df.columns:
        return df["TC"].to_numpy(float), "TC（单点，回退）"
    raise KeyError("找不到温度通道")


def pick_species(df, base):
    """呼吸高度 + 烟气层求平均，两者都是真实暴露量。"""
    cols = [c for c in (f"{base}LO", f"{base}HI") if c in df.columns]
    if not cols:
        raise KeyError(f"找不到 {base} 通道")
    return df[cols].to_numpy(float).mean(axis=1), "+".join(cols)


def resample(t, cols, step):
    n = int(np.floor((t[-1] - t[0]) / step)) + 1
    grid = t[0] + np.arange(n) * step
    out = np.column_stack([np.interp(grid, t, c) for c in cols])
    return grid, out


def convert(name, case):
    src = os.path.join(GROWTH_DIR, f"{case}_devc.csv")
    if not os.path.exists(src):
        print(f"{name:<8} 缺 {os.path.basename(src)}，跳过")
        return None
    df = read_devc(src)
    tcol = df.columns[0]
    t = df[tcol].to_numpy(float)
    if t[-1] < 1795:
        print(f"{name:<8} 未跑完 ({t[-1]:.0f}s)，跳过")
        return None

    T, tsrc = pick_temperature(df)
    co, cosrc = pick_species(df, "CO")
    co2, co2src = pick_species(df, "CO2")
    # 烟气层浓度单独留一份。训练通道用平均（呼吸高度+烟气层），
    # 但**验收必须看烟气层**——那才是人和烟气所在处，也是损管的危害量。
    # 用平均值会把士兵住舱 2.60% 的致命层浓度稀释成 1.42% 而误判。
    co2_layer = None
    for c in ("CO2HI", "CO2_HI"):
        if c in df.columns:
            co2_layer = df[c].to_numpy(float)
            break
    grid, out = resample(t, [T, co, co2], STEP)
    if co2_layer is not None:
        out = np.column_stack([out, np.interp(grid, t, co2_layer)])
    else:
        out = np.column_stack([out, out[:, 2]])

    os.makedirs(OUT_DIR, exist_ok=True)
    dst = os.path.join(OUT_DIR, f"{name}.csv")
    pd.DataFrame({
        "Time": grid,
        "temperature": out[:, 0],
        "CO": out[:, 1],
        "CO2": out[:, 2],
    }).to_csv(dst, index=False)

    Tser = out[:, 0]
    CO2_LAYER = out[:, 3]
    print(f"{name:<8} {len(grid):>5} 点 / {grid[-1]:.0f}s   温度源={tsrc} "
          f"({cosrc}+{co2src})")

    # ---- 燃烧面实际释热（手册 p.346 的面积分测法，唯一的可信口径）----
    if "Q_BURNER" in df.columns:
        q = df["Q_BURNER"].to_numpy(float)
        print(f"{'':<8} 燃烧面实际释热 {q.max():.0f} kW 峰值")
        if q.max() < 50:
            print(f"{'':<8} ❌ 释热量异常低，数据不可用")
            return False

    print(f"{'':<8} 温度 {Tser[0]:.0f} -> 峰 {Tser.max():.0f}℃   "
          f"CO峰 {out[:,1].max()*100:.2f}%  CO2均 {out[:,2].max()*100:.2f}%  "
          f"CO2烟气层 {CO2_LAYER.max()*100:.2f}%")

    # ---- 验收 1：火灾是否真的发展起来了 ----
    # 只查「增长是不是太快」不够。电站间 542 m^3 塞 1000 kW 只有
    # 1.8 kW/m^3，远低于闪燃判据，火根本没发展起来，峰值 38℃，
    # 却因为「没在几秒内冲高温」而被判为合格。
    #
    # 但也不能一律要求高温度。闪燃型火灾看层温，非闪燃型看毒气 ——
    # 士兵住舱 400 kW / 80 m^3 = 5 kW/m^3，闪燃不了，层温只有 73℃，
    # 可层内 CO_2 到了 2.6%，这个浓度本身就足以致人失去意识，
    # 对损管是完全有效的火灾发展。
    # 闪燃型看层温，非闪燃型看毒气。阈值按舱容分档：
    #   灶炉间 22.5 m^3 / 562 kW  (25 kW/m^3)  -> 闪燃，层温 252℃
    #   机库  252  m^3 / 1000 kW (4 kW/m^3)   -> 闪燃，层温 220℃
    #   士兵住舱 80 m^3 / 400 kW (5 kW/m^3)   -> 不闪燃，层温 73℃、CO2层 2.59%
    #   电站间 542 m^3 / 3000 kW (5.5 kW/m^3) -> 不闪燃，层温 60℃、CO2层 2.92%
    # 大空间本来就不会闪燃，但 2.9% 的烟气层 CO2 一样足以致人昏迷，
    # 对损管完全有效，所以不能拿小舱室的温度线去卡它。
    # 被这条线拦下的是 1000 kW 那版电站间：38℃ + CO2 1.54%，火没发展起来。
    peakT, peakCO2 = Tser.max(), CO2_LAYER.max() * 100
    hot = peakT >= MIN_PEAK_C
    toxic = peakT >= 50.0 and peakCO2 >= 2.0
    if not (hot or toxic):
        print(f"{'':<8} ❌ 峰温 {peakT:.0f}℃ / 烟气层CO2 {peakCO2:.2f}% 都不达标，"
              f"火灾未充分发展（场景释热量相对舱容太小）")
        return False
    kind = "闪燃型" if hot else "非闪燃-毒气型"

    # ---- 增长段验收 ----
    ok = True
    marks = []
    for m in MILESTONES:
        idx = np.where(Tser >= m)[0]
        if len(idx) == 0:
            marks.append(f"{m:.0f}C:未达到")
        else:
            marks.append(f"{m:.0f}C@{grid[idx[0]]:.0f}s")
            if m >= FAST_MILESTONE and grid[idx[0]] < FAST_SECONDS:
                ok = False
    print(f"{'':<8} " + "  ".join(marks))
    uniq = [len(np.unique(np.round(out[:, c], 9))) for c in range(3)]
    print(f"{'':<8} 唯一值 T/CO/CO2 = {uniq}")
    print(f"{'':<8} {'✅ 有真实增长段，可训练' if ok else '❌ 增长过快，斜坡疑似未生效'}")
    return ok


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = {}
    for nm, cs in CASES.items():
        if only and nm != only:
            continue
        results[nm] = convert(nm, cs)
    print(f"\n输出目录: {os.path.relpath(OUT_DIR, os.getcwd())}")
    bad = [k for k, v in results.items() if v is False]
    if bad:
        print("未通过增长段验收: " + "、".join(bad))

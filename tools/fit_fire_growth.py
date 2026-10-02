"""
从真实的 180 秒 FDS 轨迹里拟合温度增长律，作为长时程外推的物理依据。

为什么要它：
    LSTM 自回归外推在 ~45 秒后会收敛到极限环（实测确认），无法给出
    15~30 分钟的火灾发展。真实损管系统在这个量级用的是**火灾增长设计曲线**
    （SOLAS II-2 / IMO 的标准做法），其增长率必须来自实际数据。

    这里不取教科书里的典型值，而是从本项目自己的 4 个舱 × 180 秒真实
    FDS 结果里拟合指数 n：T(t) ∝ t^n。这样长期外推至少有真实数据支撑。

输出：每个舱的 n、达到 500℃ 的时间，以及 t^n 的拟合优度。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

TRAIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds", "train")


def load(name):
    path = os.path.join(TRAIN_DIR, f"{name}_180.csv")
    if not os.path.exists(path):
        return None
    d = np.genfromtxt(path, delimiter=",", names=True)
    return np.atleast_1d(d)


def fit_power_law(t, T):
    """拟合 T - T0 ∝ t^n（对数线性回归）。只取升温段，峰值后是衰减。"""
    T0 = float(T[0])
    Tmax = float(T.max())
    peak_i = int(np.argmax(T))
    seg = slice(0, peak_i + 1)
    t_s, T_s = t[seg], T[seg]
    m = (T_s > T0 + 5)          # 排除起火初期的平台段
    if m.sum() < 8:
        return None
    x = np.log(np.maximum(t_s[m], 1e-3))
    y = np.log(np.maximum(T_s[m] - T0, 1e-3))
    n, log_c = np.polyfit(x, y, 1)
    pred = np.exp(log_c) * np.maximum(t_s[m], 1e-3) ** n + T0
    ss_res = float(((T_s[m] - pred) ** 2).sum())
    ss_tot = float(((T_s[m] - T_s[m].mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return dict(n=float(n), c=float(np.exp(log_c)), r2=float(r2),
                T0=T0, t_peak=float(t[peak_i]), T_peak=Tmax)


def main():
    print("从真实 180s FDS 轨迹拟合温度增长律  T - T0 = c · t^n\n")
    print(f"{'舱室':<8}{'n 指数':>9}{'c 系数':>12}{'R²':>8}"
          f"{'实测峰值':>10}{'达峰时刻':>10}{'达500℃':>10}")
    for name in ("主机舱", "机库", "士兵住舱", "灶炉间"):
        d = load(name)
        if d is None:
            print(f"{name:<8} 缺数据")
            continue
        t = d["Time"]
        T = d["temperature"]
        r = fit_power_law(t, T)
        if r is None:
            print(f"{name:<8} 升温段样本不足")
            continue
        # 由拟合式外推：达到 500℃ 需要多久
        t500 = ((500 - r["T0"]) / r["c"]) ** (1.0 / r["n"]) if r["c"] > 0 else float("inf")
        print(f"{name:<8}{r['n']:>9.2f}{r['c']:>12.4f}{r['r2']:>8.3f}"
              f"{r['T_peak']:>9.0f}C{r['t_peak']:>9.0f}s{t500:>9.0f}s")

    print("\n说明：")
    print("  n 越大表示火势发展越快。舱室火灾的 t^1.8~t^3 属正常量级。")
    print("  达500℃ 是按拟合式外推得到的，用于判断还需多久会烧到 500℃ ——")
    print("  这是**物理外推**，不是 LSTM 模型输出，界面上必须如实区分。")


if __name__ == "__main__":
    main()

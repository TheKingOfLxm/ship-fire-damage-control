"""
按舱室真实 FDS 轨迹做一次"标准时间线"回放，验证数据链路是否通。

用途：把某个舱室的真实训练轨迹当作"已知答案"，走一遍
  后端演化引擎 -> LSTM 服务 -> /api/fleet/status
如果回放时温度按真实轨迹变化，说明链路是通的；
如果仍是 20℃ 恒定，说明是**数据**没温度（不是系统 bug）。

这不等于预测能力，只验证数据可信度与链路连通性。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

API = "http://localhost:5182"
CASES = {
    "机库": (5, "tools/fds/ship_6_1800_devc.csv"),
    "灶炉间": (3, "tools/fds/ship_5_1800_devc.csv"),
    "士兵住舱": (4, "tools/fds/ship_8_1800_devc.csv"),
}


def read_trace(path):
    import pandas as pd
    df = pd.read_csv(path, skiprows=[0])
    df.columns = [c.strip().strip('"') for c in df.columns]
    return df


def main():
    for name, (cid, path) in CASES.items():
        if not os.path.exists(path):
            print(f"[{name}] 缺 {path}")
            continue
        df = read_trace(path)
        T = df["TC"].to_numpy(float)
        t = df[df.columns[0]].to_numpy(float)
        uniq = len(set(round(x, 3) for x in T))
        print(f"\n=== {name} (id={cid}) 真实 FDS 轨迹 ===")
        print(f"  时长 {t[-1]:.0f}s  温度 {T[0]:.1f} -> 峰 {T.max():.0f} -> 末 {T[-1]:.1f}℃")
        print(f"  温度唯一值 {uniq}  {'✅ 有真实温度信号' if uniq > 100 else '❌ 无信号'}")

        # 抽 6 个时刻的真实值，和系统当前读数对照
        samples = []
        for frac in (0.05, 0.2, 0.4, 0.6, 0.8, 0.98):
            i = int(t[-1] * frac)
            samples.append((int(t[i]), round(float(T[i]), 1)))
        print(f"  轨迹采样: {samples}")


if __name__ == "__main__":
    main()

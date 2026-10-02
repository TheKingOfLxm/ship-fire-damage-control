"""
机库温度通道退化的可修复性分析。

事实：机库训练数据中 temperature 全程恒为 20.0℃（345 个样本，1 个唯一值），
CO 与 CO2 正常上升。任何模型都无法从恒定输入学到温度动力学 —— 这是数据问题，
不是模型问题。

这里检验一个替代方案的可行性：舱室火灾的温度与 CO2 产率存在稳定关系，
能否用**其它舱室真实轨迹**拟合出 T–CO2 关系，再套到机库上。

注意这不是 LSTM 在预测机库温度，而是一个来自其它舱室实测数据的标定关系。
如果要用，必须如实标注来源，不能冒充模型输出。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import CASES, load_raw, resample_uniform

STEP = 0.1


def main():
    curves = {}
    for n in CASES:
        t, v = load_raw(n)
        _, r = resample_uniform(t, v, STEP)
        curves[n] = r

    print("各舱温度峰值与对应 CO2：")
    print(f"{'舱室':<10}{'温度峰值℃':>12}{'CO2 峰值ppm':>14}{'比值℃/万ppm':>14}")
    for n, r in curves.items():
        T = r[:, 0]
        C2 = r[:, 2] * 1e6
        if T.max() - T.min() < 1e-6:
            print(f"{n:<10}{'恒定 20.0':>12}{C2.max():>14.0f}{'—':>14}")
        else:
            print(f"{n:<10}{T.max():>12.1f}{C2.max():>14.0f}"
                  f"{T.max()/(C2.max()/1e4):>14.3f}")

    # 用温度有变化的三个舱拟合 T 对 CO2 的关系
    src = [n for n in CASES if curves[n][:, 0].max() - curves[n][:, 0].min() > 1.0]
    print(f"\n用于标定的舱室: {', '.join(src)}")

    X, Y = [], []
    for n in src:
        r = curves[n]
        X.append(r[:, 2])
        Y.append(r[:, 0])
    X = np.concatenate(X)
    Y = np.concatenate(Y)

    # 一次关系 T = a * CO2(ppm) + b
    A = np.vstack([X * 1e6, np.ones_like(X)]).T
    coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
    pred = A @ coef
    ss_res = float(((Y - pred) ** 2).sum())
    ss_tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    print(f"线性标定 T = {coef[0]:.6f} * CO2_ppm + {coef[1]:.2f}   R² = {r2:.4f}")

    if "机库" in curves:
        h = curves["机库"]
        c2 = h[:, 2] * 1e6
        t_hat = coef[0] * c2 + coef[1]
        print(f"\n套到机库上（CO2 峰值 {c2.max():.0f} ppm）:")
        print(f"   推得温度 {t_hat[0]:.1f} -> {t_hat.max():.1f} ℃")
        print(f"   机库实测温度: 恒定 20.0℃（无变化，推得值不可验证）")
        print("\n   ⚠ 这条关系从未在机库数据上验证过 —— 机库没有温度变化的真值，")
        print("     所以它只能作为『量级参考』，不能当作该舱的预测结果。")


if __name__ == "__main__":
    main()

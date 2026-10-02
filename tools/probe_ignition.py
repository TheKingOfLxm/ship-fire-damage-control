# -*- coding: utf-8 -*-
"""诊断：为什么温度从 20℃ 一步跳到几百℃ 然后在高位浮动。

假设：模型被问「刚起火，20℃，7.5 秒后是多少？」，
而 MSE 训练下这个问题的贝叶斯最优解是「所有可能结果的均值」，
也就是中段火情温度 —— 于是起火瞬间被一步推到 ~490℃。

本脚本直接从训练数据里算出这个「正确答案」，验证假设。
"""
import os
import sys
import glob

import numpy as np
import pandas as pd

io = sys.stdout
try:
    io.reconfigure(encoding='utf-8')
except Exception:
    pass

TRAIN30 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fds', 'train30')
WANTED = ['temperature', 'carbon monoxide', 'carbon dioxide']


def load(path):
    """train30 的 CSV 有正常表头（Time, temperature, CO, CO2）；
    旧 PyroSim 导出多一行单位行，列名可能是全名，这里两种都兼容。"""
    for skip in ([], [0]):
        try:
            df = pd.read_csv(path, skiprows=skip)
            df.columns = [str(c).strip().strip('"') for c in df.columns]
            low = [c.lower() for c in df.columns]
            if any('temperature' in c for c in low):
                return df
        except Exception:
            continue
    raise RuntimeError(f'无法解析表头: {path}')


def main():
    for path in sorted(glob.glob(os.path.join(TRAIN30, '*.csv'))):
        name = os.path.splitext(os.path.basename(path))[0]
        df = load(path)
        cols = [c for c in df.columns]
        tcol = next((c for c in cols if c.lower().startswith('time')), None)
        tcol = tcol or cols[0]
        temp = [c for c in cols if 'temperature' in c.lower()][0]
        t = df[tcol].to_numpy(float)
        T = df[temp].to_numpy(float)
        dt = float(np.median(np.diff(t)))

        seq, pred = 50, 75          # 与 models_v3 checkpoint 一致
        hist_s = seq * dt
        fut_s = pred * dt

        print('=' * 72)
        print(f'{name}   {len(T)} 点   dt={dt:.4f}s   时域={t[-1]-t[0]:.0f}s')
        print(f'  模型看 {hist_s:.1f}s 历史 -> 预测未来 {fut_s:.1f}s')
        print(f'  温度: 起 {T[0]:.1f}℃  末 {T[-1]:.1f}℃  峰 {T.max():.1f}℃  '
              f'唯一值 {len(np.unique(np.round(T, 3)))}')

        # 真实火灾从环境温度爬到 400℃ 需要多久
        reach = np.where(T >= 400.0)[0]
        if len(reach):
            print(f'  真实轨迹 20℃ -> 400℃ 用时 {t[reach[0]] - t[0]:.1f}s '
                  f'({reach[0]} 步)')
        else:
            print(f'  真实轨迹始终未到 400℃')

        # ---- 关键：起火窗口「7.5 秒后」的真实分布 ----
        # 找所有历史窗温度都在环境温度附近的起点
        amb = T[0]
        starts = []
        for i in range(0, len(T) - seq - pred, max(1, int(0.5 / dt))):
            win = T[i:i + seq]
            if win.max() < amb + 30.0:            # 整窗都还是环境温度
                j = i + seq + pred
                if j < len(T):
                    starts.append((T[j], T[i + seq]))
        if not starts:
            print('  [起火窗口] 训练数据里没有「全窗环境温度」的窗口')
            continue
        fut = np.array([a for a, _ in starts])
        print(f'  [起火窗口] 共 {len(starts)} 个「整窗≈{amb:.0f}℃」的窗口')
        print(f'    7.5 秒后真实温度: min={fut.min():.1f}  '
              f'mean={fut.mean():.1f}  max={fut.max():.1f}')
        print(f'    >>> MSE 训练下模型会输出该分布的均值 ≈ {fut.mean():.1f}℃')
        print(f'    >>> 这就是「20℃ 一步跳到几百℃」的直接原因')

        # 对照：末段窗口
        tail = T[-1]
        j = len(T) - 1
        print(f'  [末段] 训练轨迹末端 {tail:.1f}℃，7.5s 量级内基本不��� -> '
              f'模型输出自然在 {tail:.0f}℃ 附近')


if __name__ == '__main__':
    main()

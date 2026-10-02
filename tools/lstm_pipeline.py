"""
火灾 LSTM 的数据管线与实验框架。

背景：原始训练脚本有三个方法学问题，导致模型在部署时表现远差于预期：

  1. **时间尺度是假的**。config 注释写「前15秒（150个数据点）」，但 PyroSim
     导出的 CSV 采样间隔并不均匀（主机舱 mean 0.038s，min 0.0103 / max 0.1411）。
     训练时直接把相邻行当成等间隔序列，模型学到的「一步」并不是一秒，
     部署时前端按 0.1s 喂数据，时间尺度直接错位。

  2. **训练/验证随机切分**。random_split 把时间序列的窗口打乱后随机分配，
     相邻窗口大量重叠 —— 验证集几乎被训练集覆盖，早停选出的模型是在
     重叠数据上拟合出来的，验证 loss 严重偏乐观。

  3. **标准化器在全量数据上拟合**。scaler 在切分之前 fit，等于把验证集的
     统计量泄漏进了训练。

本模块提供正确做法：统一时间网格重采样 + 按时间顺序切分 + 仅用训练集拟合标准化器。
重采样只对已有真实测量做插值，不产生任何新数据。
"""
import os

import numpy as np
import pandas as pd

ROOT = r"D:\PyrosimLSTM"
# 180 秒版本（FDS 重跑，带真实温度信号）
NEW_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds", "train")
# 30 分钟版本（覆盖起火→增长→充分发展→达峰→衰减全过程）
NEW_DATA_30 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds", "train30")

# 舱室 -> (训练目录, CSV 文件名)
CASES = {
    "主机舱": ("zjc.LSTM(new)", "主机舱.csv"),
    "机库": ("JK_LSTM", "机库.csv"),
    "士兵住舱": ("SBZV.lstm", "士兵住舱.csv"),
    "灶炉间": ("LZJ.LSTM", "炉灶间.csv"),
    # 电站间原来借用主机舱的模型，两舱温度数值完全相同。
    # 它有自己的 FDS 算例和 30 分钟轨迹，必须单列。
    "电站间": ("ship_4", "电站间.csv"),
}

FEATURES = ["temperature", "carbon monoxide", "carbon dioxide"]


def load_raw(name, prefer_new=True, prefer_30min=False):
    """读取训练数据，返回 (时间秒, [n,3] 物理量)。

    prefer_30min=True 时优先用 30 分钟数据 —— 损管需要的是完整火灾发展过程，
    只见过增长段的模型永远预测不出"火势开始回落"。
    没有 30 分钟版本的舱自动回退到 180 秒，再回退到原始 30 秒。
    """
    order = [NEW_DATA_30, NEW_DATA] if prefer_30min else [NEW_DATA, NEW_DATA_30]
    for base in order:
        fname = f"{name}.csv" if prefer_30min else f"{name}_180.csv"
        new = os.path.join(base, fname)
        if os.path.exists(new):
            df = pd.read_csv(new)
            t = df["Time"].to_numpy(float)
            v = df[["temperature", "CO", "CO2"]].to_numpy(float)
            order_idx = np.argsort(t)
            return t[order_idx], v[order_idx]

    folder, fn = CASES[name]
    path = os.path.join(ROOT, folder, fn)
    # 第 0 行是单位，第 1 行是字段名
    df = pd.read_csv(path, skiprows=[0])
    df.columns = [c.strip().strip('"') for c in df.columns]
    t = df[df.columns[0]].to_numpy(float)
    v = df[FEATURES].to_numpy(float)
    order = np.argsort(t)
    return t[order], v[order]


def native_step(t):
    """原始采样间隔（秒/步）。"""
    return float(np.median(np.diff(t)))


def resample_uniform(t, v, step):
    """重采样到 step 秒的均匀时间网格。

    只在原始数据的时间跨度内插值，不外推 —— 外推等于凭空造数据。
    """
    t0, t1 = t[0], t[-1]
    n = int(np.floor((t1 - t0) / step)) + 1
    grid = t0 + np.arange(n) * step
    out = np.empty((n, v.shape[1]), dtype=float)
    for c in range(v.shape[1]):
        out[:, c] = np.interp(grid, t, v[:, c])
    return grid, out


def chronological_split(n, train_frac=0.7, val_frac=0.15):
    """按时间顺序切分，绝不 shuffle。"""
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    return slice(0, n_train), slice(n_train, n_train + n_val), slice(n_train + n_val, n)


def make_windows(data, seq_len, pred_len):
    """滑动窗口，返回 (X, Y)，形状 (n_win, seq_len, 3) / (n_win, pred_len, 3)。"""
    total = seq_len + pred_len
    if len(data) < total:
        return np.empty((0, seq_len, data.shape[1])), np.empty((0, pred_len, data.shape[1]))
    idx = np.arange(len(data) - total + 1)[:, None]
    xi = idx + np.arange(seq_len)[None, :]
    yi = idx + seq_len + np.arange(pred_len)[None, :]
    return data[xi], data[yi]


def per_channel_constant(v):
    """返回每个通道的唯一值个数，用于识别退化通道。"""
    return [int(len(np.unique(np.round(v[:, c], 9)))) for c in range(v.shape[1])]


def describe(name, step=0.1):
    """打印一个舱室的管线信息。"""
    t, v = load_raw(name)
    const = per_channel_constant(v)
    g, r = resample_uniform(t, v, step)
    print(f"── {name} ──")
    print(f"   原始 {len(t)} 点, 原生间隔 {native_step(t):.4f}s, 跨度 {t[-1]-t[0]:.1f}s")
    print(f"   重采样 @{step}s -> {len(g)} 点, 跨度 {g[-1]-g[0]:.1f}s")
    for i, f in enumerate(FEATURES):
        flag = "  ⚠ 退化" if const[i] <= 2 else ""
        print(f"   {f:<20} {v[:, i].min():12.4f} -> {v[:, i].max():12.4f}  唯一值 {const[i]}{flag}")
    return g, r


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for n in CASES:
        describe(n)
        print()

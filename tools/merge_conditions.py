"""
把同一舱室的多组工况轨迹合并成一个训练集。

关键点：**每组工况之间要留出"冷启动"间隔**。如果直接把多条轨迹首尾
拼接，窗口会跨越两个工况的交界处，学到"工况A的结尾突然跳到工况B的开头"
这种物理上不存在的跳变。

做法：每条轨迹内部按时间切片取窗口，切片长度取 seq+pred，
相邻切片之间丢弃 gap 个点作为边界缓冲。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

FDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds")
OUT_ROOT = os.path.join(FDS_DIR, "multi")


def load(path):
    df = pd.read_csv(path, skiprows=[0])
    df.columns = [c.strip().strip('"') for c in df.columns]
    t = df[df.columns[0]].to_numpy(float)
    v = df[["TC", "COLO", "CO2LO"]].to_numpy(float)
    o = np.argsort(t)
    return t[o], v[o]


def slice_windows(t, v, seq, pred, step_frac=0.25):
    """在单条轨迹上切不重叠的窗口，返回 (X, Y)。"""
    need = seq + pred
    span = need + int(need * step_frac)        # 相邻窗口之间留边界缓冲
    X, Y = [], []
    i = 0
    while i + need <= len(t):
        X.append(v[i:i + seq])
        Y.append(v[i + seq:i + need])
        i += span
    return np.array(X), np.array(Y)


def smooth(a, w):
    if w <= 1:
        return a
    k = np.ones(w) / w
    out = a.copy()
    for c in range(a.shape[1]):
        out[:, c] = np.convolve(a[:, c], k, mode="same")
    return out


def main():
    prefix = sys.argv[1]                 # 例如 c5
    seq, pred, sw = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    cases = sys.argv[5:]                  # 各工况的 devc 文件名（不含路径）

    Xs, Ys, tags = [], [], []
    for c in cases:
        p = os.path.join(FDS_DIR, f"{c}_devc.csv")
        if not os.path.exists(p):
            print(f"  缺 {c}，跳过")
            continue
        t, v = load(p)
        if t[-1] < 1795:
            print(f"  {c} 未跑完 ({t[-1]:.0f}s)，跳过")
            continue
        v = smooth(v, sw)
        x, y = slice_windows(t, v, seq, pred)
        if len(x) == 0:
            print(f"  {c} 太短，跳过")
            continue
        Xs.append(x)
        Ys.append(y)
        tags.append((c, len(x), float(v[:, 0].max())))
        print(f"  {c:<16} {len(x):>5} 窗口   温度峰 {v[:,0].max():.0f}C")

    if not Xs:
        print("没有可用工况")
        return

    X = np.concatenate(Xs)
    Y = np.concatenate(Ys)
    os.makedirs(OUT_ROOT, exist_ok=True)
    # ⚠ 存**工况之间的行分界**，不是窗口起点。
    # 之前误存了每个窗口的起点（575 个边界、每段 100 行），
    # 训练端按工况切段时得到 575 个长度 100 的碎段，
    # 每段都不够 seq+pred=200 行，于是"载入 0 段工况"。
    # 正确做法：合并时保留连续行，只记录 5 个工况各占多少行。
    rows_per_case = np.array([len(x) * seq for x in Xs], dtype=np.int64)
    np.save(os.path.join(OUT_ROOT, f"{prefix}_bounds.npy"), rows_per_case)
    np.save(os.path.join(OUT_ROOT, f"{prefix}_windowlen.npy"),
            np.array([seq, pred], dtype=np.int64))
    # 同时落一份平滑后的连续行表，训练端直接用它
    flat_all = np.concatenate([smooth(v, sw) for _, v in
                               [(c, load(os.path.join(FDS_DIR, f"{c}_devc.csv"))[1])
                                for c, _, _ in tags]])
    pd.DataFrame(flat_all, columns=["temperature", "CO", "CO2"]).to_csv(
        os.path.join(OUT_ROOT, f"{prefix}_raw.csv"), index=False)

    print(f"\n合计 {len(Xs)} 组工况 / {len(flat_all)} 连续行 / {len(X)} 窗口")
    print(f"  各工况行数: {rows_per_case.tolist()}")
    for c, w, peak in tags:
        print(f"    {c:<16} {w:>5} 窗口  峰 {peak:.0f}C")


if __name__ == "__main__":
    main()

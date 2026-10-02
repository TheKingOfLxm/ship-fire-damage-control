"""
LSTM 闭环外推可行性实测
══════════════════════════════════════════════════════════════
回答两个问题：
  1. 训练数据的真实时间跨度与量纲是什么？
  2. 把 LSTM 当作"火灾演化引擎"（自回归闭环外推）能撑多久？
     即：喂入最近 150 步 → 预测后续若干步 → 把预测接回窗口 → 重复，
     看输出在多长时间内仍在物理合理范围内。

用法: py -3 tools/probe_lstm_rollout.py
"""
import os
import sys

import numpy as np
import pandas as pd
import torch

ROOT = r"D:\PyrosimLSTM"
sys.path.insert(0, ROOT)

# 与 server.py 保持一致的模型定义
class Seq2SeqLSTM(torch.nn.Module):
    def __init__(self, input_dim=3, hidden_dim=128, num_layers=2,
                 output_dim=3, seq_len=150, pred_len=150, dropout=0.2):
        super().__init__()
        self.hidden_dim, self.num_layers = hidden_dim, num_layers
        self.seq_len, self.pred_len = seq_len, pred_len
        self.encoder_lstm = torch.nn.LSTM(
            input_dim, hidden_dim, num_layers, batch_first=True,
            dropout=dropout if num_layers > 1 else 0)
        self.decoder_lstm = torch.nn.LSTM(
            hidden_dim, hidden_dim, num_layers, batch_first=True,
            dropout=dropout if num_layers > 1 else 0)
        self.fc = torch.nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        enc, (h, c) = self.encoder_lstm(x)
        dec_in = enc[:, -1:, :].repeat(1, self.pred_len, 1)
        dec, _ = self.decoder_lstm(dec_in, (h, c))
        return self.fc(dec)


def load_model(folder):
    """与 server.py 保持一致：checkpoint 带 model_state_dict 与 StandardScaler。"""
    ckpt = torch.load(os.path.join(ROOT, folder, "models", "best_model.pth"),
                      map_location="cpu", weights_only=False)
    m = Seq2SeqLSTM(dropout=0.0)
    m.load_state_dict(ckpt["model_state_dict"])
    m.eval()
    return m, ckpt["scaler"]


def probe_data():
    print("=" * 68)
    print("① 训练数据实况")
    print("=" * 68)
    for folder, fname, unit in (
        ("zjc.LSTM(new)", "主机舱.csv", "K"),
        ("JK_LSTM", "机库.csv", "K"),
        ("SBZV.lstm", "士兵住舱.csv", "K"),
        ("LZJ.LSTM", "炉灶间.csv", "K"),
    ):
        p = os.path.join(ROOT, folder, fname)
        if not os.path.exists(p):
            print(f"  {folder}: 文件缺失")
            continue
        df = pd.read_csv(p, skiprows=[1])
        cols = list(df.columns)
        t = df[cols[0]].to_numpy(float)
        t2 = df[cols[1]].to_numpy(float)
        print(f"\n  {folder}  ({fname})")
        print(f"    列       : {cols}")
        print(f"    样本数   : {len(df)}")
        print(f"    时间跨度 : {t[-1] - t[0]:.1f} s   采样间隔 {(t[1]-t[0]):.4f} s")
        print(f"    {cols[1]:>12}: {t2[0]:.3f} → {t2[-1]:.3f} {unit}   "
              f"峰值 {t2.max():.3f}   谷值 {t2.min():.3f}")
        for c in cols[2:]:
            v = df[c].to_numpy(float)
            print(f"    {c:>12}: {v[0]*1e6:.2f} → {v[-1]*1e6:.2f} ppm     "
                  f"峰值 {v.max()*1e6:.2f}")
        n_steps = int((t[-1] - t[0]) / (t[1] - t[0]))
        print(f"    → 单条轨迹约 {n_steps} 步；模型窗口 150 步 ≈ "
              f"{150 * (t[1]-t[0]):.1f} s")


def probe_rollout():
    print()
    print("=" * 68)
    print("② 自回归闭环外推（LSTM 当演化引擎）")
    print("=" * 68)
    model, scaler = load_model("JK_LSTM")
    SEQ = 150

    # 构造一个物理合理的起火初始窗口：
    # 室温 20℃、CO/CO2 近零，末端已出现起火迹象（温度开始上升）
    window = np.zeros((SEQ, 3), dtype=np.float32)
    for i in range(SEQ):
        f = i / (SEQ - 1)
        window[i, 0] = 20.0 + 60.0 * f                 # 20 → 80 ℃
        window[i, 1] = 0.0 + 2.0e-5 * f                # CO   0 → 20 ppm
        window[i, 2] = 7.0e-4 + 2.0e-4 * f             # CO2  700 → 900 ppm

    print(f"  初始窗口: 温度 {window[0,0]:.1f}→{window[-1,0]:.1f}℃  "
          f"CO {window[0,1]*1e6:.1f}→{window[-1,1]*1e6:.1f}ppm")
    print(f"  scaler  : mean={np.round(scaler.mean_, 4)}  scale={np.round(scaler.scale_, 4)}")
    print()
    print("  外推（StandardScaler 归一化 → 预测 → 反归一化 → 接回窗口）:")
    print("  轮次   温度℃        CO(ppm)      CO2(ppm)    物理合理?")

    CHUNK = 1                       # 每轮推进 1 个模型步（最保守的漂移控制）
    dt = 0.357                        # 实测采样间隔 s
    bad_at = None
    with torch.no_grad():
        for r in range(1, 151):      # 最多 150 轮
            norm = scaler.transform(window)
            inp = torch.tensor(norm, dtype=torch.float32).unsqueeze(0)
            out = model(inp)[0].numpy()
            step = scaler.inverse_transform(out[0:1])[0]
            window = np.vstack([step[None, :], window[:-1]])

            T, CO, CO2 = float(step[0]), float(step[1]), float(step[2])
            ok = (0 < T < 1200) and (0 <= CO < 0.05) and (0.0003 < CO2 < 0.2)
            if not ok and bad_at is None:
                bad_at = r
            if r % 15 == 0 or (not ok and bad_at == r):
                print(f"  {r:>4}   {T:>9.1f}   {CO*1e6:>9.2f}   {CO2*1e6:>9.1f}   "
                      f"{'是' if ok else '★ 否'}   (t≈{r*CHUNK*dt:.0f}s)")

    print()
    if bad_at is None:
        print(f"  → 150 轮（约 {150*CHUNK*dt:.0f} s）内输出始终在物理范围内")
    else:
        print(f"  → 第 {bad_at} 轮（t≈{bad_at*CHUNK*dt:.0f}s）首次越界")
    print("  注：自回归外推误差会逐步累积，且训练分布只覆盖约 30s。")


if __name__ == "__main__":
    torch.set_num_threads(4)
    probe_data()
    probe_rollout()

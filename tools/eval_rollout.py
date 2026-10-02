"""
与实际用法对齐的评估：多步自回归推演。

系统里 LSTM 不是用来"预测整段轨迹"的，而是被 /api/lstm/evolve 反复调用，
每次从当前窗口往前推一小步。所以评估口径应该是：

    从测试段里的若干起点出发，连续自回归推演 N 步，
    比较推演轨迹与真实轨迹的偏差。

这比"一次性预测 2.5 秒"更接近真实使用，也比"在没见过的阶段上做一次性
外推"要公平 —— 起火过程本来就只能沿着已知的增长趋势往前推。

同时报告一个关键指标：**推演是否发散**。
火灾外推最典型的失败是温度指数爆炸或塌缩成常数，两者都要能看出来。
"""
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import CASES, FEATURES, load_raw, per_channel_constant, resample_uniform
from model_def import Seq2SeqLSTM
from retrain import STEP, segments

ROOT = r"D:\PyrosimLSTM"
OLD_DIR = {
    "主机舱": os.path.join(ROOT, "zjc.LSTM(new)", "models"),
    "机库": os.path.join(ROOT, "JK_LSTM", "models"),
    "士兵住舱": os.path.join(ROOT, "SBZV.lstm", "models"),
    "灶炉间": os.path.join(ROOT, "LZJ.LSTM", "models"),
}
NEW_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models_v2")
ROLLOUT_STEPS = 25          # 连续推演步数（25 * 0.1s = 2.5 秒）


def load_old(name):
    p = os.path.join(OLD_DIR[name], "best_model.pth")
    if not os.path.exists(p):
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    sd = ck["model_state_dict"]
    m = Seq2SeqLSTM(sd["encoder_lstm.weight_ih_l0"].shape[1], sd["fc.weight"].shape[1],
                    2, sd["fc.weight"].shape[0], 150, 150, 0.0)
    m.load_state_dict(sd, strict=False)
    m.eval()
    return m, ck["scaler"], 150, 150


def load_new(name):
    p = os.path.join(NEW_ROOT, name, "best_model.pth")
    if not os.path.exists(p):
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    c = ck["config"]
    layers = c.get("num_layers", c.get("layers", 2))
    m = Seq2SeqLSTM(3, c["hidden"], layers, 3, c["seq_len"], c["pred_len"], 0.0)
    m.load_state_dict(ck["model_state_dict"])
    m.eval()
    return m, ck["scaler"], c["seq_len"], c["pred_len"]


@torch.no_grad()
def rollout(model, scaler, seq_len, window, n_steps):
    """自回归推演：每次取模型输出第 0 步接回窗口末尾。"""
    w = np.array(window, dtype=float)
    out = []
    for _ in range(n_steps):
        xs = scaler.transform(w[-seq_len:])
        p = model(torch.FloatTensor(xs).unsqueeze(0))
        step = scaler.inverse_transform(p[0, 0].numpy().reshape(1, -1))[0]
        out.append(step)
        w = np.vstack([w, step[None, :]])
    return np.array(out)


def assess(name, data, const):
    """在整条轨迹上取多个起点做推演。

    ⚠ 这**不是留出集**评估：旧模型在全部数据上训练过，新模型训练过前 55%，
    所以绝对误差都偏乐观。它回答的是另一个问题 ——
    "把模型接进系统后连续推演，它会发散还是会塌缩"，这对本项目更重要。
    """
    res = {}
    for tag, loader in (("旧", load_old), ("新", load_new)):
        loaded = loader(name)
        if loaded is None:
            continue
        m, sc, sq, pr = loaded
        # 按通道分开统计，否则三个通道的指标会被平均成一个数，看不出谁好谁坏
        errs = [[] for _ in range(3)]
        div = [[] for _ in range(3)]
        flat = [[] for _ in range(3)]
        starts = 0
        for start in range(sq, len(data) - ROLLOUT_STEPS, 5):
            hist = data[start - sq:start]
            future = data[start:start + ROLLOUT_STEPS]
            pred = rollout(m, sc, sq, hist, ROLLOUT_STEPS)
            starts += 1
            for c in range(3):
                yt, yp = future[:, c], pred[:, c]
                rng = float(yt.max() - yt.min())
                mae = float(np.mean(np.abs(yt - yp)))
                errs[c].append(mae / rng * 100 if rng > 1e-12 else np.nan)
                div[c].append(1.0 if abs(yp[-1]) > 3 * max(abs(yt[-1]), 1e-9) + 1e-6 else 0.0)
                flat[c].append(1.0 if (yp.max() - yp.min()) < 1e-6 else 0.0)
        if starts:
            res[tag] = dict(
                err=[float(np.nanmean(e)) if e else float("nan") for e in errs],
                div=[float(np.mean(d)) if d else float("nan") for d in div],
                flat=[float(np.mean(f)) if f else float("nan") for f in flat],
                starts=starts)
    return res


def main():
    targets = sys.argv[1:] or list(CASES)
    for name in targets:
        t, v = load_raw(name)
        _, data = resample_uniform(t, v, STEP)
        const = per_channel_constant(v)
        res = assess(name, data, const)
        print(f"\n{'═'*74}\n  {name}   自回归推演 {ROLLOUT_STEPS} 步 ({ROLLOUT_STEPS*STEP:.1f}s)"
              f"   [整条轨迹多起点，非留出集]")
        for i, f in enumerate(FEATURES):
            deg = "  ⚠源数据恒定" if const[i] <= 2 else ""
            print(f"    {f}{deg}")
            for tag in ("旧", "新"):
                if tag not in res:
                    print(f"      {tag}: (无模型)")
                    continue
                r = res[tag]
                print(f"      {tag}: 相对误差 {r['err'][i]:8.2f}%   发散 {r['div'][i]*100:5.1f}%"
                      f"   塌缩 {r['flat'][i]*100:5.1f}%   (起点 {r['starts']})")


if __name__ == "__main__":
    main()

"""
对比实验：现有模型（按真实部署方式喂数据） vs 修正管线重训的新模型。

评估口径必须是「部署时会发生什么」，而不是「训练时 loss 多低」。
现有模型的 150 步窗口在原生采样下只覆盖约 5.7 秒，但前端按 0.1s 喂数据，
窗口就变成 15 秒 —— 时间尺度错位 2.6 倍。这里就按前端的实际行为评估它。
"""
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import (CASES, FEATURES, chronological_split, load_raw,
                           make_windows, native_step, resample_uniform)

ROOT = r"D:\PyrosimLSTM"
MODEL_DIR = {
    "主机舱": os.path.join(ROOT, "zjc.LSTM(new)", "models"),
    "机库": os.path.join(ROOT, "JK_LSTM", "models"),
    "士兵住舱": os.path.join(ROOT, "SBZV.lstm", "models"),
    "灶炉间": os.path.join(ROOT, "LZJ.LSTM", "models"),
}
CKPT = "best_model.pth"


def load_existing(name):
    """加载现有 checkpoint，返回 (model, scaler, meta)。"""
    from model_def import Seq2SeqLSTM  # 延迟导入，避免污染 sys.path
    p = os.path.join(MODEL_DIR[name], CKPT)
    if not os.path.exists(p):
        return None, None, None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    # 从 checkpoint 反推结构参数：fc.weight 是 (output_dim, hidden_dim)
    sd = ck["model_state_dict"]
    hidden = sd["fc.weight"].shape[1]
    model = Seq2SeqLSTM(input_dim=3, hidden_dim=hidden, num_layers=2,
                        output_dim=3, seq_len=150, pred_len=150, dropout=0.0)
    # 旧 checkpoint 没有位置嵌入，基线评估用 strict=False 跳过缺失项
    missing, unexpected = model.load_state_dict(sd, strict=False)
    model.eval()
    return model, ck.get("scaler"), ck


def predict(model, scaler, x_raw):
    """x_raw: (n, seq, 3) 物理量 -> 预测的物理量 (n, pred, 3)。"""
    with torch.no_grad():
        xs = torch.FloatTensor(scaler.transform(x_raw.reshape(-1, 3)).reshape(x_raw.shape))
        out = model(xs).numpy()
    return scaler.inverse_transform(out.reshape(-1, 3)).reshape(out.shape)


def metrics(y_true, y_pred, const_mask):
    """逐通道指标。const_mask 标出数据本身退化的通道。"""
    rows = []
    for c in range(3):
        yt, yp = y_true[:, :, c], y_pred[:, :, c]
        mae = float(np.mean(np.abs(yt - yp)))
        rng = float(yt.max() - yt.min())
        rel = mae / rng if rng > 1e-12 else float("nan")
        # 预测是否真的在动 —— 塌缩的模型会输出近乎常数
        pred_span = float(yp.max(axis=(0, 1))[c] - yp.min(axis=(0, 1))[c]) if False else float(yp.max() - yp.min())
        var = float(np.var(yp))
        rows.append(dict(mae=mae, rel=rel, pred_span=pred_span, pred_var=var, degenerate=const_mask[c]))
    return rows


def evaluate(name, step=0.1, seq=150, pred=150):
    t, v = load_raw(name)
    grid, data = resample_uniform(t, v, step)
    from lstm_pipeline import per_channel_constant
    const = per_channel_constant(v)

    _, _, test_slice = chronological_split(len(data), 0.7, 0.15)
    test = data[test_slice]

    model, scaler, _ = load_existing(name)
    out = dict(compartment=name, usable=len(test))
    if model is None:
        out["error"] = "无 checkpoint"
        return out

    X, Y = make_windows(test, seq, pred)
    if len(X) == 0:
        # 测试段不够切一个窗口，退回全量末尾窗口
        X, Y = make_windows(data, seq, pred)
        X, Y = X[-1:], Y[-1:]
    P = predict(model, scaler, X)
    out["windows"] = int(len(X))
    out["channels"] = metrics(Y, P, const)
    out["seq"] = seq
    out["pred"] = pred
    out["window_input_sec"] = seq * step
    out["window_pred_sec"] = pred * step
    return out


def report(res):
    print(f"══ {res['compartment']} ══")
    if "error" in res:
        print(f"   {res['error']}")
        return
    print(f"   窗口 {res['windows']} 个 | 输入 {res['window_input_sec']:.1f}s -> 预测 {res['window_pred_sec']:.1f}s")
    for i, c in enumerate(FEATURES):
        m = res["channels"][i]
        tag = "  ⚠数据退化" if m["degenerate"] else ""
        print(f"   {c:<20} MAE {m['mae']:11.4f}  相对 {m['rel']*100:6.2f}%"
              f"  预测跨度 {m['pred_span']:11.4f}  预测方差 {m['pred_var']:11.5f}{tag}")
    print()


if __name__ == "__main__":
    for n in CASES:
        try:
            report(evaluate(n))
        except Exception as e:
            print(f"══ {n} ══\n   评估失败: {type(e).__name__}: {e}\n")

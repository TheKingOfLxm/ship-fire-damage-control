"""
决定性验证：多工况训练后，模型是否学会了「起始条件影响后续演化」。

判定方法（直接、可复现）：
    喂进同一条形状的上升窗口，只改起始温度，看模型第一步输出多少。

    单轨迹模型（旧）：  喂 200℃ -> 输出 317℃   与输入无关 = 背曲线
    多工况模型（期望）：喂 200℃ -> 输出接近 200℃
                       喂 400℃ -> 输出接近 400℃

    起始温度差 200℃，若模型输出差 < 20℃ 视为"不敏感"（失败）；
    差 > 80℃ 视为"敏感"（通过）。
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

CASES = [
    ("多工况(灶炉间)", r"D:\PyrosimLSTM\..\..\models_multi", "灶炉间"),
]

# 实际路径在项目里
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def probe(model_dir, compartment, tag):
    import torch
    from model_def import Seq2SeqLSTM
    p = os.path.join(model_dir, compartment, "best_model.pth")
    if not os.path.exists(p):
        print(f"  {tag}: 无模型 {p}")
        return None
    ck = torch.load(p, map_location="cpu", weights_only=False)
    c = ck["config"]
    seq, pred = c["seq_len"], c["pred_len"]
    m = Seq2SeqLSTM(3, c["hidden"], c.get("num_layers", 1), 3, seq, pred, 0.0)
    m.load_state_dict(ck["model_state_dict"]); m.eval()
    sc = ck["scaler"]

    print(f"\n  {tag}")
    print(f"    {'喂入起始T':>10} {'模型第1步':>10} {'偏差':>8}   判定")
    results = []
    for t0 in (150.0, 200.0, 300.0, 400.0, 500.0):
        # 构造一条从 t0 线性上升到 t0+50 的窗口
        w = np.array([[t0 + i * 0.5, 0.0002, 0.001] for i in range(seq)])
        xs = sc.transform(w)
        with torch.no_grad():
            out = m(torch.FloatTensor(xs).unsqueeze(0))
        first = sc.inverse_transform(out[0, 0].numpy().reshape(1, -1))[0]
        dev = first[0] - t0
        results.append((t0, first[0]))
        print(f"    {t0:>9.0f}℃ {first[0]:>9.1f}℃ {dev:>+8.1f}")

    lo = min(r[1] for r in results)
    hi = max(r[1] for r in results)
    span = hi - lo
    in_span = max(r[1] - r[0] for r in results) - min(r[1] - r[0] for r in results)
    verdict = "✅ 敏感（学到了条件依赖）" if span > 80 else "❌ 不敏感（仍在背曲线）"
    print(f"    输出跨度 {span:.0f}℃  ->  {verdict}")
    return span


if __name__ == "__main__":
    probe(os.path.join(ROOT, "models_multi"), "灶炉间", "多工况模型（灶炉间 5 组条件）")
    probe(os.path.join(ROOT, "models_v3"), "灶炉间", "单轨迹模型 v3（灶炉间 1 条曲线）")

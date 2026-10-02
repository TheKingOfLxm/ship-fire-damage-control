# -*- coding: utf-8 -*-
"""用**有火灾发展过程**的新数据重训 LSTM（v4）。

======================================================================
和 v3 的差别不只是换了数据，还有时域口径
======================================================================
v3 用 0.1s 网格、seq=50/pred=75，每步只走 5~7.5 秒。
30 分钟要 18000 步自回归，而 Seq2Seq 实测几十步就塌缩 —— 时域上根本不可能。

v4 用 FDS 实际的 0.5s 输出网格（DT_DEVC=0.5），seq=120/pred=120，
每步走 60 秒。30 分钟只要 **30 步**自回归，落在可自回归的范围内。
输入 60 秒历史、预测未来 60 秒，对损管也才有意义。

输出到 models_v4 而不是就地覆盖 models_v3：
先验证新模型的泛化和自回归稳定性，确认不倒退再切换加载优先级。
"""
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import retrain as R
from lstm_pipeline import FEATURES, load_raw, per_channel_constant

STEP = 0.5                 # 与 prep_fds_design.py 的 DT_DEVC 一致
OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "models_v4")

# 60 秒输入 / 60 秒输出。smooth=10 -> 5 秒滑动平均，用来压 FDS 网格振荡，
# 不做更长的平滑：再长会把增长段的拐点抹平，正是要学的东西。
CONFIGS = [
    dict(seq_len=120, pred_len=120, hidden=64, layers=1, dropout=0.3,
         lr=2e-3, epochs=60, num_layers=1, smooth=10),
]


def main():
    targets = sys.argv[1:] or list(R.CASES)
    os.makedirs(OUT_ROOT, exist_ok=True)
    R.STEP = STEP
    R.OUT_ROOT = OUT_ROOT
    R.CONFIGS = CONFIGS

    summary = []
    for name in targets:
        print(f"\n{'='*68}\n  {name}\n{'='*68}")
        t, v = load_raw(name, prefer_30min=True)
        const = per_channel_constant(v)
        bad = [FEATURES[i] for i in range(3) if const[i] <= 2]
        dt = float(np.median(np.diff(t))) if len(t) > 1 else 0.0
        print(f"  数据 {len(t)} 点 / {t[-1]-t[0]:.0f}s  原始间隔 {dt:.3f}s")
        if bad:
            print(f"  [警告] 源数据恒定通道: {', '.join(bad)}")

        res = R.train_one(name, CONFIGS[0], verbose=True, refit=True)
        if res is None:
            print("  数据装不下窗口，跳过")
            continue
        print(f"  训练完成: 窗 {res['n_train']}  val={res['val_loss']:.5f}  "
              f"输入{res['input_sec']:.0f}s 预测{res['pred_sec']:.0f}s")

        out = os.path.join(OUT_ROOT, name)
        os.makedirs(out, exist_ok=True)
        torch.save({
            "model_state_dict": res["model"].state_dict(),
            "scaler": res["scaler"],
            "config": res["cfg"],
            "step_seconds": STEP,
            "input_dim": 3, "output_dim": 3,
            "val_loss": res["val_loss"],
            "degenerate_channels": bad,
            "trained_horizon_seconds": round(float(t[-1] - t[0]), 1),
            "pipeline": "0.5s 网格(FIREDESIGN t^2 斜坡 + 上层烟气层) "
                        "+ 时间序切分 + 全量重拟合",
        }, os.path.join(out, "best_model.pth"))
        print(f"  -> {os.path.relpath(out, os.getcwd())}/best_model.pth")
        summary.append((name, res))

    print(f"\n{'='*68}\n  汇总\n{'='*68}")
    for name, b in summary:
        print(f"  {name:<10} seq={b['cfg']['seq_len']:<4}pred={b['cfg']['pred_len']:<4}"
              f"输入{b['input_sec']:.0f}s 预测{b['pred_sec']:.0f}s  "
              f"val={b['val_loss']:.5f}")


if __name__ == "__main__":
    main()

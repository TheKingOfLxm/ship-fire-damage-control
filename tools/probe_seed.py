# -*- coding: utf-8 -*-
"""诊断：/evolve 三个舱返回完全相同的数值，连 windowVariance 都一样。

说明 seed_from_training_data 给它们的窗口是同一份。查种子来源。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "lstm-prediction-server"))
sys.stdout.reconfigure(encoding="utf-8")

import server as S

for name in ("主机舱", "电站间", "灶炉间", "机库", "士兵住舱"):
    folder = S.FirePredictor.COMPARTMENT_MODEL_MAP.get(name, "<默认>")
    real = S.FirePredictor.NAME_ALIASES.get(name, name)
    win, total = S.seed_from_training_data(name, 50, 0)
    if win is None:
        print(f"{name:<8} 种子=NULL   (folder={folder}, real={real})")
        continue
    a = np.array(win)
    print(f"{name:<8} folder={folder:<14} real={real:<6} 轨迹长={total:<6} "
          f"窗口形状={a.shape}  温 {a[:,0].min():.1f}~{a[:,0].max():.1f}℃  "
          f"均值{a[:,0].mean():.1f}℃")

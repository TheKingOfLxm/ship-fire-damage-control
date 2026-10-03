# -*- coding: utf-8 -*-
"""v7：含灭火干预的 4 通道训练 —— 让模型学会回答"投入灭火后火会怎么走"。

======================================================================
为什么需要 v7
======================================================================
v6 的训练数据全是"没人灭火"的火灾发展轨迹。系统对 suppress 指令
只能做运维层叠加（显示层回落），**原则上**算不出反事实 —— 因为模型
的世界里不存在"灭火"这个动作。

v7 把灭火干预变成模型的**第 4 个输入通道**：

    输入  (T, CO, CO₂, S)    S = 灭火剂投放状态，0(未投放)→1(到位)
    输出  (ΔT, ΔCO, ΔCO₂)   增量目标（v6 配方不变）

因果方向干净：S 是"操作员做了什么"，输出是"火怎么响应"。
推理时把当前灭火状态填进 S，模型给出扑救后的演化 —— 反事实由此成立。

======================================================================
数据来源
======================================================================
  旧 v6 工况（growth 基准 + cond 条件）     →  S ≡ 0（没有干预）
  suppress/ 干预算例（gen_suppression.py）  →  S(t) 从 manifest 精确重构：
        S(t) = 0                          (t <  t_s)
        S(t) = 1 - exp(-(t - t_s) / 5)    (t >= t_s, 10 秒内到位)

干预工况三档（详见 gen_suppression.py 的工况设计）：
  s0180_t0060  增长期强扑 → 火被扑灭，层温快速回落
  s0300_t0150  充分发展初期中效 → 压制但拖尾
  s0600_t0300  充分发展期弱效 → 扑晚了，压不住

======================================================================
验证（本版本的核心证据）
======================================================================
留出**一整个干预算例**（模型完全没见过这种扑法），评估：
  1. 整段温度 MAE（与 v6 同口径）
  2. 干预段 MAE（t >= t_s 之后的响应）—— 模型能否从 S 通道推出
     正确的压制响应，是"反事实能力"的直接度量
留出旧工况（换一场火）的验证照旧，确认加通道没有损害基线能力。

======================================================================
用法
======================================================================
    RETRAIN_V7_FOLDS=1 py -3 tools/retrain_v7.py            # 全部舱室
    RETRAIN_V7_FOLDS=1 py -3 tools/retrain_v7.py 灶炉间      # 单个舱室
    RETRAIN_V7_SMOKE=1 py -3 tools/retrain_v7.py 灶炉间      # 3 epoch 冒烟，
                                                             # 产物进 tools/smoke_models

产物: models_v7/<舱室>/best_model.pth（input_dim=4）
服务端重启后自动加载（优先级 models_v7 > models_v6 > v3 > v2）。
"""
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import make_windows
from model_def import Seq2SeqLSTM

HERE = os.path.dirname(os.path.abspath(__file__))
FDS = os.path.join(HERE, "fds")
SMOKE = bool(os.environ.get("RETRAIN_V7_SMOKE"))
OUT_ROOT = os.path.join(HERE, "smoke_models") if SMOKE else \
    os.path.join(HERE, "..", "models_v7")

STEP = 0.5
HID, LAYERS, SEQ, PRED = 64, 2, 60, 60
EPOCHS, LR, DROPOUT, SMOOTH = 45, 2e-3, 0.2, 10
if SMOKE:
    EPOCHS = 3
TAIL = 0.15          # 每条轨迹末尾 15% 不参与训练
S_DELIVERY_TAU = 5.0  # 灭火剂到位时间常数（与 gen_suppression.py 一致）

EXPECTED_POINTS = 3601
STALE_SEC = 900

COMPARTMENTS = {
    "灶炉间": ("growth", "d5", "d5"),
    "机库": ("growth", "d6", "d6"),
    "士兵住舱": ("growth", "d8m", "d8"),
    "电站间": ("growth", "d4b", "d4"),
    "主机舱": ("growth", "zjc", "zjc"),
}
EXTRA = ["q035_v100", "q070_v100", "q100_v050", "q100_v200",
         "q150_v100", "q200_v150"]
SUPPRESS_DIR = os.path.join(FDS, "suppress")
SUPPRESS_MANIFEST = os.path.join(SUPPRESS_DIR, "manifest.json")


def _read_devc(path, label):
    """读一个 devc.csv 并过完整性闸门：点数齐 + 不在被写。"""
    if not os.path.exists(path):
        return None
    n = sum(1 for _ in open(path, "rb")) - 2
    if n != EXPECTED_POINTS:
        print(f"    {label:<22} 跳过：只跑了 {n}/{EXPECTED_POINTS} 点")
        return None
    if time.time() - os.path.getmtime(path) < STALE_SEC:
        print(f"    {label:<22} 跳过：文件仍在写入")
        return None
    d = np.genfromtxt(path, delimiter=",", names=True, skip_header=1)
    names = list(d.dtype.names)
    tl = sorted([n for n in names if n.startswith("TL")],
                key=lambda n: int(n[2:]))
    if not tl:
        return None
    v = np.column_stack([
        np.mean([d[n] for n in tl], axis=0),
        np.mean([d[n] for n in ("COLO", "COHI") if n in names], axis=0),
        np.mean([d[n] for n in ("CO2LO", "CO2HI") if n in names], axis=0),
    ])
    if SMOOTH > 1:
        k = np.ones(SMOOTH) / SMOOTH
        for c in range(3):
            v[:, c] = np.convolve(v[:, c], k, mode="same")
    return v, np.asarray(d["Time"], dtype=float)


def load_devc(sub, chid):
    r = _read_devc(os.path.join(FDS, sub, f"{chid}_devc.csv"), chid)
    return r


def load_suppress(chid):
    """读干预算例并按 manifest 重构 S 通道。

    S 曲线由 t_s 参数**精确构造**，不依赖任何测量 —— 这是输入侧的
    "动作"，与输出侧的"响应"（层温/气体实测）严格分开。
    """
    if not os.path.exists(SUPPRESS_MANIFEST):
        return None
    meta = json.load(open(SUPPRESS_MANIFEST, encoding="utf-8")).get(chid)
    if not meta:
        return None
    r = _read_devc(os.path.join(SUPPRESS_DIR, f"{chid}_devc.csv"), chid)
    if r is None:
        return None
    v, t = r
    ts = float(meta["t_start"])
    s = np.where(t < ts, 0.0, 1.0 - np.exp(-(t - ts) / S_DELIVERY_TAU))
    return np.column_stack([v, np.clip(s, 0.0, 1.0)]), ts


def with_s0(v3):
    """旧工况（无干预）补 S ≡ 0 列。"""
    return np.column_stack([v3, np.zeros(len(v3))])


def gather(comp):
    """收齐该舱所有工况：旧条件（S=0）+ 干预条件（S 从 manifest 重构）。"""
    sub, base, ctag = COMPARTMENTS[comp]
    cand = []          # (标签, v4, ts)  ts=None 表示无干预
    r = load_devc(sub, base)
    if r is not None:
        cand.append((f"{ctag}_q100", with_s0(r[0]), None))
    for e in EXTRA:
        r = load_devc("cond", f"{ctag}_{e}")
        if r is not None:
            cand.append((f"{ctag}_{e}", with_s0(r[0]), None))
    if os.path.exists(SUPPRESS_MANIFEST):
        for chid, meta in json.load(open(SUPPRESS_MANIFEST,
                                         encoding="utf-8")).items():
            if meta.get("tag") != ctag:
                continue
            r = load_suppress(chid)
            if r is not None:
                cand.append((chid, r[0], r[1]))

    # 去重（与 v6 同口径）：干预工况的响应形状差异大，不会误伤
    out, kept = [], []
    for lab, v, ts in cand:
        sig = (round(float(v[:, 0].max())), round(float(v[:, 2].max()), 4),
               round(float(v[-1, 0]), 1))
        if any(abs(sig[0] - k[0]) <= 2 and abs(sig[1] - k[1]) <= 5e-4
               and abs(sig[2] - k[2]) <= 2 for k in kept):
            print(f"    {lab:<22} 与已收工况重复，跳过")
            continue
        kept.append(sig)
        out.append((lab, v, ts))
    return out


def wdelta(data):
    """4 通道输入 → 3 通道增量输出。

    Y 取前 3 个物理通道（S 只进输入，不预测 —— 它由操作员决定，
    不是模型的输出对象）。增量基准取输入末行的前 3 通道。
    """
    X, Y = make_windows(data, SEQ, PRED)
    return X, Y[:, :, :3] - X[:, -1:, :3]


def fit(comp, cases, holdout):
    """留出一整个工况（含干预算例）做泛化评估，不重拟合。"""
    ho_v = dict((k, v) for k, v, _ in cases)[holdout]
    ho_ts = dict((k, ts) for k, _, ts in cases)[holdout]
    train = [v for k, v, _ in cases if k != holdout]
    tr = np.vstack(train)
    mu, sd = tr.mean(axis=0), tr.std(axis=0) + 1e-8

    Xs, Ys = [], []
    for seg in train:
        n = int(len(seg) * (1 - TAIL))
        a, b = wdelta((seg[:n] - mu) / sd)
        Xs.append(a); Ys.append(b)
    Xtr, Ytr = np.vstack(Xs), np.vstack(Ys)
    Xva, Yva = wdelta((ho_v[:int(len(ho_v) * (1 - TAIL))] - mu) / sd)
    Xte, Yte = wdelta((ho_v[int(len(ho_v) * (1 - TAIL)):] - mu) / sd)

    dsd = Ytr.reshape(-1, 3).std(axis=0) + 1e-8
    Ytr, Yva, Yte = Ytr / dsd, Yva / dsd, Yte / dsd

    torch.manual_seed(0)
    np.random.seed(0)
    m = Seq2SeqLSTM(4, HID, LAYERS, 3, SEQ, PRED, DROPOUT)
    opt = optim.Adam(m.parameters(), lr=LR)
    sch = optim.lr_scheduler.ReduceLROnPlateau(opt, "min", factor=0.5,
                                               patience=10)
    crit = nn.MSELoss()
    xt, yt = torch.FloatTensor(Xtr), torch.FloatTensor(Ytr)
    xv, yv = torch.FloatTensor(Xva), torch.FloatTensor(Yva)

    best, state, bad = float("inf"), None, 0
    for ep in range(1, EPOCHS + 1):
        m.train()
        perm = torch.randperm(len(xt))
        for i in range(0, len(xt), 64):
            idx = perm[i:i + 64]
            opt.zero_grad()
            crit(m(xt[idx]), yt[idx]).backward()
            torch.nn.utils.clip_grad_norm_(m.parameters(), 1.0)
            opt.step()
        m.eval()
        with torch.no_grad():
            vl = float(crit(m(xv), yv).detach())
        sch.step(vl)
        if vl < best - 1e-5:
            best, bad = vl, 0
            state = {k: v.clone() for k, v in m.state_dict().items()}
        else:
            bad += 1
        if bad >= 15:
            break
    m.load_state_dict(state)
    m.eval()

    # 解码回物理量（与 server decode_raw 同一套两步还原）
    with torch.no_grad():
        pred = (m(torch.FloatTensor(Xte)).numpy() * dsd
                + Xte[:, -1:, :3]) * sd[:3] + mu[:3]
        true = (Yte * dsd + Xte[:, -1:, :3]) * sd[:3] + mu[:3]
    mae = [float(np.abs(pred[..., i] - true[..., i]).mean()) for i in range(3)]

    # 干预工况的额外口径：t >= t_s 之后的窗口（干预响应段）
    ts = ho_ts
    if ts is not None:
        n = int(len(ho_v) * (1 - TAIL))
        # 评估段第 i 个窗口在完整轨迹上的起始时间 = (n + i) * STEP
        t_all = (np.arange(len(Xte)) + n) * STEP
        seg_mask = t_all >= ts
        if seg_mask.any():
            p = pred[seg_mask]; q = true[seg_mask]
            mae_s = float(np.abs(p[..., 0] - q[..., 0]).mean())
            print(f"  留出 {holdout:<22} val={best:.4f}  "
                  f"MAE 温{mae[0]:5.2f}℃  干预段温{mae_s:5.2f}℃")
            return mae[0], best
    print(f"  留出 {holdout:<22} val={best:.4f}  "
          f"MAE 温{mae[0]:5.2f}℃  CO{mae[1]*100:5.3f}%  CO2{mae[2]*100:5.3f}%")
    return mae[0], best


def refit_all(cases, out_dir, comp):
    """全部工况重拟合后落盘（v6 同策略：交付模型吃满数据）。"""
    allv = np.vstack([v for _, v, _ in cases])
    amu, asd = allv.mean(axis=0), allv.std(axis=0) + 1e-8
    Xs, Ys = [], []
    for _, seg, _ in cases:
        n = int(len(seg) * (1 - TAIL))
        a, b = wdelta((seg[:n] - amu) / asd)
        Xs.append(a); Ys.append(b)
    Xa, Ya = np.vstack(Xs), np.vstack(Ys)
    adsd = Ya.reshape(-1, 3).std(axis=0) + 1e-8
    Ya = Ya / adsd

    last = cases[-1][1]
    n = int(len(last) * (1 - TAIL))
    _, Xva, Yva = None, *wdelta((last[max(0, n - 400):n] - amu) / asd)
    Yva = Yva / adsd

    torch.manual_seed(0)
    np.random.seed(0)
    fm = Seq2SeqLSTM(4, HID, LAYERS, 3, SEQ, PRED, DROPOUT)
    fo = optim.Adam(fm.parameters(), lr=LR)
    fsch = optim.lr_scheduler.ReduceLROnPlateau(fo, "min", factor=0.5,
                                                patience=10)
    crit = nn.MSELoss()
    xa, ya = torch.FloatTensor(Xa), torch.FloatTensor(Ya)
    xvv, yvv = torch.FloatTensor(Xva), torch.FloatTensor(Yva)
    fbest, fstate = float("inf"), None
    for ep in range(1, EPOCHS + 1):
        fm.train()
        perm = torch.randperm(len(xa))
        for i in range(0, len(xa), 64):
            idx = perm[i:i + 64]
            fo.zero_grad()
            crit(fm(xa[idx]), ya[idx]).backward()
            torch.nn.utils.clip_grad_norm_(fm.parameters(), 1.0)
            fo.step()
        fm.eval()
        with torch.no_grad():
            fvl = float(crit(fm(xvv), yvv).detach())
        fsch.step(fvl)
        if fvl < fbest - 1e-5:
            fbest, fstate = fvl, {k: v.clone() for k, v in fm.state_dict().items()}

    os.makedirs(out_dir, exist_ok=True)
    torch.save({
        "model_state_dict": fstate,
        "config": {"hidden": HID, "layers": LAYERS, "num_layers": LAYERS,
                   "seq_len": SEQ, "pred_len": PRED, "dropout": DROPOUT,
                   "lr": LR, "epochs": EPOCHS, "smooth": SMOOTH},
        "target": "delta",
        "channel_mean": amu, "channel_std": asd, "delta_std": adsd,
        "step_seconds": STEP,
        "input_dim": 4, "output_dim": 3,
        # 干预通道的完整说明：推理侧据此构造 S 列
        "intervention": {
            "channel": 3, "name": "suppression",
            "delivery_tau_s": S_DELIVERY_TAU,
            "note": "S=灭火剂投放状态(0→1)；由调用方按当前灭火状态填充",
        },
        "val_loss": float(fbest),
        "n_conditions": len(cases),
        "degenerate_channels": [],
        "trained_horizon_seconds": 1800.0,
        "pipeline": "v6 配方 + 第4输入通道(灭火状态) + 干预工况 "
                    "(gen_suppression.py, t_s/tau/floor 三档)",
    }, os.path.join(out_dir, "best_model.pth"))
    print(f"  -> {os.path.relpath(out_dir, os.getcwd())}/best_model.pth "
          f"({len(cases)} 工况全量重拟合, val={fbest:.4f})")


def main():
    targets = sys.argv[1:] or list(COMPARTMENTS)
    maxfolds = int(os.environ.get("RETRAIN_V7_FOLDS", "0")) or (3 if SMOKE else 0)
    if SMOKE:
        maxfolds = min(maxfolds, 1)
    summary = []
    for comp in targets:
        print(f"\n{'=' * 68}\n  {comp}\n{'=' * 68}")
        cases = gather(comp)
        n_supp = sum(1 for _, _, ts in cases if ts is not None)
        print(f"  可用工况 {len(cases)} 条（含干预 {n_supp} 条）")
        if n_supp == 0 and not SMOKE:
            print("  ⚠ 没有任何干预工况 —— 先跑 py -3 tools\\fds_queue.py suppress")
        if len(cases) < 2:
            print("  可用工况不足，跳过")
            continue
        for k, v, ts in cases:
            tag = f"干预 t_s={ts:g}s" if ts is not None else "无干预"
            print(f"    {k:<22} {len(v)} 点  峰温 {v[:,0].max():5.0f}℃  [{tag}]")
        if maxfolds:
            errs = {}
            folds = cases if not maxfolds else cases[:maxfolds]
            # 干预工况优先留出 —— 反事实能力是本版本的核心验证
            supp_first = sorted(cases, key=lambda c: c[2] is None)
            folds = supp_first[:maxfolds] if maxfolds < len(cases) else cases
            for lab, _, _ in folds:
                e, _v = fit(comp, cases, lab)
                errs[lab] = e
        refit_all(cases, os.path.join(OUT_ROOT, comp), comp)
        summary.append((comp, len(cases), n_supp))

    if summary:
        print(f"\n{'=' * 68}\n  汇总\n{'=' * 68}")
        for comp, n, ns in summary:
            print(f"  {comp:<8} {n} 工况（含干预 {ns}）")


if __name__ == "__main__":
    main()

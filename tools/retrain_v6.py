# -*- coding: utf-8 -*-
"""生产版多工况增量目标训练（v6）。

配方来自 exp_multicond.py 的 A/B 对照实验，结论是三个要素缺一不可：

  1. **增量目标**（target=delta）
     绝对值目标下，MSE 的最优解是条件均值。单轨迹 90% 是平台段时，
     模型喂 52/73/83/92/111℃ 五种输入全部输出 253.9℃（平台温度），
     完全无响应。改成预测增量后，零增量是中性答案，模型才学到动力学。

  2. **多工况**
     A/B 对照（测试集是模型完全没见过的工况）：
         A 单工况   温度 MAE 22.00℃  相对误差 15.7%
         B 多工况   温度 MAE  3.81℃  相对误差  2.7%
     好 5.8 倍。这推翻了「多工况更不敏感」的旧结论 —— 那次测的是
     阶跃数据，模型里根本没有可学的信号。

  3. **0.5s 时间网格**
     FDS 的 DT_DEVC=0.5。seq=60/pred=60 即 30 秒输入、30 秒输出，
     30 分钟只要 60 步自回归，落在 Seq2Seq 能自回归的范围里
     （v3 的 0.1s 网格需要 18000 步，几十步就塌缩）。

验证方式：留出**一整个工况**不参与训练，训练完在它身上评估。
这正是损管实况 —— 你永远不会遇到和训练集一模一样的火。
"""
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
OUT_ROOT = os.path.join(HERE, "..", "models_v6")

STEP = 0.5
HID, LAYERS, SEQ, PRED = 64, 2, 60, 60
EPOCHS, LR, DROPOUT, SMOOTH = 45, 2e-3, 0.2, 10
TAIL = 0.15          # 每条轨迹末尾 15% 不参与训练
HOLDOUT = 0.6        # 验证用的留出工况（小数点后一位=q0x.x，最末位=0 则整条留出）

EXPECTED_POINTS = 3601   # T_END=1800s / DT_DEVC=0.5s + 1，缺一不可
STALE_SEC = 900          # 最近还在被写过 = FDS 正在往这个文件里追加

# 舱室 -> (基准算例所在目录, 基准 CHID, 条件算例的 CHID 前缀)
# 前缀必须单列：基准 CHID 带调节后缀（d8m / d4b），而 gen_conditions2.py
# 是拿它生成条件、却用去掉后缀的前缀命名条件文件（d8_* / d4_*）。
# 直接从基准 CHID 推前缀会一个条件都收不上。
COMPARTMENTS = {
    "灶炉间": ("growth", "d5", "d5"),
    "机库": ("growth", "d6", "d6"),
    "士兵住舱": ("growth", "d8m", "d8"),
    "电站间": ("growth", "d4b", "d4"),
    # 主机舱的基准算例用的是 config/compartments.json 里的权威舱段
    # 18x7.6x10.4 = 1423 m^3，不是 PyroSim 网格域的 13.1 万 m^3。
    # 详见 prep_fds_design.py 里 FORCED_BOUNDS 的说明。
    "主机舱": ("growth", "zjc", "zjc"),
}
# 参与的工况 CHID（q100 基准本身不另跑，条件算例里已有同参数版本时也可用）
EXTRA = ["q035_v100", "q070_v100", "q100_v050", "q100_v200",
         "q150_v100", "q200_v150"]


def load_devc(sub, chid):
    p = os.path.join(FDS, sub, f"{chid}_devc.csv")
    if not os.path.exists(p):
        return None
    # FDS 边跑边追加 devc.csv，中途取文件会拿到**半截轨迹**。
    # 这种样本最毒：它在增长段戛然而止，TAIL=0.15 切出来的"末段"
    # 其实是火还没烧完的中段，模型会把它当成"接下来就该降温"的规律学进去。
    # 实测机库 d6_q150_v100 跑到 2782/3601 点时峰温 296℃ —— 半截轨迹
    # 和跑完后的 352℃ 完全是两个工况，混进去等于投毒。
    n = sum(1 for _ in open(p, "rb")) - 2
    if n != EXPECTED_POINTS:
        print(f"    {chid:<18} 跳过：只跑了 {n}/{EXPECTED_POINTS} 点，"
              f"FDS 尚未结束")
        return None
    if time.time() - os.path.getmtime(p) < STALE_SEC:
        print(f"    {chid:<18} 跳过：文件仍在写入")
        return None
    d = np.genfromtxt(p, delimiter=",", names=True, skip_header=1)
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
    return v


def gather(comp):
    """收齐该舱所有可用工况，返回 [(标签, 轨迹)]。

    带**去重**：某些工况改动其实不产生任何差异。实测士兵住舱
    门宽 0.40 / 0.80 / 1.60 m 三个工况结果分毫不差（峰温 73℃ @ 1064s、
    烟气层 CO2 2.60%）—— 火相对舱室太小，烟气层由火本身决定，门的流量
    差异在 0.4 m 网格下体现不出来。三条重复样本会让那个火_sizes 在训练里
    被当成三份加权，把分布带偏，所以只留第一条。
    """
    sub, base, ctag = COMPARTMENTS[comp]
    cand = []
    v = load_devc(sub, base)
    if v is not None:
        cand.append((f"{ctag}_q100", v))
    for e in EXTRA:
        v = load_devc("cond", f"{ctag}_{e}")
        if v is not None:
            cand.append((f"{ctag}_{e}", v))

    out, kept = [], []
    for lab, v in cand:
        sig = (round(float(v[:, 0].max())), round(float(v[:, 2].max()), 4),
               round(float(v[-1, 0]), 1))
        if any(abs(sig[0] - k[0]) <= 2 and abs(sig[1] - k[1]) <= 5e-4
               and abs(sig[2] - k[2]) <= 2 for k in kept):
            print(f"    {lab:<18} 与已收工况重复（峰温/CO2/末温一致），跳过")
            continue
        kept.append(sig)
        out.append((lab, v))
    return out


def wdelta(data):
    X, Y = make_windows(data, SEQ, PRED)
    return X, Y - X[:, -1:, :]


def fit(comp, cases, holdout_label):
    """只做「留出一整个工况」的泛化评估，**不做重拟合**。

    重拟合吃全部工况，和留出哪一折无关 —— 原来每折都重拟合一次，
    4 折算出来是同一个模型，白算 3 次。现在统一在 refit_all() 里做一次。
    """
    train = [v for k, v in cases if k != holdout_label]
    test = dict(cases)[holdout_label]
    tr = np.vstack(train)
    mu, sd = tr.mean(axis=0), tr.std(axis=0) + 1e-8

    Xs, Ys = [], []
    for seg in train:
        n = int(len(seg) * (1 - TAIL))
        a, b = wdelta((seg[:n] - mu) / sd)
        Xs.append(a); Ys.append(b)
    Xtr, Ytr = np.vstack(Xs), np.vstack(Ys)
    Xva, Yva = wdelta((test[:int(len(test) * (1 - TAIL))] - mu) / sd)
    Xte, Yte = wdelta((test[int(len(test) * (1 - TAIL)):] - mu) / sd)

    dsd = Ytr.reshape(-1, 3).std(axis=0) + 1e-8
    Ytr, Yva, Yte = Ytr / dsd, Yva / dsd, Yte / dsd

    torch.manual_seed(0)
    np.random.seed(0)
    m = Seq2SeqLSTM(3, HID, LAYERS, 3, SEQ, PRED, DROPOUT)
    opt = optim.Adam(m.parameters(), lr=LR)
    sch = optim.lr_scheduler.ReduceLROnPlateau(opt, "min", factor=0.5, patience=10)
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
    with torch.no_grad():
        pred = (m(torch.FloatTensor(Xte)).numpy() * dsd
                + Xte[:, -1:, :]) * sd + mu
        true = (Yte * dsd + Xte[:, -1:, :]) * sd + mu

    mae = [float(np.abs(pred[..., i] - true[..., i]).mean()) for i in range(3)]
    print(f"  留出 {holdout_label:<18} val={best:.4f}  "
          f"MAE 温{mae[0]:5.2f}℃  CO{mae[1]*100:5.3f}%  CO2{mae[2]*100:5.3f}%")
    return mae[0], best


def refit_all(cases, out_dir, comp):
    """用**全部**工况重拟合后落盘。

    留出评估只用来量化「换一场火还准不准」，交付的模型应该吃满所有数据 ——
    多工况每舱只有几条轨迹，少一条的代价很大。
    """
    allv = np.vstack([v for _, v in cases])
    amu, asd = allv.mean(axis=0), allv.std(axis=0) + 1e-8
    Xs, Ys = [], []
    for seg in [v for _, v in cases]:
        n = int(len(seg) * (1 - TAIL))
        a, b = wdelta((seg[:n] - amu) / asd)
        Xs.append(a); Ys.append(b)
    Xa, Ya = np.vstack(Xs), np.vstack(Ys)
    adsd = Ya.reshape(-1, 3).std(axis=0) + 1e-8
    Ya = Ya / adsd

    # 验证用最后一条工况的末段（它已经进了训练，所以这个 val 只用于早停，
    # 泛化能力看上面的留一评估，不看这个数）
    last = cases[-1][1]
    n = int(len(last) * (1 - TAIL))
    _, Xva, Yva = None, *wdelta((last[max(0, n - 400):n] - amu) / asd)
    Yva = Yva / adsd

    torch.manual_seed(0)
    np.random.seed(0)
    fm = Seq2SeqLSTM(3, HID, LAYERS, 3, SEQ, PRED, DROPOUT)
    fo = optim.Adam(fm.parameters(), lr=LR)
    fsch = optim.lr_scheduler.ReduceLROnPlateau(fo, "min", factor=0.5, patience=10)
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
        "input_dim": 3, "output_dim": 3,
        "val_loss": float(fbest),
        "n_conditions": len(cases),
        "degenerate_channels": [],
        "trained_horizon_seconds": 1800.0,
        "pipeline": "0.5s 网格 + IMO FTP t^2 增长 + 上层烟气层 + 增量目标 + 多工况",
    }, os.path.join(out_dir, "best_model.pth"))
    print(f"  -> {os.path.relpath(out_dir, os.getcwd())}/best_model.pth "
          f"({len(cases)} 工况全量重拟合, val={fbest:.4f})")


def main():
    targets = sys.argv[1:] or list(COMPARTMENTS)
    # RETRAIN_V6_FOLDS 限制交叉验证折数。留一验证每折要完整训一遍，
    # 4 工况就是 4 遍，单舱要 1 小时以上。链路打通阶段先只跑 1 折，
    # 泛化能力的决定性证据已经有了（A/B 对照 22.00℃ vs 3.81℃）。
    maxfolds = int(os.environ.get("RETRAIN_V6_FOLDS", "0"))
    summary = []
    for comp in targets:
        print(f"\n{'=' * 68}\n  {comp}\n{'=' * 68}")
        cases = gather(comp)
        if len(cases) < 2:
            print(f"  可用工况只有 {len(cases)} 条，不足以做留出验证")
            continue
        for k, v in cases:
            print(f"    {k:<18} {len(v)} 点  峰温 {v[:,0].max():5.0f}℃  "
                  f"CO2层末 {v[-1,2]*100:.2f}%")
        # 留出工况：每舱轮流留出，量化「换一场火还准不准」
        errs = {}
        folds = cases if not maxfolds else cases[:maxfolds]
        for lab, _ in folds:
            e, _v = fit(comp, cases, lab)
            errs[lab] = e
        refit_all(cases, os.path.join(OUT_ROOT, comp), comp)
        summary.append((comp, len(cases), errs))

    print(f"\n{'=' * 68}\n  留出工况泛化误差汇总（温度 MAE, ℃）\n{'=' * 68}")
    for comp, n, errs in summary:
        vals = list(errs.values())
        print(f"  {comp:<8} {n} 工况  " +
              "  ".join(f"{k.split('_')[-1]}:{v:.1f}" for k, v in errs.items()))
        print(f"  {'':<8}          平均 {np.mean(vals):.2f}℃  "
              f"最差 {np.max(vals):.2f}℃")


if __name__ == "__main__":
    main()

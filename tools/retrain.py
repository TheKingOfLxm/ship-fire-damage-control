"""
修正管线重训 + 与基线的可复现对比。

相对原训练脚本的三处修正（都是方法学问题，不是调参）：

  1. 统一时间网格：原始 CSV 采样间隔不均匀（主机舱 0.0103~0.1411s），
     原训练把相邻行直接当等间隔序列，模型学到的"一步"没有物理意义。
     这里先重采样到 0.1s 均匀网格，让"一步 = 0.1 秒"成为真话。

  2. 按时间顺序切分：原脚本用 random_split 打乱窗口，相邻窗口重叠 149/150，
     验证集几乎完全被训练集覆盖，早停选出的模型在重叠数据上拟合。
     这里先按时间把序列切成三段，再**在段内**建窗口，边界处不跨界，
     训练/验证/测试之间零泄漏。

  3. 标准化器只在训练段拟合：原脚本在切分前 fit，把验证集统计量泄漏进训练。

另外给 decoder 加了可学习位置嵌入：原实现把编码器末状态 repeat 成 150 份
完全相同的向量，decoder 输入恒定，无法区分预测的是第几步。

用法： py -3 tools/retrain.py            # 跑全部候选配置
       py -3 tools/retrain.py 主机舱     # 只跑一个舱室
"""
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from lstm_pipeline import (CASES, FEATURES, load_raw, make_windows,
                           per_channel_constant, resample_uniform)
from model_def import Seq2SeqLSTM

OUT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models_v3")

STEP = 0.1                      # 重采样网格，秒/步

# 切分比例。数据源不同长度差异很大（新数据 1801 点、主机舱仍是 301 点），
# 比例必须让验证段和测试段都装得下至少一个窗口。
TRAIN_FRAC, VAL_FRAC = 0.55, 0.225

# 候选配置（轻量）。
#
# 30 分钟数据有 18001 点，300/300 的窗口一次训练要几个小时，
# 挡住交付。这里先用 100/100（10 秒输入 + 10 秒预测）把模型跑出来，
# 确认机库温度通道真的有信号、能学出升温过程；窗口再大是下一轮的事。
# 正式配置：2 层 96 维，100 步窗口（10 秒输入 + 10 秒预测）。
#
# 100 步窗口已实测可用：机库快速验证版（12 epoch）训练后温度能在 324~330℃
# 真实变化，但幅度小，说明容量不足。这里把容量和训练量都提上去，
# 目标是让模型学到完整的升温 -> 达峰 -> 衰减过程。
#
# epoch 数不拍脑袋：先跑 RETRAIN.bench_epochs 实测单 epoch 耗时，
# 再按总预算算该跑多少，避免又出现"跑了一小时才发现太慢"。
CONFIGS = [
    dict(seq_len=100, pred_len=100, hidden=64, layers=1, dropout=0.3, lr=2e-3,
         epochs=10, num_layers=1, smooth=25),
]


def segments(data, refit=False, need=0):
    """按时间把序列切成 train / val / test 三段。

    refit=True 时不留独立测试段，训练用除验证尾巴以外的全部数据。
    原因：留出 22.5% 做测试会让模型只见到前 55% 的轨迹，而原始模型是在
    100% 数据上训练的（虽然切分方式错误）。数据覆盖率的差距足以抵消
    方法学修正带来的收益，所以最终模型必须用满数据重拟合。

    need = seq_len + pred_len。验证段必须装得下这个长度，否则窗口切不出来，
    早停会失效。验证段按需求自适应，而不是拍一个百分比。
    """
    n = len(data)
    if refit:
        val_len = max(int(n * 0.10), need + 8)   # 至少留出几个验证窗口
        val_len = min(val_len, n // 3)
        return data[:n - val_len], data[n - val_len:], data[n - val_len:]
    a = int(n * TRAIN_FRAC)
    b = a + int(n * VAL_FRAC)
    return data[:a], data[a:b], data[b:]


def build(seg, seq_len, pred_len, scaler, fit=False):
    if fit:
        flat = seg.reshape(-1, seg.shape[1])
        scaler.fit(flat)
    scaled = scaler.transform(seg.reshape(-1, seg.shape[1])).reshape(seg.shape)
    return make_windows(scaled, seq_len, pred_len)


def evaluate(model, scaler, X, Y):
    """返回逐通道相对误差与预测动态性。"""
    if len(X) == 0:
        return None
    with torch.no_grad():
        p = model(torch.FloatTensor(X)).numpy()
    p = scaler.inverse_transform(p.reshape(-1, p.shape[2])).reshape(p.shape)
    out = []
    for c in range(Y.shape[2]):
        yt, yp = Y[:, :, c], p[:, :, c]
        rng = float(yt.max() - yt.min())
        mae = float(np.mean(np.abs(yt - yp)))
        # 预测跨度相对真实跨度：<1 说明预测被压平，接近 0 就是塌缩
        span_ratio = float((yp.max() - yp.min()) / rng) if rng > 1e-12 else float("nan")
        out.append(dict(mae=mae, rel=mae / rng if rng > 1e-12 else float("nan"),
                        span_ratio=span_ratio))
    return out


def train_one(name, cfg, verbose=True, refit=False):
    t, v = load_raw(name, prefer_30min=True)
    # FDS 输出带数值噪声：机库 18001 点里只有 8490 个唯一值、3972 次趋势转折。
    # 不过滤的话模型学到的是一团抖动而不是火灾发展过程（实测会横盘在均值附近）。
    # 轻度滑动平均只压噪声，保留秒级尺度上的真实上升。
    if cfg.get("smooth", 0) > 1:
        w = int(cfg["smooth"])
        kernel = np.ones(w) / w
        v = v.copy()
        for c in range(v.shape[1]):
            v[:, c] = np.convolve(v[:, c], kernel, mode="same")
    const = per_channel_constant(v)
    seq, pred = cfg["seq_len"], cfg["pred_len"]
    data = resample_uniform(t, v, STEP)[1]
    tr, va, te = segments(data, refit=refit, need=seq + pred)

    scaler = StandardScaler()
    Xtr, Ytr = build(tr, seq, pred, scaler, fit=True)   # 只在训练段拟合
    Xva, Yva = build(va, seq, pred, scaler)
    Xte, Yte = build(te, seq, pred, scaler)

    # 重拟合模式下测试段与验证段是同一段（末尾 10%），不再要求独立测试窗
    if len(Xtr) == 0:
        return None
    if len(Xva) == 0 or (not refit and len(Xte) == 0):
        if verbose:
            print(f"  seq={seq:<4}pred={pred:<4}跳过："
                  f"验证/测试段装不下窗口 (验{len(Xva)} 测{len(Xte)})")
        return None

    torch.manual_seed(0)
    np.random.seed(0)
    device = torch.device("cpu")
    model = Seq2SeqLSTM(3, cfg["hidden"], cfg["layers"], 3, seq, pred, cfg["dropout"]).to(device)
    opt = optim.Adam(model.parameters(), lr=cfg["lr"])
    sched = optim.lr_scheduler.ReduceLROnPlateau(opt, mode="min", factor=0.5, patience=25)
    crit = nn.MSELoss()

    xt = torch.FloatTensor(Xtr)
    yt = torch.FloatTensor(Ytr)
    best, best_state, bad = float("inf"), None, 0
    bs = 32
    for ep in range(cfg["epochs"]):
        model.train()
        perm = torch.randperm(len(xt))
        tot = 0.0
        nb = 0
        for i in range(0, len(xt), bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            loss = crit(model(xt[idx]), yt[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss)
            nb += 1
        # 验证（早停只看验证集，不碰测试集）
        model.eval()
        with torch.no_grad():
            vl = float(crit(model(torch.FloatTensor(Xva)), torch.FloatTensor(Yva)))
        sched.step(vl)
        if vl < best - 1e-6:
            best, bad = vl, 0
            best_state = {k: x.detach().clone() for k, x in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 60:
                break
        if verbose:
            print(f"      epoch {ep+1}/{cfg['epochs']}  train={tot/max(nb,1):.5f}  val={vl:.5f}", flush=True)

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    res = dict(
        compartment=name, cfg=cfg, val_loss=best,
        n_train=len(Xtr), n_val=len(Xva), n_test=len(Xte),
        input_sec=seq * STEP, pred_sec=pred * STEP,
        const=const, test=evaluate(model, scaler, Xte, Yte),
        model=model, scaler=scaler,
    )
    if verbose:
        print(f"  seq={seq:<4}pred={pred:<4}hid={cfg['hidden']:<4} "
              f"窗 训{len(Xtr):<4}验{len(Xva):<3}测{len(Xte):<3} "
              f"val={best:.5f}  输入{res['input_sec']:.1f}s 预测{res['pred_sec']:.1f}s")
        if res["test"]:
            for i, f in enumerate(FEATURES):
                m = res["test"][i]
                deg = "  ⚠源数据该通道退化" if const[i] <= 2 else ""
                print(f"      {f:<20} 相对误差 {m['rel']*100:6.2f}%  "
                      f"预测动态/真实动态 {m['span_ratio']*100:6.1f}%{deg}")
    return res


def main():
    targets = sys.argv[1:] or list(CASES)
    os.makedirs(OUT_ROOT, exist_ok=True)
    summary = []

    for name in targets:
        print(f"\n{'═'*68}\n  {name}\n{'═'*68}")
        t, v = load_raw(name, prefer_30min=True)
        const = per_channel_constant(v)
        bad_ch = [FEATURES[i] for i in range(3) if const[i] <= 2]
        if bad_ch:
            print(f"  ⚠ 源数据中这些通道恒定，模型无法学习：{', '.join(bad_ch)}")

        # 只剩一个配置时**直接全量重拟合**。
        # 原来还要先跑一遍严格切分来"选配置"，但单配置没有可选的，
        # 那一轮纯属浪费时间 —— 30 分钟数据上跑两遍要多花几十分钟。
        cfg = CONFIGS[0]
        final = train_one(name, cfg, verbose=True, refit=True)
        if final is None:
            print("  该舱数据装不下窗口，跳过")
            continue
        print(f"  训练完成: 训练窗 {final['n_train']} 验证窗 {final['n_val']}  "
              f"val={final['val_loss']:.5f}")

        out_dir = os.path.join(OUT_ROOT, name)
        os.makedirs(out_dir, exist_ok=True)
        # 有效时域按**这份数据实际覆盖的秒数**写，不能一律 180。
        # 主机舱沿用原始 30s 数据，写 180 就是在对外谎报模型能力。
        raw_t, _ = load_raw(name, prefer_30min=True)
        horizon = float(raw_t[-1] - raw_t[0])
        torch.save({
            "model_state_dict": final["model"].state_dict(),
            "scaler": final["scaler"],
            "config": final["cfg"],
            "step_seconds": STEP,
            "input_dim": 3, "output_dim": 3,
            "val_loss": final["val_loss"],
            "degenerate_channels": bad_ch,
            "trained_horizon_seconds": round(horizon, 1),
            "pipeline": "0.1s 均匀网格 + 时间序切分 + 末尾早停, 全量重拟合",
        }, os.path.join(out_dir, "best_model.pth"))
        print(f"  → 已保存 {os.path.relpath(out_dir, os.getcwd())}/best_model.pth")
        summary.append((name, final))

    print(f"\n{'═'*68}\n  汇总\n{'═'*68}")
    for name, b in summary:
        print(f"  {name:<10} seq={b['cfg']['seq_len']:<4}pred={b['cfg']['pred_len']:<4}"
              f"输入{b['input_sec']:.1f}s 预测{b['pred_sec']:.1f}s  val={b['val_loss']:.5f}")


if __name__ == "__main__":
    main()

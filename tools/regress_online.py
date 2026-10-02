# -*- coding: utf-8 -*-
"""端到端回归：拿真实 FDS 轨迹打 5001 服务，比对预测与真值。

为什么必须打 HTTP 而不是直接 import 服务端的类：
  服务里最容易错的就是「解码」这一层 —— 同一个模型权��，
  前向算对了但还原算错，返回的仍然是数字，看代码很难发现。
  早先 /evolve、/forecast、/projection 三个自回归端点就都绕过了
  增量还原，把 delta 当绝对值 inverse_transform，前端那条演化曲线
  （灶炉间 356→338→338→345）整个是假的。只有拿真值打一遍才看得出来。

怎么打：
  拿 FDS 真实轨迹的 [t0-60s, t0] 当历史喂进去（seq=60 @0.5s = 30 秒，
  再往前 pred=60 步是 30 秒），让它自回归往前推 300 秒。
  多个 t0 取平均，避免只测一个时刻的偶然。

判据：
  温度 MAE 记在 ℃，同时记录「物理上不可能的输出」计数 ——
  非有限值、或温度落在输入窗口温度 ±150℃ 之外、CO₂ 超过 30% 的，
  都是解码层出错的直接证据，比 MAE 更致命。
"""
import json
import os
import sys
import urllib.request

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from retrain_v6 import COMPARTMENTS, EXTRA, load_devc  # noqa: E402

BASE = "http://localhost:5001"
HORIZON = 300.0          # 服务端硬上限
SRC_DT = 0.5             # FDS devc 的采样间隔（DT_DEVC=0.5）
SEED_TIMES = [180.0, 420.0, 660.0, 900.0]   # 秒，取增长/充分发展各一段
# 分段统计：单步预测的精度和自回归外推的精度完全是两回事。
# 离线留一验证报的是「喂 30 秒真值 -> 吐 30 秒」的**单步**误差；
# 前端实际展示的是自回归推 300 秒，误差会逐轮累积。混用这两个数字
# 会让人以为服务退化，所以必须分开报。
SEGMENTS = [(0.0, 30.0), (30.0, 60.0), (60.0, 120.0), (120.0, 300.0)]


def post(path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


_CONFIG = None


def model_grid(comp):
    """从服务端问出该舱模型自己的 seq_len / step_seconds。

    这一步不能省。各舱模型的采样网格并不一样：v6 是 0.5s，
    v3 还是 0.1s。拿 0.5s 的 FDS 轨迹去喂 0.1s 模型，等于把时间轴
    压缩了 5 倍再送进去 —— 头几秒就会跑飞，报出来的 MAE 是测试台的
    错，不是模型的错（初版就把机库量成了 260℃）。
    """
    global _CONFIG
    if _CONFIG is None:
        with urllib.request.urlopen(BASE + "/api/lstm/config", timeout=30) as r:
            _CONFIG = json.loads(r.read().decode("utf-8"))["data"]
    shapes = _CONFIG.get("modelShapes", {})
    s = shapes.get(comp)
    if s is None:
        for k, v in shapes.items():
            if k in comp or comp in k:
                s = v
                break
    if s is None:
        raise SystemExit(f"✗ 服务端没有 {comp} 的 modelShapes")
    return int(s["seqLen"]), float(s["stepSeconds"])


def resample(v, src_dt, dst_dt):
    """把 FDS 轨迹重采样到模型自己的网格。

    源和目标都是规则的等间隔网格，用索引线性插值即可；只做插值不做
    降采样之外的加工，避免引入额外的信息损失。
    """
    if abs(src_dt - dst_dt) < 1e-9:
        return v
    n = int(np.floor((len(v) - 1) * src_dt / dst_dt)) + 1
    x_old = np.arange(len(v)) * src_dt
    x_new = np.arange(n) * dst_dt
    out = np.empty((n, v.shape[1]))
    for c in range(v.shape[1]):
        out[:, c] = np.interp(x_new, x_old, v[:, c])
    return out


def run(comp, url_health=True):
    sub, base_chid, ctag = COMPARTMENTS[comp]
    cases = []
    v = load_devc(sub, base_chid)
    if v is not None:
        cases.append((f"{ctag}_q100", v))
    for e in EXTRA:
        v = load_devc("cond", f"{ctag}_{e}")
        if v is not None:
            cases.append((f"{ctag}_{e}", v))
    if not cases:
        print(f"  {comp}: 没有可用轨迹")
        return

    seq, dt = model_grid(comp)
    per = errs_t = errs_co = errs_co2 = 0
    bad = 0
    ncase = 0
    seg_err = {sg: [0.0, 0] for sg in SEGMENTS}
    last_pred = []          # 每窗末值，用来判断是"误差累积"还是"压平成平台"
    last_true = []
    print(f"\n  {comp}  ({len(cases)} 工况,  seq={seq} dt={dt}s)")
    for lab, v_raw in cases:
        # 统一到模型自己的网格
        v = resample(v_raw, SRC_DT, dt)
        n = len(v)
        for t0 in SEED_TIMES:
            i0 = int(t0 / dt)
            if i0 - seq < 0 or i0 + int(HORIZON / dt) >= n:
                continue
            hist = [{"temperature": float(v[i, 0]),
                     "co": float(v[i, 1]),
                     "co2": float(v[i, 2])}
                    for i in range(i0 - seq, i0)]
            anchor = v[i0 - 1].copy()
            try:
                res = post("/api/lstm/forecast",
                           {"compartmentName": comp,
                            "historyData": hist,
                            "horizonSeconds": HORIZON})
            except Exception as e:
                print(f"    {lab} t0={t0:.0f}s 请求失败: {e}")
                bad += 1
                continue
            if res.get("code") != 200:
                print(f"    {lab} t0={t0:.0f}s 返回 {res.get('code')}: "
                      f"{res.get('message')}")
                bad += 1
                continue
            pts = res["data"]["predictions"]
            k = min(len(pts), int(HORIZON / dt))
            if k == 0:
                bad += 1
                continue
            pred = np.array([[p["temperature"], p["co"] * 1e-6, p["co2"] * 1e-6]
                             for p in pts[:k]])
            true = v[i0:i0 + k]
            per += k
            errs_t += float(np.abs(pred[:, 0] - true[:, 0]).sum())
            errs_co += float(np.abs(pred[:, 1] - true[:, 1]).sum())
            errs_co2 += float(np.abs(pred[:, 2] - true[:, 2]).sum())
            # 物理合理性：温度不能离锚点太远，CO2 不能超过 30%
            off = np.abs(pred[:, 0] - anchor[0]) > 150.0
            co2bad = pred[:, 2] > 0.30
            nan = ~np.isfinite(pred).all(axis=1)
            bad += int(off.sum() + co2bad.sum() + nan.sum())
            ncase += 1
            for j in range(k):
                h = j * dt
                for sg in SEGMENTS:
                    if sg[0] <= h < sg[1]:
                        seg_err[sg][0] += abs(pred[j, 0] - true[j, 0])
                        seg_err[sg][1] += 1
                        break
            last_pred.append(float(pred[-1, 0]))
            last_true.append(float(true[-1, 0]))

    if per == 0:
        print("    没有成功的时间窗")
        return
    print(f"    {ncase} 个时间窗 / {per} 点   "
          f"温度 MAE {errs_t/per:6.2f}℃  "
          f"CO {errs_co/per*100:.4f}%  CO2 {errs_co2/per*100:.3f}%")
    print(f"    物理不可能的输出点: {bad}")
    print("    分段温度 MAE（自回归 300 秒内）:")
    for sg in SEGMENTS:
        s, c = seg_err[sg]
        if c:
            print(f"      {sg[0]:5.0f}~{sg[1]:<4.0f}s  MAE {s/c:6.2f}℃  ({c} 点)")
    if last_pred:
        lp = np.array(last_pred)
        lt = np.array(last_true)
        print(f"    300 秒末：预测 {lp.mean():6.1f}℃(±{lp.std():.1f})  "
              f"真值 {lt.mean():6.1f}℃(±{lt.std():.1f})  "
              f"预测跨度 {lp.max()-lp.min():.1f}℃  真值跨度 {lt.max()-lt.min():.1f}℃")


def main():
    targets = sys.argv[1:] or list(COMPARTMENTS)
    try:
        with urllib.request.urlopen(BASE + "/api/lstm/health", timeout=20) as r:
            h = json.loads(r.read().decode("utf-8"))
        print(f"服务在线，设备={h.get('device')}，"
              f"舱室={h.get('available_compartments')}")
    except Exception as e:
        raise SystemExit(f"✗ 连不上 {BASE}：{e}")

    print("\n" + "=" * 68)
    print("  端到端回归（真实 FDS 轨迹 -> 5001 -> 与真值比对）")
    print("=" * 68)
    for comp in targets:
        run(comp)


if __name__ == "__main__":
    main()

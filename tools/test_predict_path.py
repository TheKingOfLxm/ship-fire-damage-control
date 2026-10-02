"""按前端 lstmPredictionService.forecastFromCurrent 的真实做法复刻一遍预测链路。

前端做的事：
  1. 取最近 N 条历史（1s 一点）
  2. 线性插值重采样到 150 步 × 0.1s
  3. 组装 {temperature, smoke, oxygen, co, co2} 对象数组
  4. POST 到 LSTM 服务 /api/lstm/predict

这里 1~4 全部照抄，用来确认「浏览器里那条预测链路」真的能跑通，
而不是只验证后端演化引擎那条链路。
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

SEQ = 150
STEP = 0.1
BASE = "http://localhost:5182"          # 经 vite proxy 取历史
LSTM = "http://localhost:5001"          # 前端直连 LSTM 的地址


def ms(value):
    """时间戳可能是 ISO 字符串也可能是毫秒数，统一成毫秒。"""
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000.0


def num(d, key, fallback):
    v = d.get(key)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return fallback
    return f if f == f else fallback          # NaN 检查


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def build_series(points):
    """复刻前端的重采样：1s 序列 -> 150 步 × 0.1s"""
    points = sorted(points, key=lambda p: p[0])
    t_end = points[-1][0]
    out = []
    for i in range(SEQ):
        target = t_end - (SEQ - 1 - i) * STEP * 1000
        lo = 0
        while lo < len(points) - 2 and points[lo + 1][0] <= target:
            lo += 1
        a_t, a = points[lo]
        b_t, b = points[min(lo + 1, len(points) - 1)]
        ta, tb = a_t, b_t
        u = (target - ta) / (tb - ta) if tb > ta else 0.0
        u = max(0.0, min(1.0, u))

        def mix(getter):
            return getter(a) + (getter(b) - getter(a)) * u

        out.append({
            "temperature": mix(lambda p: num(p, "temperature", 0)),
            "smoke": mix(lambda p: num(p, "smoke", 0)),
            "oxygen": mix(lambda p: num(p, "oxygen", 0)),
            "co": mix(lambda p: num(p, "co", 0)),
            "co2": mix(lambda p: num(p, "co2", 400)),
        })
    return out


for name, cid in [("主机舱", 1), ("士兵住舱", 4), ("机库", 5)]:
    d = get(f"{BASE}/api/fire/{cid}/history?limit=150")["data"]
    raw = [(ms(s["timestamp"]), s) for s in d["series"]]
    if len(raw) < 2:
        print(f"[{name}] 历史点不足 ({len(raw)})，跳过")
        continue

    series = build_series(raw)
    last_in = raw[-1][1]
    payload = json.dumps({
        "compartmentName": name,
        "historyData": series,
        "predictionSteps": int(15 / STEP),
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{LSTM}/api/lstm/predict",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Origin": "http://localhost:5182"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"[{name}] 预测失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        continue
    except Exception as e:
        print(f"[{name}] 预测失败: {e}")
        continue

    body = res.get("data", res)
    preds = body.get("predictions") or []
    print(f"[{name}] 输入 {len(raw)} 点(1s) -> 重采样 {len(series)} 点(0.1s) "
          f"[CO2 实测 {last_in.get('co2')} ppm]")
    if not preds:
        print(f"         无预测点: {json.dumps(body, ensure_ascii=False)[:200]}")
        continue
    t = [p["temperature"] for p in preds]
    print(f"         预测 {len(preds)} 点，温度 {t[0]:.1f} -> {t[-1]:.1f} ℃ ({t[-1]-t[0]:+.1f})")
    s = body.get("summary") or {}
    if s:
        print(f"         summary: 峰值 {s.get('maxTemperature')}℃ / 趋势 {s.get('trend')} / 风险 {s.get('riskLevel')}")

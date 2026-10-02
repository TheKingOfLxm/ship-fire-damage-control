"""把这一轮踩过的坑固化成自检。

每个条目都是**实测复现过**的，不是推测。服务端改动后跑一遍，
可以挡住已经修过又改回去的那类错。
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.environ.get("LSTM_BASE", "http://localhost:5001")
COMPARTMENTS = ["灶炉间", "机库", "士兵住舱", "电站间", "主机舱"]


def post(path, payload, timeout=90):
    r = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.load(urllib.request.urlopen(r, timeout=timeout))
    except urllib.error.HTTPError as e:
        return json.load(e)


def get(path, timeout=30):
    try:
        return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))
    except Exception as e:
        return {"ERR": str(e)}


def check(name, ok, detail=""):
    print("  %s %-46s %s" % ("✅" if ok else "❌", name, detail))
    return ok


def main():
    passed = 0
    total = 0

    print("=" * 74)
    print("  服务自检（每一项都是本轮实测踩过的坑）")
    print("=" * 74)

    h = get("/api/lstm/health")
    if h.get("ERR"):
        print(f"✗ 连不上 {BASE}：{h['ERR']}")
        return 1
    cfg = get("/api/lstm/config").get("data", {})
    shapes = cfg.get("modelShapes", {})

    # ① /evolve 越界：steps 超过模型自己的 pred_len 会 HTTP 500
    print("\n[1] /evolve 的 steps 不能超过该模型 pred_len")
    for c in COMPARTMENTS:
        total += 1
        r = post("/api/lstm/evolve",
                 {"compartmentName": c, "steps": 20,
                  "base": {"temperature": 28, "co": 0, "co2": 400}})
        pl = (shapes.get(c) or {}).get("predLen") or 0
        n = len((r.get("data") or {}).get("steps") or [])
        passed += check(f"{c} 请求 20 步 -> 返回 {n} 步 (pred_len={pl})",
                        r.get("code") == 200 and n <= pl)

    # ② 缓存键冲突：/projection 先跑会往播种缓存塞单列数组
    print("\n[2] /projection 与 /evolve 共用服务不串味（先 projection 再 evolve）")
    for c in COMPARTMENTS:
        total += 1
        post("/api/lstm/projection", {"compartmentName": c, "horizonSeconds": 120})
        r = post("/api/lstm/evolve",
                 {"compartmentName": c, "steps": 5,
                  "base": {"temperature": 28, "co": 0, "co2": 400}})
        w = ((r.get("data") or {}).get("window") or [])
        passed += check(f"{c} evolve 窗口列数 = 3",
                        r.get("code") == 200 and (not w or len(w[0]) == 3),
                        f"行数 {len(w)}")

    # ③ 网格错配：0.1s 模型读 0.5s 的 train30
    print("\n[3] 播种轨迹网格必须与模型一致")
    for c in COMPARTMENTS:
        total += 1
        st = (shapes.get(c) or {}).get("stepSeconds")
        want = 0.5 if st and abs(st - 0.5) < 1e-6 else 0.1
        r = post("/api/lstm/evolve",
                 {"compartmentName": c, "steps": 2,
                  "base": {"temperature": 28, "co": 0, "co2": 400}})
        si = ((r.get("data") or {}).get("samplingInterval"))
        passed += check(f"{c} 采样间隔 {si}s 与模型 {st}s 一致", si == st, f"期望 {st}s")

    # ④ extrapolated 不能挂在训练轨迹总长上
    print("\n[4] extrapolated 起点 = 单次前向可靠时长，不是训练轨迹总长")
    for c in COMPARTMENTS:
        total += 1
        d = (post("/api/lstm/projection",
                  {"compartmentName": c, "horizonSeconds": 300}).get("data") or {})
        a = d.get("analysis") or {}
        rh = a.get("reliableHorizonSeconds")
        th = a.get("trainedHorizonSeconds")
        passed += check(f"{c} 可靠 {rh}s <= 训练时域 {th}s",
                        rh is not None and th is not None and rh <= th)

    # ⑤ 降级名单必须真的对应 v2/v3 模型
    print("\n[5] 降级提示只标记非 v6 舱室")
    total += 1
    deg = h.get("degraded_compartments") or {}
    non_v6 = set()
    for name, sh in shapes.items():
        rh = sh.get("reliableHorizonSeconds")
        if not sh.get("target") == "delta":
            non_v6.add(name)
    ok = set(deg) <= non_v6
    passed += check(f"降级名单 {sorted(deg)} ⊆ 非增量目标 {sorted(non_v6)}", ok)

    print("\n" + "=" * 74)
    print(f"  {passed}/{total} 通过")
    print("=" * 74)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

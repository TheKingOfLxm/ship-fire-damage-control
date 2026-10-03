# -*- coding: utf-8 -*-
"""电站间工况补跑队列。

为什么单独一个队列脚本：单个 fds.exe 约占 6 GB，机器共 15.7 GB，
并行两条线就吃掉 12 GB 以上，再加就开始换页、FDS 速度反而崩。
这里自己管槽位，有空位才起下一条。

为什么是这个顺序：电站间（净容积 542 m^3）目前只有基准算例 d4b 是完整的，
一条轨迹撑不起留出工况验证 —— 留一验证至少要 2 条。按「峰值释热
0.7x / 1.0x / 1.5x / 2.0x + 门宽变体」排，先把释热跨度拉开，
四条轨迹的峰温才拉得开，留出验证才有意义。

为什么之前用 .ps1 失败：PowerShell 5.1 默认按 ANSI 读无 BOM 的 UTF-8
脚本，中文注释的字节被解成乱码直接把解析器带崩（报 fds_queue.ps1:15）。
Python 默认按 UTF-8 读，不存在这个问题。
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
COND = os.path.join(HERE, "fds", "cond")
SUPPRESS = os.path.join(HERE, "fds", "suppress")
FDS = r"C:\Program Files\PyroSim 2025\fds\fds.exe"
LOG = os.path.join(HERE, "fds_queue_log.txt")
EXPECTED_POINTS = 3601
MAX_PAR = 2

# (标签, 峰值倍数, 门宽倍数)
# 默认是电站间那一批。主机舱用同一套标签，只是基准算例不同 ——
# 传入舱室前缀即可（fds_queue.py zjc）。
QUEUE_BY_TAG = {
    "d4": ["d4_q100_v100", "d4_q070_v100", "d4_q150_v100",
           "d4_q200_v150", "d4_q100_v200", "d4_q100_v050"],
    "zjc": ["zjc_q100_v100", "zjc_q070_v100", "zjc_q150_v100",
            "zjc_q200_v150", "zjc_q100_v200", "zjc_q035_v100"],
}


def say(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def running_jobs():
    """当前还在跑的 fds.exe 数量（含别的会话起的）。"""
    return len(running_cases())


def running_cases():
    """当前正在跑的算例 CHID 集合。

    队列重启时，上一次被会话清理掐掉的父进程可能留下**孤儿** fds.exe
    —— 子进程不随父进程死。不查重就直接重排，会把同一个 CHID 起第二份，
    两个 FDS 抢同一个 devc.csv，结果是文件交错、算例必废。
    """
    names = set()
    # 只能走 CIM 拿命令行：tasklist 的 /V 不含命令行，wmic 在新版本
    # Windows 上虽然还在但已被标记弃用、行为不稳。PowerShell 单参数
    # 数组传参，避免嵌套引号被外层 shell 吃掉。
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='fds.exe'\" "
             "| ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return names
    for line in out.splitlines():
        line = line.strip().strip('"')
        # 命令行形如 `"C:\...\fds.exe" d4_q100_v100.fds`，可执行文件路径
        # 本身带引号，直接 basename 切出来是 `fds.exe" d4_q100_v100.fds`。
        # 取最后一个空白分隔的 token 才是算例名。
        tok = line.split()[-1] if line.split() else ""
        if tok.lower().endswith(".fds"):
            names.add(os.path.splitext(os.path.basename(tok))[0])
    return names


def point_count(case, workdir=COND):
    p = os.path.join(workdir, f"{case}_devc.csv")
    if not os.path.exists(p):
        return 0
    with open(p, "rb") as f:
        return sum(1 for _ in f) - 2


def main():
    env = dict(os.environ)
    env["PATH"] = (r"C:\Program Files\PyroSim 2025\fds;"
                   r"C:\Program Files\PyroSim 2025\fds\mpi;" + env.get("PATH", ""))
    tag = sys.argv[1] if len(sys.argv) > 1 else "d4"

    # 灭火干预算例（gen_suppression.py 的产物）：队列来自 manifest，
    # 顺序按"每舱先跑强扑、再中效、再弱效"交错 —— 任意时刻挂掉，
    # 已完成的舱都凑齐了部分档位，而不是一个舱跑满三档另一个舱空着。
    if tag == "suppress":
        import json
        workdir = SUPPRESS
        entries = json.load(open(os.path.join(workdir, "manifest.json"),
                                 encoding="utf-8"))
        by_variant = {}
        for chid, meta in entries.items():
            # CHID 形如 d5_s0180_t0060：去掉舱室前缀后的整段才是工况标签
            by_variant.setdefault(chid.split("_", 1)[1], []).append(chid)
        queue = []
        for variant in ("s0180_t0060", "s0300_t0150", "s0600_t0300"):
            queue.extend(sorted(by_variant.get(variant, [])))
    else:
        workdir = COND
        queue = QUEUE_BY_TAG.get(tag)
        if queue is None:
            raise SystemExit(f"✗ 未知舱室前缀 {tag}，可选 {list(QUEUE_BY_TAG) + ['suppress']}")

    os.makedirs(workdir, exist_ok=True)
    say(f"[{tag}] 队列启动，待跑 {len(queue)} 条，并发上限 {MAX_PAR}")

    todo = [c for c in queue if point_count(c, workdir) < EXPECTED_POINTS]
    skipped = [c for c in queue if c not in todo]
    if skipped:
        say(f"跳过已完成: {', '.join(skipped)}")
    if not todo:
        say("队列全部结束")
        return

    # 已经在跑的不重复起（多半是上一轮会话清理留下的孤儿），但要占着
    # 并发名额 —— 直接等它跑完会白白空掉一个槽位。
    # 注意：busy 在主循环里**动态刷新**。早先只在启动时算一次，
    # 孤儿跑完后集合过期 —— 队列要么永远空等（以为名额被占），
    # 要么对着已完成的算例再起一份。
    busy = running_cases() & set(todo)
    if busy:
        say(f"检测到已在运行（占用 {len(busy)} 个名额）: "
            f"{', '.join(sorted(busy))}")

    # 并发跑：每个 fds.exe 约 6 GB，2 条约 12.6 GB，机器 15.7 GB，
    # 再加训练进程也还剩得住。早先串行跑，一条 90 分钟、剩 5 条要 7.5 小时。
    live = {}          # case -> Popen
    while todo or live:
        # 收割已结束的
        for case, p in list(live.items()):
            if p.poll() is not None:
                n = point_count(case, workdir)
                if p.returncode == 0 and n >= EXPECTED_POINTS:
                    say(f"{case} 完成，{n} 点")
                else:
                    say(f"{case} 异常结束 rc={p.returncode}，"
                        f"只有 {n}/{EXPECTED_POINTS} 点")
                del live[case]

        # 孤儿/外部进程的占用要实时看，不能用启动时的快照
        busy = running_cases() - set(live.keys())

        while todo and len(live) + len(busy) < MAX_PAR:
            case = todo[0]
            # 孤儿可能在等待期间把这条例跑完了，起之前再验一次点数
            if point_count(case, workdir) >= EXPECTED_POINTS:
                say(f"{case} 已由外部进程完成，跳过")
                todo.pop(0)
                continue
            if not os.path.exists(os.path.join(workdir, f"{case}.fds")):
                say(f"{case} 跳过：算例文件不存在")
                todo.pop(0)
                continue
            todo.pop(0)
            say(f"启动 {case}（待跑 {len(todo)} 条, 在跑 {len(live) + 1} 条）")
            live[case] = subprocess.Popen([FDS, f"{case}.fds"],
                                         cwd=workdir, env=env)
            time.sleep(5)      # 让 FDS 先把文件建起来再起下一条

        if live or todo:
            time.sleep(20)

    say("队列全部结束")


if __name__ == "__main__":
    main()

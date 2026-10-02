"""并行跑多个 30 分钟 FDS 仿真。

每个算例一个子进程，OMP 线程数按 CPU 核数分配。
用法： py -3 run_fds_long.py <case1> <case2> ...
（case 指去掉 .fds 后缀的文件名，脚本在 tools/fds 下找）
"""
import os
import subprocess
import sys
import time

FDS_DIR = r"C:\Program Files\PyroSim 2025\fds"
FDS = os.path.join(FDS_DIR, "fds.exe")
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fds")


def child(case, threads):
    env = dict(os.environ)
    env["PATH"] = FDS_DIR + os.pathsep + os.path.join(FDS_DIR, "mpi") + os.pathsep + env["PATH"]
    env["OMP_NUM_THREADS"] = str(threads)
    t0 = time.time()
    print(f"── {case} 启动  threads={threads}", flush=True)
    p = subprocess.run([FDS, case + ".fds"], cwd=HERE, env=env,
                       capture_output=True, text=True, errors="replace")
    print(f"── {case} 结束  用时 {(time.time()-t0)/60:.1f} 分钟  rc={p.returncode}", flush=True)
    for l in (p.stdout or "").splitlines():
        if "ERROR(" in l or "STOP" in l:
            print("   " + l.strip(), flush=True)
    return p.returncode


if __name__ == "__main__":
    cases = sys.argv[1:]
    if not cases:
        print("用法: py -3 run_fds_long.py <case1> [case2 ...]", file=sys.stderr)
        raise SystemExit(1)
    if len(cases) == 1:
        raise SystemExit(child(cases[0], max(1, os.cpu_count() or 4)))
    # MPI 对每个进程可用端口/共享内存有硬限制，算例一多就 rc=47 初始化失败。
    # 实测 5 个算例并行必挂，2~3 个才稳。这里保守限制并发数，
    # 超出的排队串行跑。
    MAX_PARALLEL = 3
    threads = max(1, (os.cpu_count() or 4) // min(len(cases), MAX_PARALLEL))
    print(f"CPU={os.cpu_count()}核  {len(cases)} 个算例，"
          f"并发上限 {MAX_PARALLEL}，每例 {threads} 线程", flush=True)

    pending = list(cases)
    procs = []
    while pending or procs:
        while pending and len(procs) < MAX_PARALLEL:
            c = pending.pop(0)
            p = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), c],
                env=dict(os.environ, OMP_NUM_THREADS=str(threads)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace")
            procs.append((c, p))
        time.sleep(2)
        for item in list(procs):
            c, p = item
            if p.poll() is not None:
                out, _ = p.communicate()
                print(out, flush=True)
                if p.returncode != 0:
                    print(f"   ⚠ {c} 返回码 {p.returncode}", flush=True)
                procs.remove(item)
    print("全部完成", flush=True)

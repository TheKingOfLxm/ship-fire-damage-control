"""后台启动 FDS 仿真。

不放在 PowerShell 里跑循环，是因为 `& $var` 在循环里的路径解析出过问题，
表现为命令静默失败、连 .out 都不生成，排查很费时间。
Python 直接 subprocess，行为可预期。
"""
import os
import subprocess
import sys
import time

FDS_DIR = r"C:\Program Files\PyroSim 2025\fds"
FDS = os.path.join(FDS_DIR, "fds.exe")


def run(case, cwd):
    env = dict(os.environ)
    env["PATH"] = FDS_DIR + os.pathsep + os.path.join(FDS_DIR, "mpi") + os.pathsep + env["PATH"]
    env["OMP_NUM_THREADS"] = "4"
    t0 = time.time()
    print(f"── {case}  开始 {time.strftime('%H:%M:%S')}", flush=True)
    p = subprocess.run([FDS, case + ".fds"], cwd=cwd, env=env,
                       capture_output=True, text=True, errors="replace")
    tail = [l for l in (p.stdout or "").splitlines() if "STOP" in l or "ERROR(" in l]
    print(f"── {case}  结束 {time.strftime('%H:%M:%S')}  用时 {time.time()-t0:.0f}s", flush=True)
    for l in tail[:2]:
        print("   " + l.strip(), flush=True)
    return p.returncode


if __name__ == "__main__":
    cwd = sys.argv[1]
    for case in sys.argv[2:]:
        run(case, cwd)
    print("全部完成", flush=True)

"""调试 rmtree_force 为何失效（P0.5 加固后 tests 目录仍残留）。"""
import os
import shutil
import subprocess
import tempfile
import traceback

ROOT = os.path.abspath("tmp")


def probe(label, d):
    exists = os.path.exists(d)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Test-Path -LiteralPath '{d}'"],
            capture_output=True, timeout=10)
        print(f"{label}: os.path.exists={exists} Test-Path={r.stdout!r} rc={r.returncode} stderr={r.stderr!r}")
    except Exception as e:
        print(f"{label}: probe except {e}")


def rmtree_force(path):
    shutil.rmtree(path, ignore_errors=True)
    probe("after shutil", path)
    if not os.path.exists(path):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Test-Path -LiteralPath '{path}'"],
                capture_output=True, timeout=10)
            if r.stdout.strip() != b"True":
                return
        except Exception:
            pass
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Remove-Item -LiteralPath '{path}' -Recurse -Force -ErrorAction Stop"],
            capture_output=True, timeout=20)
        print("Remove-Item rc:", r.returncode, "stderr:", r.stderr)
    except Exception as e:
        print("Remove-Item except:", e)


try:
    d = tempfile.mkdtemp(prefix="dbg_rm_", dir=ROOT)
    print("created:", d)
    # 造点内容
    with open(os.path.join(d, "x.txt"), "w") as f:
        f.write("hi")
    os.makedirs(os.path.join(d, "sub"))
    rmtree_force(d)
    print("final exists:", os.path.exists(d))
except Exception:
    traceback.print_exc()

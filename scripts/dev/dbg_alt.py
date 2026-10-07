import os
import subprocess
import tempfile
import time

ROOT = os.path.abspath("tmp")

def mk(name):
    d = tempfile.mkdtemp(prefix=name, dir=ROOT)
    with open(os.path.join(d, "x.txt"), "w") as f:
        f.write("hi")
    os.makedirs(os.path.join(d, "sub"))
    with open(os.path.join(d, "sub", "y.txt"), "w") as f:
        f.write("yo")
    return d

# cmd rd /s /q
d1 = mk("dbg_cmdrd_")
r = subprocess.run(["cmd", "/c", "rd", "/s", "/q", f'"{d1}"'],
                   capture_output=True, timeout=30)
print("cmd rd rc:", r.returncode, "exists:", os.path.exists(d1))
time.sleep(1)
print("cmd rd after 1s:", os.path.exists(d1))

# os.rename 可靠性
d2 = mk("dbg_ren_")
dst = d2 + ".trash-x"
try:
    os.rename(d2, dst)
    print("os.rename rc ok, src exists:", os.path.exists(d2), "dst exists:", os.path.exists(dst))
except Exception as e:
    print("os.rename except:", e)

# PowerShell Move-Item
d3 = mk("dbg_move_")
dst3 = d3 + ".trash-y"
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     f"Move-Item -LiteralPath '{d3}' -Destination '{dst3}' -Force"],
    capture_output=True, timeout=30)
print("Move-Item rc:", r.returncode, "src exists:", os.path.exists(d3), "dst exists:", os.path.exists(dst3))

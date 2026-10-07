import os
import subprocess
import tempfile
import time

ROOT = os.path.abspath("tmp")

def try_ri(d):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Remove-Item -LiteralPath '%s' -Recurse -Force -ErrorAction Stop" % d],
        capture_output=True, timeout=30)
    return r.returncode, r.stderr.decode(errors="replace")[:150]

# 立即删
d1 = tempfile.mkdtemp(prefix="dbg_imm_", dir=ROOT)
with open(os.path.join(d1, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(d1, "sub"))
with open(os.path.join(d1, "sub", "y.txt"), "w") as f:
    f.write("yo")
rc, err = try_ri(d1)
print("immediate rc:", rc, "exists:", os.path.exists(d1), err)

# 等待 3s 删
d2 = tempfile.mkdtemp(prefix="dbg_wait_", dir=ROOT)
with open(os.path.join(d2, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(d2, "sub"))
with open(os.path.join(d2, "sub", "y.txt"), "w") as f:
    f.write("yo")
time.sleep(3)
rc2, err2 = try_ri(d2)
print("wait3 rc:", rc2, "exists:", os.path.exists(d2), err2)

# 关键：y.txt 删除本身是否成功（单独删文件）
d3 = tempfile.mkdtemp(prefix="dbg_f_", dir=ROOT)
fp = os.path.join(d3, "y.txt")
with open(fp, "w") as f:
    f.write("yo")
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Remove-Item -LiteralPath '%s' -Force -ErrorAction Stop" % fp],
    capture_output=True, timeout=10)
print("file remove rc:", r.returncode, "file exists:", os.path.exists(fp), r.stderr.decode(errors="replace")[:150])

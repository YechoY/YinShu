import os
import subprocess
import tempfile
import time

ROOT = os.path.abspath("tmp")

d = tempfile.mkdtemp(prefix="dbg_delay_", dir=ROOT)
with open(os.path.join(d, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(d, "sub"))
with open(os.path.join(d, "sub", "y.txt"), "w") as f:
    f.write("yo")

# 立即删除 → 失败预期
r1 = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[System.IO.Directory]::Delete('%s', $true)" % d],
    capture_output=True, timeout=30)
print("immediate rc:", r1.returncode, "exists:", os.path.exists(d))

# 等待 2s 再删
time.sleep(2)
r2 = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[System.IO.Directory]::Delete('%s', $true)" % d],
    capture_output=True, timeout=30)
print("after 2s rc:", r2.returncode, "exists:", os.path.exists(d))

# 重试 3 次每次 1s
d2 = tempfile.mkdtemp(prefix="dbg_retry_", dir=ROOT)
with open(os.path.join(d2, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(d2, "sub"))
with open(os.path.join(d2, "sub", "y.txt"), "w") as f:
    f.write("yo")
for i in range(3):
    r3 = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[System.IO.Directory]::Delete('%s', $true)" % d2],
        capture_output=True, timeout=30)
    print("retry", i, "rc:", r3.returncode, "exists:", os.path.exists(d2))
    if r3.returncode == 0:
        break
    time.sleep(1)

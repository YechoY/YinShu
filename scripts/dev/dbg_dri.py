import os
import subprocess
import tempfile

ROOT = os.path.abspath("tmp")

d = tempfile.mkdtemp(prefix="dbg_dri_", dir=ROOT)
with open(os.path.join(d, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(d, "sub"))
with open(os.path.join(d, "sub", "y.txt"), "w") as f:
    f.write("yo")

r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Remove-Item -LiteralPath '%s' -Recurse -Force -ErrorAction Stop" % d],
    capture_output=True, timeout=30)
print("direct Remove-Item rc:", r.returncode, "exists:", os.path.exists(d))
print("stderr:", r.stderr.decode(errors="replace")[:200])
# 再验证 Test-Path（PowerShell 视角）
r2 = subprocess.run(
    ["powershell", "-NoProfile", "-Command", "Test-Path -LiteralPath '%s'" % d],
    capture_output=True, timeout=10)
print("Test-Path:", r2.stdout)

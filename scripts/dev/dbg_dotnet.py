"""验证 .NET Directory.Delete 递归删除在 PowerShell 5.1 下可靠。"""
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.abspath("tmp")
d = tempfile.mkdtemp(prefix="dbg_dotnet_", dir=ROOT)
with open(os.path.join(d, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(d, "sub"))
with open(os.path.join(d, "sub", "y.txt"), "w") as f:
    f.write("yo")
# 模拟假成功残留：shutil 假删
shutil.rmtree(d, ignore_errors=True)
print("after shutil exists:", os.path.exists(d))
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[System.IO.Directory]::Delete('%s', $true)" % d],
    capture_output=True, timeout=30)
print("dotnet delete rc:", r.returncode, "stderr:", r.stderr)
print("final exists:", os.path.exists(d))

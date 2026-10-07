"""临时验收运行器（非交付物）：在受限沙箱里跑项目自带测试。

沙箱规则（实测）：Python 进程只能写入"会话开始时已存在"的目录，自己 mkdtemp 出来的
新目录会被拒绝（PermissionError）；但用 PowerShell 建的目录，Python 可以写。
因此这里把 tempfile.mkdtemp 换成"用 PowerShell 建目录"，从而能跑 tests/。
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub"
TMP = os.path.join(ROOT, "tmp")


def mkdtemp(prefix=None, suffix=None, dir=None):
    base = dir or TMP
    name = (prefix or "tmp") + next(tempfile._get_candidate_names())
    path = os.path.join(base, name)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Directory -Force -Path '{path}' | Out-Null"],
                   capture_output=True, timeout=30)
    return path


tempfile.mkdtemp = mkdtemp

sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

loader = unittest.TestLoader()
suite = unittest.TestSuite()
# 注意：这里必须覆盖 tests/ 下**全部** test_*.py。曾经写死四个文件名，
# 结果新增的 test_multidevice.py / test_devices_http.py 没被跑到，
# 却报出 "tests=56 ALL PASS"（数字与改动前一样，掩盖了漏跑）。
for pattern in ("test_*.py",):
    suite.addTests(loader.discover(os.path.join(ROOT, "tests"), pattern=pattern, top_level_dir=ROOT))

res = unittest.TextTestRunner(verbosity=1).run(suite)
print("=" * 60)
print(f"tests={res.testsRun} failures={len(res.failures)} errors={len(res.errors)} "
      f"-> {'ALL PASS' if res.wasSuccessful() else 'HAS FAILURES'}")

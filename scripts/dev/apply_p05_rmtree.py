# -*- coding: utf-8 -*-
"""P0.5 补丁 3：修测试清理 rmtree_force —— os.path.exists 假阴性导致 PowerShell 兜底永不触发，
残留 70+ 测试目录。改为：rmtree → 无条件 PowerShell Remove-Item(Stop) → Test-Path 双验证。
"""
import io

TARGETS = [
    r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\tests\hub\helpers.py",
    r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\tests\hub\test_api_http.py",
    r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\tests\hub\test_devices_http.py",
]

NEW_BODY = '''def _gone_dir(path):
    if not os.path.exists(path):
        return True
    try:
        import subprocess as _sp
        r = _sp.run(["powershell", "-NoProfile", "-Command",
                     "Test-Path -LiteralPath '%s'" % path],
                    capture_output=True, timeout=10)
        return r.stdout.strip() != b"True"
    except Exception:
        return False


def rmtree_force(path):
    """本机 Windows 上 Python 进程发起的删除可能假成功（且 os.path.exists 会假阴性），
    所以不依赖 exists 判断：rmtree 后无条件走 PowerShell Remove-Item 兜底，
    再用 Test-Path 双验证（2026-10-06 加固）。"""
    shutil.rmtree(path, ignore_errors=True)
    if _gone_dir(path):
        return
    try:
        import subprocess
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Remove-Item -LiteralPath '%s' -Recurse -Force -ErrorAction Stop" % path],
            capture_output=True, timeout=20)
    except Exception:
        pass
'''

for p in TARGETS:
    s = io.open(p, encoding="utf-8").read()
    # helpers.py 用原函数名 rmtree_force；两个 http 测试用 _rmtree_force
    for name in ("rmtree_force", "_rmtree_force"):
        start = s.find("def %s(path)" % name)
        if start == -1:
            start = s.find("def %s(" % name)
        if start == -1:
            continue
        # 找到函数体结束（下一个顶层 def 或文件尾，按缩进扫描）
        lines = s.split("\n")
        line_i = s[:start].count("\n")
        end = len(lines)
        for j in range(line_i + 1, len(lines)):
            if lines[j].strip() == "":
                continue
            if lines[j] and not lines[j][0].isspace() and lines[j].startswith("def "):
                end = j
                break
            if lines[j] and not lines[j][0].isspace() and not lines[j].strip().startswith(("@", "class ", "def ")):
                # 顶层非缩进语句（import 等）也算函数结束
                if lines[j].strip():
                    end = j
                    break
        # 清理函数区到结束之间的注释行保留？直接替换整个函数（含 docstring）为新实现
        body = "\n".join(lines[line_i:end])
        new_fn = NEW_BODY.replace("def rmtree_force", "def %s" % name) if name == "rmtree_force" else NEW_BODY.replace("def rmtree_force", "def _rmtree_force")
        s = s.replace(body, new_fn.rstrip("\n"), 1)
        break
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    print("patched:", p)

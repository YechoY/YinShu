# 音枢（Yinshu）Windows 一键启动脚本
# 用法（在项目根目录的 PowerShell 里）：
#   .\run.ps1                          # 默认 127.0.0.1:8000
#   .\run.ps1 --host 0.0.0.0           # 手机客户端局域网联调
#   .\run.ps1 --port 9000              # 自定义端口
# 首次运行会自动安装 uv 并配好依赖，之后每次直接启动。
# 若提示执行策略限制，先执行一次：
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

# ---- 1. 确保 uv 存在（没有就自动装） ----
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[yinshu] 未找到 uv，正在自动安装..." -ForegroundColor Cyan
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # 安装器把 uv 装到用户目录，当前会话可能还没进 PATH，手动补上
    $uvPath = "$env:USERPROFILE\.local\bin"
    if (Test-Path $uvPath) { $env:Path = "$uvPath;$env:Path" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "[yinshu] uv 安装完成，但需要重开一个终端再运行 .\run.ps1" -ForegroundColor Yellow
        exit 1
    }
}

# ---- 2. 同步依赖（首次 clone 后自动装齐；有锁文件用 --frozen 保证可复现） ----
Write-Host "[yinshu] 检查依赖..." -ForegroundColor Cyan
$ErrorActionPreference = "Continue"     # uv 会往 stderr 打进度，PS5.1 下别当异常
uv sync --frozen
if ($LASTEXITCODE -ne 0) { uv sync }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[yinshu] 依赖安装失败，请检查网络后重试" -ForegroundColor Red
    exit 1
}
$ErrorActionPreference = "Stop"

# ---- 3. 启动服务（参数原样透传） ----
Write-Host "[yinshu] 启动音枢..." -ForegroundColor Green
uv run yinshu @args

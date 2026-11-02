<#
  start_m6_stack.ps1 - one-click bootstrap for M6 infrastructure, then run validation.

  Order of preference per component (anything already listening on its port is skipped):
    1) LiteLLM  127.0.0.1:4000  - pip proxy; pass -Key <dashscope-key> for real generation
    2) PG       127.0.0.1:5432  - docker compose, else native pg_ctl (D:\pgsql + D:\pgdata)
    3) Object store 127.0.0.1:9000 - docker minio if image present,
                                     else S3-compatible moto server (license-free fallback)

  Then runs backend\scripts\validate_m6.py and prints PASS/SKIP/FAIL per component.

  Usage (PowerShell):
    cd D:\enterprise-ai-platform\backend
    .\scripts\start_m6_stack.ps1 -Key "sk-xxxxxxxx"     # bootstrap + validate
    .\scripts\start_m6_stack.ps1 -SkipValidate          # only start services
    .\scripts\start_m6_stack.ps1 -Stop                  # stop everything

  Logs: D:\enterprise-ai-platform\logs\
  ASCII only on purpose (PowerShell 5.1 reads .ps1 as ANSI when there is no BOM).
#>
param(
    [string]$Key = $env:DASHSCOPE_API_KEY,
    [string]$Model = "qwen-plus",
    [switch]$NoDocker,
    [switch]$SkipValidate,
    [switch]$Stop
)

$ErrorActionPreference = "Continue"

$Root       = "D:\enterprise-ai-platform"
$Backend    = Join-Path $Root "backend"
$Python     = Join-Path $Backend ".venv\Scripts\python.exe"
$LitellmExe = Join-Path $Backend ".venv\Scripts\litellm.exe"
$LitellmCfg = "app\services\model_gateway\litellm_config.yaml"
$LogDir     = Join-Path $Root "logs"
$PgsqlBin   = "D:\pgsql\bin"
$PgData     = "D:\pgdata"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Windows PowerShell 5.1 quirk: if both HTTPS_PROXY and https_proxy exist,
# Start-Process throws "Item has already been added. Key in dictionary".
# Drop the lowercase duplicate (uppercase one keeps proxy support).
foreach ($n in @("https_proxy", "http_proxy", "no_proxy", "all_proxy")) {
    $upper = $n.ToUpper()
    if ((Get-Item "Env:$n" -ErrorAction SilentlyContinue) -and (Get-Item "Env:$upper" -ErrorAction SilentlyContinue)) {
        Remove-Item "Env:$n" -Force -ErrorAction SilentlyContinue
    }
}

function Say([string]$m) { Write-Host $m }

function Test-Port([int]$Port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $t = $c.ConnectAsync("127.0.0.1", $Port)
        if ($t.Wait(700) -and $c.Connected) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        $c.Dispose()
    }
}

function Wait-Port([int]$Port, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Show-LogTail([string]$Path, [int]$Lines = 8) {
    if (Test-Path $Path) {
        Say "      --- tail $Path ---"
        Get-Content $Path -Tail $Lines | ForEach-Object { Say "      $_" }
    }
}

# ---------------------------------------------------------------- stop mode
if ($Stop) {
    Say "Stopping M6 services ..."
    Get-Process litellm, minio -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*moto.server*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (Test-Path (Join-Path $PgsqlBin "pg_ctl.exe")) {
        & (Join-Path $PgsqlBin "pg_ctl.exe") -D $PgData stop -m fast 2>&1 | Out-Null
    }
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Push-Location $Root; docker compose stop postgres minio 2>&1 | Out-Null; Pop-Location
    }
    Say "Done. Ports 4000/5432/9000 should be free now."
    exit 0
}

if (-not (Test-Path $Python)) {
    Say "ERROR: venv python not found at $Python"
    exit 1
}

$hasDocker = $false
if (-not $NoDocker -and (Get-Command docker -ErrorAction SilentlyContinue)) {
    & docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $hasDocker = $true }
}

Say "==============================================================="
Say " M6 stack bootstrap   (docker engine usable: $(if ($hasDocker) {'yes'} else {'no'}))"
Say "==============================================================="

# ------------------------------------------------------------ 1) LiteLLM
Say ""
Say "[1/3] LiteLLM  ->  127.0.0.1:4000"
if (Test-Port 4000) {
    Say "      already listening - skip"
} elseif (-not (Test-Path $LitellmExe)) {
    Say "      litellm.exe missing. Install it first:"
    Say "      `"$Python`" -m pip install `"litellm[proxy]`""
} else {
    if ([string]::IsNullOrWhiteSpace($Key)) {
        Say "      WARN: no DASHSCOPE_API_KEY -> LLM call will fall back to stub"
    } elseif ($Key -match '[^\x20-\x7E]') {
        Say "      WARN: key has non-ASCII characters (Chinese?) -> will fail with 500"
    } elseif ($Key -notmatch '^sk-[A-Za-z0-9]{8,}$') {
        Say "      NOTE: key format differs from the usual Bailian API-KEY (sk- + 32 alnum)."
        Say "            If generation fails with 401/403, re-copy it from bailian.console.aliyun.com -> API-KEY."
    }
    $env:DASHSCOPE_API_KEY = $Key
    $env:LLM_DEFAULT_MODEL = $Model
    $env:LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY = "true"
    $launchErr = $null
    try {
        Start-Process -FilePath $LitellmExe `
            -ArgumentList @("--config", $LitellmCfg, "--host", "127.0.0.1", "--port", "4000") `
            -WorkingDirectory $Backend `
            -RedirectStandardOutput (Join-Path $LogDir "litellm.out.log") `
            -RedirectStandardError  (Join-Path $LogDir "litellm.err.log") `
            -WindowStyle Hidden -ErrorAction Stop
    } catch {
        $launchErr = $_.Exception.Message
    }
    if ($launchErr) {
        Say "      cannot launch from this shell: $launchErr"
        if ($launchErr -like "*Key in dictionary*") {
            Say "      reason: this shell has duplicate env vars (HTTP_PROXY/http_proxy)."
            Say "      fix   : open a FRESH PowerShell window (Start menu) and run the script there."
        } else {
            Show-LogTail (Join-Path $LogDir "litellm.err.log")
        }
    } elseif (Wait-Port 4000 60) {
        Say "      started (log: logs\litellm.err.log)"
    } else {
        Say "      FAILED to open port 4000"
        Show-LogTail (Join-Path $LogDir "litellm.err.log")
    }
}

# ------------------------------------------------------------ 2) PostgreSQL
Say ""
Say "[2/3] PostgreSQL  ->  127.0.0.1:5432"
if (Test-Port 5432) {
    Say "      already listening - skip"
} else {
    $ok = $false
    if ($hasDocker) {
        Say "      docker compose up -d postgres"
        Push-Location $Root
        docker compose up -d postgres 2>&1 | ForEach-Object { Say "      $_" }
        Pop-Location
        $ok = Wait-Port 5432 60
    }
    if (-not $ok) {
        $pgExe = Join-Path $PgsqlBin "postgres.exe"
        if ((Test-Path $pgExe) -and (Test-Path $PgData)) {
            # 上次异常退出会残留 postmaster.pid，postgres 会拒绝启动
            $pidFile = Join-Path $PgData "postmaster.pid"
            if (Test-Path $pidFile) {
                $stale = (Get-Content $pidFile -TotalCount 1) -as [int]
                if (-not (Get-Process -Id $stale -ErrorAction SilentlyContinue)) {
                    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
                    Say "      removed stale postmaster.pid"
                }
            }
            # 直接用 postgres.exe：pg_ctl start 的输出管道会被子进程继承，
            # 在 PowerShell 里 `| Out-Null` 会一直等 EOF 而看起来"卡死"（服务其实已起）。
            Say "      native postgres.exe start (D:\pgsql + D:\pgdata)"
            try {
                Start-Process -FilePath $pgExe -ArgumentList @("-D", $PgData, "-p", "5432") `
                    -RedirectStandardError (Join-Path $LogDir "pg.err.log") `
                    -WindowStyle Hidden -ErrorAction Stop
                $ok = Wait-Port 5432 40
            } catch {
                Say "      cannot launch from this shell: $($_.Exception.Message)"
                Say "      fix: open a FRESH PowerShell window and run the script there"
            }
        } elseif (-not (Test-Path $PgData)) {
            Say "      native PG not initialized. One-time setup (see M6_VALIDATION.md appendix):"
            Say "        download postgresql-16.x-windows-x64-binaries.zip, unzip to D:\pgsql"
            Say "        D:\pgsql\bin\initdb.exe -D D:\pgdata -U app --auth=trust -E UTF8 --locale=C"
        }
    }
    if ($ok) {
        Say "      up"
    } else {
        Say "      NOT available -> PG check will SKIP"
    }
}

# ------------------------------------------------------------ 3) Object store
Say ""
Say "[3/3] Object store  ->  127.0.0.1:9000"
if (Test-Port 9000) {
    Say "      already listening - skip"
} else {
    $ok = $false
    if ($hasDocker -and (& docker images -q minio/minio:latest 2>$null)) {
        Say "      docker compose up -d minio (local image present)"
        Push-Location $Root
        docker compose up -d minio 2>&1 | ForEach-Object { Say "      $_" }
        Pop-Location
        $ok = Wait-Port 9000 45
    }
    if (-not $ok) {
        Say "      no MinIO available -> S3-compatible moto server (license-free, same API)"
        & $Python -c "import moto" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Say "      installing moto[server] ..."
            & $Python -m pip install -q "moto[server]" 2>&1 | Out-Null
        }
        try {
            Start-Process -FilePath $Python `
                -ArgumentList @("-m", "moto.server", "-H", "127.0.0.1", "-p", "9000") `
                -WorkingDirectory $Backend `
                -RedirectStandardOutput (Join-Path $LogDir "s3.out.log") `
                -RedirectStandardError  (Join-Path $LogDir "s3.err.log") `
                -WindowStyle Hidden -ErrorAction Stop
            $ok = Wait-Port 9000 40
        } catch {
            Say "      cannot launch from this shell: $($_.Exception.Message)"
            Say "      fix: open a FRESH PowerShell window and run the script there"
        }
        if ($ok) { Say "      S3-compatible endpoint up on 9000 (moto)" }
    }
    if (-not $ok) {
        Say "      NOT available -> object store check will SKIP"
        Show-LogTail (Join-Path $LogDir "s3.err.log")
    }
}

# ------------------------------------------------------------ validate
Say ""
Say "==============================================================="
Say (" ports now:  4000={0}  5432={1}  9000={2}" -f (Test-Port 4000), (Test-Port 5432), (Test-Port 9000))
Say "==============================================================="

if ($SkipValidate) {
    Say "Services started. Run: `"$Python`" scripts\validate_m6.py"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($env:LLM_DEFAULT_MODEL)) { $env:LLM_DEFAULT_MODEL = $Model }
Push-Location $Backend
& $Python (Join-Path $Backend "scripts\validate_m6.py")
$code = $LASTEXITCODE
Pop-Location
exit $code

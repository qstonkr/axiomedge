# GS Edge Model Server 설치 스크립트 (Windows)
#
# Usage:
#   $env:STORE_ID="gangnam-01"
#   $env:EDGE_API_KEY="your-key"
#   $env:MANIFEST_URL="https://s3.../manifest.json"
#   $env:CENTRAL_API_URL="https://knowledge-api.gs.internal"
#   .\install.ps1

$ErrorActionPreference = "Stop"

# 필수 환경변수 체크
if (-not $env:STORE_ID) { throw "STORE_ID is required" }
if (-not $env:MANIFEST_URL) { throw "MANIFEST_URL is required" }
if (-not $env:CENTRAL_API_URL) { throw "CENTRAL_API_URL is required" }

$EdgeHome = "C:\edge-model"
$EdgePort = if ($env:EDGE_PORT) { $env:EDGE_PORT } else { "8080" }

Write-Host "[install] Installing GS Edge Model Server..."
Write-Host "  Store: $($env:STORE_ID)"
Write-Host "  Home:  $EdgeHome"

# 1. manifest 가져오기
$manifest = Invoke-RestMethod -Uri $env:MANIFEST_URL
$platform = "windows-amd64"
$appUrl = $manifest.app_downloads.$platform.url
$modelUrl = $manifest.download_url

if (-not $appUrl) { throw "No app download for platform $platform" }

# 2. 디렉토리 생성
foreach ($dir in @("current-app", "models\current", "logs", "staging")) {
    New-Item -ItemType Directory -Force -Path "$EdgeHome\$dir" | Out-Null
}

# 3. 바이너리 다운로드
Write-Host "[install] Downloading app binary..."
Invoke-WebRequest -Uri $appUrl -OutFile "$EdgeHome\current-app\edge-server.exe"

# 4. 모델 다운로드
if ($modelUrl) {
    Write-Host "[install] Downloading model..."
    Invoke-WebRequest -Uri $modelUrl -OutFile "$EdgeHome\models\current\model.gguf"
    $manifest | ConvertTo-Json | Out-File "$EdgeHome\models\current\manifest.json" -Encoding utf8
}

# 5. 환경변수 파일
@"
STORE_ID=$($env:STORE_ID)
EDGE_API_KEY=$($env:EDGE_API_KEY)
MANIFEST_URL=$($env:MANIFEST_URL)
CENTRAL_API_URL=$($env:CENTRAL_API_URL)
MODEL_PATH=$EdgeHome\models\current\model.gguf
LOG_DIR=$EdgeHome\logs
EDGE_SERVER_URL=http://localhost:$EdgePort
APP_DIR=$EdgeHome
EDGE_HOME=$EdgeHome
"@ | Out-File "$EdgeHome\.env" -Encoding utf8

# 6. nssm으로 Windows 서비스 등록
$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssmPath) {
    Write-Host "[install] Registering Windows service via nssm..."
    nssm install edge-server "$EdgeHome\current-app\edge-server.exe"
    nssm set edge-server AppEnvironmentExtra `
        "STORE_ID=$($env:STORE_ID)" `
        "EDGE_API_KEY=$($env:EDGE_API_KEY)" `
        "MODEL_PATH=$EdgeHome\models\current\model.gguf" `
        "LOG_DIR=$EdgeHome\logs"
    nssm start edge-server
} else {
    Write-Host "[install] nssm not found. Install nssm for Windows service management."
    Write-Host "  Manual start: $EdgeHome\current-app\edge-server.exe"
}

# 7. Task Scheduler로 sync 등록 (5분마다)
$taskExists = Get-ScheduledTask -TaskName "EdgeSync" -ErrorAction SilentlyContinue
if (-not $taskExists) {
    $action = New-ScheduledTaskAction `
        -Execute "$EdgeHome\current-app\edge-server.exe" `
        -Argument "--sync"
    $trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)
    Register-ScheduledTask -TaskName "EdgeSync" -Action $action -Trigger $trigger -Description "GS Edge Sync"
}

Write-Host "[install] Installation complete!"
Write-Host "  Server: http://localhost:${EdgePort}/health"
Write-Host "  Logs:   $EdgeHome\logs\"

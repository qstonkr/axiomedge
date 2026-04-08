# 앱 바이너리 교체 스크립트 (Windows)
# sync.py가 staging에 다운로드한 바이너리를 교체.

$EdgeHome = if ($env:EDGE_HOME) { $env:EDGE_HOME } else { "C:\edge-model" }
$Staging = "$EdgeHome\staging"
$Current = "$EdgeHome\current-app"
$Rollback = "$EdgeHome\rollback-app"
$HealthUrl = "http://localhost:8080/health"

if (-not (Test-Path "$Staging\UPDATE_READY")) { exit 0 }

$NewVersion = Get-Content "$Staging\UPDATE_READY"
Write-Host "[update-edge] Updating app to $NewVersion..."

# 1. 서비스 중지
try { nssm stop edge-server 2>$null } catch {}

# 2. rollback 보존
if (Test-Path $Rollback) { Remove-Item -Recurse -Force $Rollback }
if (Test-Path $Current) { Copy-Item -Recurse $Current $Rollback }
New-Item -ItemType Directory -Force -Path $Current | Out-Null

# 3. staging → current
Copy-Item "$Staging\edge-server.exe" "$Current\edge-server.exe" -Force
Remove-Item "$Staging\UPDATE_READY"

# 4. 서비스 시작
try { nssm start edge-server 2>$null } catch {}

# 5. 헬스체크
Start-Sleep -Seconds 5
try {
    $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
    Write-Host "[update-edge] Updated successfully to $NewVersion"
} catch {
    Write-Host "[update-edge] Health check failed, rolling back..."
    Copy-Item "$Rollback\edge-server.exe" "$Current\edge-server.exe" -Force
    try { nssm restart edge-server 2>$null } catch {}
    Write-Host "[update-edge] Rolled back"
    exit 1
}

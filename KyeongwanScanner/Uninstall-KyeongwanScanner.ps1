$ErrorActionPreference = 'Stop'
$destinationDirectory = Join-Path $env:SystemRoot 'twain_32\KyeongwanScanner'
$destinationFile = Join-Path $destinationDirectory 'KyeongwanScanner.ds'
$ownershipFile = Join-Path $destinationDirectory '.kyeongwan-scanner.install.json'

if (-not (Test-Path -LiteralPath $ownershipFile -PathType Leaf)) {
    throw "이 프로젝트가 설치한 파일임을 확인할 수 없어 제거를 중단했습니다: $destinationDirectory"
}

try {
    $record = Get-Content -LiteralPath $ownershipFile -Raw | ConvertFrom-Json
} catch {
    throw "설치 소유권 파일을 읽을 수 없어 제거를 중단했습니다: $ownershipFile"
}
if ($record.product -ne 'kyeongwan-scanner' -or $record.file -ne 'KyeongwanScanner.ds' -or
    $record.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw "설치 소유권 정보가 예상과 달라 제거를 중단했습니다: $ownershipFile"
}
if (-not (Test-Path -LiteralPath $destinationFile -PathType Leaf)) {
    throw "설치된 Source 파일이 없어 자동 제거를 중단했습니다: $destinationFile"
}

$actualHash = (Get-FileHash -LiteralPath $destinationFile -Algorithm SHA256).Hash
if ($actualHash -ne $record.sha256) {
    throw "Source 파일이 설치 이후 변경되어 제거하지 않았습니다. 파일을 확인하세요: $destinationFile"
}

Remove-Item -LiteralPath $destinationFile -Force
Remove-Item -LiteralPath $ownershipFile -Force
Write-Host "kyeongwan-scanner 소유 파일만 제거했습니다: $destinationFile"
Write-Host "다른 파일 보존을 위해 설치 폴더는 남겨둡니다: $destinationDirectory"

param(
    [string]$BuiltSource = (Join-Path $PSScriptRoot 'bin\Win32\Release\KyeongwanScanner.ds')
)

$ErrorActionPreference = 'Stop'
$destinationDirectory = Join-Path $env:SystemRoot 'twain_32\KyeongwanScanner'
$destinationFile = Join-Path $destinationDirectory 'KyeongwanScanner.ds'
$ownershipFile = Join-Path $destinationDirectory '.kyeongwan-scanner.install.json'

if (-not (Test-Path -LiteralPath $BuiltSource -PathType Leaf)) {
    throw "빌드 파일이 없습니다: $BuiltSource"
}
if (Test-Path -LiteralPath $destinationFile) {
    throw "같은 이름의 파일이 이미 있어 덮어쓰지 않았습니다: $destinationFile"
}
if (Test-Path -LiteralPath $ownershipFile) {
    throw "설치 소유권 파일이 이미 있어 변경하지 않았습니다: $ownershipFile"
}

$directoryCreated = $false
if (Test-Path -LiteralPath $destinationDirectory) {
    if (-not (Test-Path -LiteralPath $destinationDirectory -PathType Container)) {
        throw "설치 경로가 폴더가 아닙니다: $destinationDirectory"
    }
    if (Get-ChildItem -LiteralPath $destinationDirectory -Force | Select-Object -First 1) {
        throw "전용 설치 폴더가 비어 있지 않아 변경하지 않았습니다: $destinationDirectory"
    }
} else {
    New-Item -ItemType Directory -Path $destinationDirectory | Out-Null
    $directoryCreated = $true
}

$temporaryFile = Join-Path $destinationDirectory ('.KyeongwanScanner.' + [guid]::NewGuid().ToString('N') + '.tmp')
$sourceHash = (Get-FileHash -LiteralPath $BuiltSource -Algorithm SHA256).Hash.ToLowerInvariant()
$installedFileCreated = $false
$ownershipFileCreated = $false
try {
    [System.IO.File]::Copy((Resolve-Path -LiteralPath $BuiltSource).Path, $temporaryFile, $false)
    # File.Move without overwrite fails safely if another installer creates the target meanwhile.
    [System.IO.File]::Move($temporaryFile, $destinationFile)
    $installedFileCreated = $true

    $record = @{
        product = 'kyeongwan-scanner'
        file = 'KyeongwanScanner.ds'
        sha256 = $sourceHash
    } | ConvertTo-Json -Compress
    $stream = [System.IO.File]::Open($ownershipFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $ownershipFileCreated = $true
    try {
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($record)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally {
        $stream.Dispose()
    }
    Write-Host "설치 완료: $destinationFile"
    Write-Host '업무페이지를 완전히 종료했다가 다시 열어 kyeongwan-scanner를 확인하세요.'
} catch {
    if (Test-Path -LiteralPath $temporaryFile) { Remove-Item -LiteralPath $temporaryFile -Force }
    if ($ownershipFileCreated) { Remove-Item -LiteralPath $ownershipFile -Force }
    if ($installedFileCreated -and (Test-Path -LiteralPath $destinationFile)) {
        $installedHash = (Get-FileHash -LiteralPath $destinationFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($installedHash -eq $sourceHash) { Remove-Item -LiteralPath $destinationFile -Force }
    }
    if ($directoryCreated -and (Test-Path -LiteralPath $destinationDirectory) -and
        -not (Get-ChildItem -LiteralPath $destinationDirectory -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $destinationDirectory -Force
    }
    throw
}

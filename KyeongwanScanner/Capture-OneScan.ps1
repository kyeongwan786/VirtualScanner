param(
    [string]$OutputPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Kyeongwan-one-scan-diagnostics.txt'),
    [string]$FromTime = ''
)

$ErrorActionPreference = 'Stop'
$culture = [Globalization.CultureInfo]::InvariantCulture
$twainLog = Join-Path $env:ProgramData 'KyeongwanScanner\VirtualSource\twain-diagnostics.log'
$libLog = Join-Path 'C:\SKT-UScan\bin\log' ("SKTLibCbn_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

# This file is saved as UTF-8 with a BOM so Windows PowerShell 5.1 reads the
# Korean explanations correctly. Original log files are never changed.
Write-Host 'Existing logs will not be deleted or changed.'
if ([string]::IsNullOrWhiteSpace($FromTime)) {
    Write-Host 'Run the test application scan exactly once, then return here and press Enter.'
    $start = Get-Date
    Write-Host ("Capture start: {0}" -f $start.ToString('yyyy-MM-dd HH:mm:ss'))
    [void](Read-Host 'Press Enter immediately after the scan succeeds or fails')
} else {
    $start = [datetime]::MinValue
    $parsedStart = [datetime]::TryParseExact($FromTime, 'yyyy-MM-dd HH:mm:ss', $culture, [Globalization.DateTimeStyles]::None, [ref]$start)
    if (-not $parsedStart) {
        throw 'FromTime format must be yyyy-MM-dd HH:mm:ss.'
    }
    Write-Host ("Collecting existing log rows since: {0}" -f $start.ToString('yyyy-MM-dd HH:mm:ss'))
}
$end = Get-Date

$result = New-Object System.Collections.Generic.List[string]
$result.Add(("Capture window: {0} to {1}" -f $start.ToString('yyyy-MM-dd HH:mm:ss'), $end.ToString('yyyy-MM-dd HH:mm:ss')))
$result.Add('')

$twainRows = New-Object System.Collections.Generic.List[string]
if (Test-Path -LiteralPath $twainLog) {
    foreach ($line in Get-Content -LiteralPath $twainLog) {
        if ($line.Length -lt 19) { continue }
        $timestamp = [datetime]::MinValue
        $parsed = [datetime]::TryParseExact($line.Substring(0, 19), 'yyyy-MM-dd HH:mm:ss', $culture, [Globalization.DateTimeStyles]::None, [ref]$timestamp)
        if ($parsed -and $timestamp -ge $start.AddSeconds(-2) -and $timestamp -le $end.AddSeconds(2)) {
            $twainRows.Add($line)
        }
    }
} else {
    $twainRows.Add('TWAIN 진단 로그 파일을 찾지 못했습니다.')
}

$libRows = New-Object System.Collections.Generic.List[string]
if (Test-Path -LiteralPath $libLog) {
    foreach ($line in Get-Content -LiteralPath $libLog -Encoding Default) {
        if ($line.Length -lt 23) { continue }
        $timestamp = [datetime]::MinValue
        $parsed = [datetime]::TryParseExact($line.Substring(0, 23), 'yyyy-MM-dd;HH:mm:ss.fff', $culture, [Globalization.DateTimeStyles]::None, [ref]$timestamp)
        if ($parsed -and $timestamp -ge $start.AddSeconds(-2) -and $timestamp -le $end.AddSeconds(2)) {
            $libRows.Add($line)
        }
    }
} else {
    $libRows.Add(("U.Scan 로그 파일을 찾지 못했습니다: {0}" -f $libLog))
}

$result.Add('')
$result.Add('=== 이번 스캔을 쉬운 말로 보면 ===')

$opened = $false
$enabled = $false
$imageSent = $false
$openClosedWithoutStart = $false
$eventRejectedCount = 0
$eventRejectedCode = ''
$plainLines = New-Object System.Collections.Generic.List[string]

foreach ($line in $twainRows) {
    if ($line -notmatch 'dg=0x([0-9A-Fa-f]+) dat=0x([0-9A-Fa-f]+) msg=0x([0-9A-Fa-f]+) rc=([0-9]+) cc=([0-9]+) state=([0-9]+)') { continue }
    $dg = [Convert]::ToInt32($matches[1], 16)
    $dat = [Convert]::ToInt32($matches[2], 16)
    $msg = [Convert]::ToInt32($matches[3], 16)
    $rc = [int]$matches[4]
    $cc = [int]$matches[5]

    if ($dg -eq 1 -and $dat -eq 3 -and $msg -eq 0x0401 -and $rc -eq 0) {
        $opened = $true
        $plainLines.Add('스캐너 연결을 열었어요.')
    } elseif ($dg -eq 1 -and $dat -eq 3 -and $msg -eq 0x0402 -and $rc -eq 0) {
        if ($opened -and -not $enabled) { $openClosedWithoutStart = $true }
        $plainLines.Add('스캐너 연결을 닫았어요.')
    } elseif ($dg -eq 1 -and $dat -eq 9 -and $msg -eq 0x0502 -and $rc -eq 0) {
        $enabled = $true
        $plainLines.Add('포탈이 스캔 시작을 요청했어요.')
    } elseif ($dg -eq 1 -and $dat -eq 2 -and $msg -eq 0x0601 -and $rc -eq 1) {
        $eventRejectedCount++
        $eventRejectedCode = [string]$cc
    } elseif ($dg -eq 2 -and $dat -eq 0x0101 -and $rc -eq 0) {
        $plainLines.Add('스캔할 이미지의 정보를 요청했고, Source가 응답했어요.')
    } elseif ($dg -eq 2 -and $dat -eq 0x0104 -and $rc -eq 6) {
        $imageSent = $true
        $plainLines.Add('스캔 이미지 한 페이지를 포탈에 전달했어요.')
    } elseif ($dg -eq 1 -and $dat -eq 5 -and $msg -eq 0x0701 -and $rc -eq 0) {
        $plainLines.Add('포탈이 페이지 전송 완료를 확인했어요.')
    } elseif ($rc -eq 1 -and $dat -ne 2) {
        $plainLines.Add(("Source 요청이 실패했어요. 오류 번호는 {0}이에요." -f $cc))
    }
}

foreach ($line in $libRows) {
    if ($line -match '스캐너로 스캔완료\.([0-9]+)페이지') {
        $plainLines.Add(("U.Scan 기록: 스캔 페이지 수는 {0}장이에요." -f $matches[1]))
    }
    if ($line -match '처리결과코드:([-0-9]+)') {
        $plainLines.Add(("U.Scan 처리 결과 번호는 {0}이에요. 이 번호의 공식 의미는 이 로그만으로 단정하지 않았어요." -f $matches[1]))
    }
}

if ($eventRejectedCount -gt 0) {
    if ($eventRejectedCode -eq '13') {
        $plainLines.Add(("스캔 진행 신호를 {0}번 거부했어요 (오류 13: 이 요청을 지원하지 않는다는 뜻). 이 신호가 반복되면 포탈이 계속 기다리는 원인이 될 수 있어요." -f $eventRejectedCount))
    } else {
        $plainLines.Add(("스캔 진행 신호를 {0}번 거부했어요 (오류 {1})." -f $eventRejectedCount, $eventRejectedCode))
    }
}

if ($enabled -and $imageSent) {
    $result.Add('결과: 스캔 시작과 이미지 전달까지 로그에서 확인돼요.')
} elseif ($enabled -and $eventRejectedCount -gt 0) {
    $result.Add('결과: 스캔 시작은 됐지만 진행 신호 처리에서 문제가 기록됐어요.')
} elseif ($openClosedWithoutStart) {
    $result.Add('결과: 스캐너는 열렸지만 실제 스캔 시작 요청 전에 닫혔어요.')
} elseif (-not $opened) {
    $result.Add('결과: 이 시간 구간에서 스캐너 연결 요청을 찾지 못했어요.')
} elseif ($enabled) {
    $result.Add('결과: 스캔 시작 요청은 확인됐어요. 아래 설명과 상세 로그에서 다음 단계를 확인하세요.')
} else {
    $result.Add('결과: 스캐너가 열린 것은 확인됐지만, 다음 단계는 로그만으로 확인되지 않았어요.')
}

if ($plainLines.Count -eq 0) {
    $result.Add('상세 요청 기록을 읽을 수 없어요. 아래 원본 로그를 그대로 보존했습니다.')
} else {
    foreach ($plainLine in $plainLines) { $result.Add(("- {0}" -f $plainLine)) }
}

$result.Add('')
$result.Add('=== 원본 TWAIN 로그 ===')
foreach ($line in $twainRows) { $result.Add($line) }
$result.Add('')
$result.Add('=== 원본 U.Scan 로그 ===')
foreach ($line in $libRows) { $result.Add($line) }
$result.Add('')
$result.Add('문서 내용이나 개인정보가 포함되지 않았는지 확인한 뒤 공유하세요.')
$result | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host ("Done. Diagnostic file: {0}" -f $OutputPath)

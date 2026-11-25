$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'bin\Win32\Release\RelayScanner.ds'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw 'Build the Win32 Release source first with build-win32.cmd.'
}
$files = @('Setup-RelayScanner.cmd', 'Setup-RelayScanner.ps1', 'Install-RelayScanner.ps1', 'Uninstall-RelayScanner.ps1')
foreach ($name in $files) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) {
        throw "Missing package input: $name"
    }
}
# A new directory per build avoids replacing an earlier installation package.
$packageName = 'RelayScanner-Setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$directory = Join-Path (Join-Path $PSScriptRoot 'dist') $packageName
New-Item -ItemType Directory -Path $directory | Out-Null
Copy-Item -LiteralPath $source -Destination $directory
foreach ($name in $files) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $directory
}
@'
RelayScanner installation

1. Extract this entire ZIP to a local folder on the target Windows PC.
2. Close your scanning application.
3. Double-click Setup-RelayScanner.cmd and approve the Windows administrator prompt.
4. Restart the scanning application and select relay-scanner.
5. Run Receiver separately and add documents to the scan queue.

The setup creates %SystemRoot%\twain_32\RelayScanner and copies the source there.
Existing files are never overwritten. Other scanners and received documents are preserved.
Receiver uses the existing %ProgramData%\KyeongwanScanner\VirtualSource\ready path.
To uninstall, run Uninstall-RelayScanner.ps1 in an administrator PowerShell window.
This package does not install Receiver or build tools.
'@ | Set-Content -LiteralPath (Join-Path $directory 'INSTALL.txt') -Encoding UTF8
$archive = $directory + '.zip'
Compress-Archive -LiteralPath $directory -DestinationPath $archive
Write-Host "Installation package: $archive"

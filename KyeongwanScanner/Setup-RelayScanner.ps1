param([switch]$Elevated)

$ErrorActionPreference = 'Stop'
try {
    $source = Join-Path $PSScriptRoot 'RelayScanner.ds'
    $installer = Join-Path $PSScriptRoot 'Install-RelayScanner.ps1'
    foreach ($file in @($source, $installer)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Missing setup file: $file. Extract the entire installation ZIP first."
        }
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if ($Elevated) { throw 'Administrator permission is required.' }
        # Encode the literal script path so spaces, apostrophes and non-ASCII paths survive elevation.
        $literalPath = $PSCommandPath.Replace("'", "''")
        $command = "& '$literalPath' -Elevated"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded)
        exit $process.ExitCode
    }
    & $installer -BuiltSource $source
    Write-Host 'Installation complete. Restart your scanning application and select relay-scanner.'
    Write-Host 'Run Receiver separately and add documents to its scan queue.'
    if ($Elevated) { Read-Host 'Press Enter to close' | Out-Null }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($Elevated) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}

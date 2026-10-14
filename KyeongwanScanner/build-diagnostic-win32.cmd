@echo off
setlocal

set "PROJECT=%~dp0KyeongwanScannerDiagnostic.vcxproj"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if not exist "%PROJECT%" (
    echo ERROR: Project file not found: "%PROJECT%"
    exit /b 1
)

if not exist "%VSWHERE%" (
    echo ERROR: Visual Studio Installer's vswhere.exe was not found.
    echo Install Visual Studio with the MSVC v143 x64/x86 build tools.
    exit /b 1
)

for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -prerelease -property installationPath`) do set "VSROOT=%%I"

if not defined VSROOT (
    echo ERROR: vswhere.exe did not report a Visual Studio installation.
    exit /b 1
)

set "MSBUILD=%VSROOT%\MSBuild\Current\Bin\MSBuild.exe"
if not exist "%MSBUILD%" (
    echo ERROR: MSBuild.exe was not found under "%VSROOT%".
    exit /b 1
)

echo Building side-by-side diagnostic TWAIN source with: "%MSBUILD%"
"%MSBUILD%" "%PROJECT%" /m /p:Configuration=Release /p:Platform=Win32
if errorlevel 1 (
    echo.
    echo Build failed. Confirm the MSVC v143 x64/x86 build tools and Windows SDK are installed.
    exit /b 1
)

echo.
echo Build succeeded: "%~dp0bin\Win32\Diagnostic\KyeongwanScannerDiagnostic.ds"
exit /b 0

@echo off
setlocal
set "VSROOT="

set "PROJECT=%~dp0KyeongwanScannerDiagnostic.vcxproj"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if not exist "%PROJECT%" (
    echo ERROR: Project file not found: "%PROJECT%"
    goto :build_failed
)

if not exist "%VSWHERE%" (
    echo ERROR: Visual Studio Installer's vswhere.exe was not found.
    echo Install Visual Studio with the MSVC v143 x64/x86 build tools.
    goto :build_failed
)

for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -prerelease -property installationPath`) do set "VSROOT=%%I"

if not defined VSROOT (
    echo ERROR: vswhere.exe did not report a Visual Studio installation.
    goto :build_failed
)

set "MSBUILD=%VSROOT%\MSBuild\Current\Bin\MSBuild.exe"
if not exist "%MSBUILD%" (
    echo ERROR: MSBuild.exe was not found under "%VSROOT%".
    goto :build_failed
)

echo Building side-by-side diagnostic TWAIN source with: "%MSBUILD%"
"%MSBUILD%" "%PROJECT%" /m /p:Configuration=Release /p:Platform=Win32
if errorlevel 1 (
    echo.
    echo Build failed. Confirm the MSVC v143 x64/x86 build tools and Windows SDK are installed.
    goto :build_failed
)

echo.
echo Build succeeded: "%~dp0bin\Win32\Diagnostic\RelayScannerDiagnostic.ds"
if /i not "%~1"=="--no-pause" pause
exit /b 0

:build_failed
echo.
echo Build stopped. Read the error above.
echo Required: Visual Studio C++ tools, MSVC v143 x64/x86, and Windows SDK.
if /i not "%~1"=="--no-pause" pause
exit /b 1

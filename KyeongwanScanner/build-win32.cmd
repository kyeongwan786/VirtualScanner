@echo off
setlocal
set "VSROOT="

set "PROJECT=%~dp0KyeongwanScanner.vcxproj"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if not exist "%PROJECT%" (
    echo ERROR: Project file not found: "%PROJECT%"
    goto :build_failed
)

if not exist "%VSWHERE%" (
    echo ERROR: Visual Studio Installer's vswhere.exe was not found.
    echo Install Visual Studio 2022 or 2026 with the MSVC v143 x64/x86 build tools.
    goto :build_failed
)

rem Include Preview/Insiders installations as well as stable releases. Component
rem IDs differ between Visual Studio releases, so let MSBuild report a missing
rem v143 toolset precisely instead of filtering installations here.
for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -prerelease -property installationPath`) do set "VSROOT=%%I"

if not defined VSROOT (
    echo ERROR: vswhere.exe did not report a Visual Studio installation.
    echo Open Visual Studio Installer and confirm the installation is completed.
    goto :build_failed
)

set "MSBUILD=%VSROOT%\MSBuild\Current\Bin\MSBuild.exe"
if not exist "%MSBUILD%" (
    echo ERROR: MSBuild.exe was not found under "%VSROOT%".
    goto :build_failed
)

echo Building the 32-bit TWAIN source with:
echo Visual Studio installation: "%VSROOT%"
echo "%MSBUILD%"
"%MSBUILD%" "%PROJECT%" /m /p:Configuration=Release /p:Platform=Win32
if errorlevel 1 (
    echo.
    echo Build failed. Confirm the MSVC v143 x64/x86 build tools and Windows SDK are installed.
    goto :build_failed
)

echo.
echo Build succeeded: "%~dp0bin\Win32\Release\RelayScanner.ds"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Package-RelayScanner.ps1"
if errorlevel 1 (
    echo Installation package creation failed.
    goto :build_failed
)
if /i not "%~1"=="--no-pause" pause
exit /b 0

:build_failed
echo.
echo Build stopped. Read the error above.
echo Required: Visual Studio C++ tools, MSVC v143 x64/x86, and Windows SDK.
if /i not "%~1"=="--no-pause" pause
exit /b 1

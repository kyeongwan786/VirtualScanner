@echo off
setlocal

set "PROJECT=%~dp0KyeongwanScanner.vcxproj"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if not exist "%PROJECT%" (
    echo ERROR: Project file not found: "%PROJECT%"
    exit /b 1
)

if not exist "%VSWHERE%" (
    echo ERROR: Visual Studio Installer's vswhere.exe was not found.
    echo Install Visual Studio 2022 or 2026 with the MSVC v143 x64/x86 build tools.
    exit /b 1
)

rem VS 2026 uses a versioned component ID for the optional v143 toolset.
for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.ComponentGroup.VC.Tools.143.x86.x64 -property installationPath`) do set "VSROOT=%%I"

rem VS 2022 exposes the x86/x64 v143 tools through the unversioned component ID.
if not defined VSROOT (
    for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%I"
)

if not defined VSROOT (
    echo ERROR: No Visual Studio installation with the C++ x86/x64 tools was found.
    echo In Visual Studio Installer, add Desktop development with C++ and the MSVC v143 x64/x86 tools.
    exit /b 1
)

set "MSBUILD=%VSROOT%\MSBuild\Current\Bin\MSBuild.exe"
if not exist "%MSBUILD%" (
    echo ERROR: MSBuild.exe was not found under "%VSROOT%".
    exit /b 1
)

echo Building the 32-bit TWAIN source with:
echo Visual Studio installation: "%VSROOT%"
echo "%MSBUILD%"
"%MSBUILD%" "%PROJECT%" /m /p:Configuration=Release /p:Platform=Win32
if errorlevel 1 (
    echo.
    echo Build failed. Confirm the MSVC v143 x64/x86 build tools and Windows SDK are installed.
    exit /b 1
)

echo.
echo Build succeeded: "%~dp0bin\Win32\Release\KyeongwanScanner.ds"
exit /b 0

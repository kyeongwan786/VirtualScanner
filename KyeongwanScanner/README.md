# kyeongwan-scanner (TWAIN Source와 JPG 대기열 연결)

이 폴더는 Windows x86 TWAIN Data Source 프로젝트입니다. 업무페이지의 스캐너 선택창에 `kyeongwan-scanner` 이름을 노출하기 위한 독립 Source를 만듭니다. 기존 Canon, HP, UniTwain, VOIM 또는 WIA 드라이버 파일과 레지스트리를 수정하거나 덮어쓰지 않습니다. 빌드 결과는 고유한 하위 폴더 `C:\Windows\twain_32\KyeongwanScanner\KyeongwanScanner.ds`에 별도 설치합니다.

## Windows PC에서 빌드

1. Visual Studio 2022 또는 2026에서 **Desktop development with C++**, **MSVC v143 x64/x86 build tools**, Windows SDK를 설치합니다. `ARM` 또는 `ARM64` 도구가 아니라 **x64/x86** 도구가 필요합니다.
2. 이 폴더를 Windows PC로 복사합니다.
3. PowerShell 또는 명령 프롬프트에서 이 폴더로 이동해 빌드 스크립트를 실행합니다:

   ```bat
   .\build-win32.cmd
   ```

   스크립트가 Visual Studio 2022/2026의 MSBuild를 찾아 Win32 Release로 빌드합니다. VS2026의 v143 도구 구성 요소 ID도 검색합니다. 산출물은 `bin\Win32\Release\KyeongwanScanner.ds`입니다. 기존 업무 프로그램 화면의 `(32-32)`가 실제 32비트 TWAIN 사용을 뜻하는지는 대상 PC에서 확인해야 합니다.

   `vswhere.exe`를 찾지 못하거나 C++ 도구가 없다는 메시지가 나오면 Visual Studio Installer에서 **MSVC v143 x64/x86 build tools**가 설치되어 있는지 확인합니다. Visual Studio Installer 자체에서 메모리 접근 위반이 발생하면 이 스크립트로 해결할 수 없으며, Installer 문제를 먼저 복구해야 합니다.

## 별도 설치 및 제거

관리자 권한 PowerShell에서 설치합니다:

```powershell
 powershell -ExecutionPolicy Bypass -File .\Install-KyeongwanScanner.ps1
```

설치 스크립트는 전용 폴더가 이미 존재하면서 비어 있지 않거나 같은 이름의 파일이 있으면 중단합니다. 설치 파일과 SHA-256 소유권 기록을 새로 만들며, 기존 TWAIN Source 파일은 덮어쓰지 않습니다. 설치한 뒤 업무페이지를 완전히 종료하고 다시 열어 목록을 새로 불러오세요. 제거할 때는 다음을 실행합니다:

```powershell
 powershell -ExecutionPolicy Bypass -File .\Uninstall-KyeongwanScanner.ps1
```

제거 스크립트는 설치 소유권 기록과 Source 파일의 SHA-256이 일치할 때만 둘을 삭제합니다. Source가 설치 후 변경됐거나 소유권 기록이 없으면 중단하며, 설치 폴더는 다른 파일 보호를 위해 남겨둡니다.

## JPG 저장 위치와 스캔 흐름

Receiver가 저장하는 JPG 위치는 `%ProgramData%\KyeongwanScanner\VirtualSource\ready\<job-id>`입니다. `.ds` 설치 폴더와는 별도이며, Receiver와 TWAIN Source가 같은 데이터 폴더를 사용합니다. 업무페이지가 스캔을 요청하면 Source가 완성된 작업 하나를 찾아 JPG 페이지들을 TWAIN Native Transfer로 반환합니다. 모든 페이지 전송이 끝난 작업은 삭제하지 않고 `completed` 하위 폴더로 이동합니다. 취소되거나 미완료된 작업은 `ready`에 남아 재시도할 수 있습니다.

이 설계에서 업무페이지는 JPG 폴더를 직접 열거하지 않습니다. 업무페이지는 TWAIN Source를 선택하고 스캔을 요청하며, Source가 지정 폴더의 JPG를 읽어 스캔 결과로 응답합니다.

## 현재 구현과 검증 상태

코드에는 TWAIN identity, 열기/닫기, JPG 작업 검색, Acquire 이벤트, 이미지 정보 응답 및 Native Transfer가 작성되어 있습니다. TWAIN 1.x 자료구조의 Win32 크기를 컴파일 시 검사하도록 했습니다. 다만 Windows에서 빌드하지 않았고, 업무페이지의 TWAIN 호환성·선택·실제 이미지 반환도 아직 확인되지 않았습니다. 업무페이지가 WIA만 사용한다면 이 TWAIN Source는 목록에 나타나지 않을 수 있습니다. 화면의 `(32-32)` 표기만으로 API를 확정할 수 없습니다.

구조체 대조 기준은 [TWAIN Working Group의 공식 `twain.h`](https://github.com/twain/twain-dsm/blob/master/TWAIN_DSM/src/twain.h)입니다. x86 DSM이 `C:\Windows\twain_32` 아래의 `.ds` 파일을 하위 폴더까지 검색한다는 설치 경로 근거는 [공식 DSM README](https://github.com/twain/twain-dsm/blob/master/TWAIN_DSM/README.txt)에 있습니다.

기존 장치 파일은 건드리지 않도록 구성했지만, 페이지의 자체 장치 검색 방식과 실제 Source 호환성은 대상 PC에서 목록을 확인해야만 확정할 수 있습니다.

# kyeongwan-scanner (TWAIN Source와 JPG 대기열 연결)

이 폴더는 Windows x86 TWAIN Data Source 프로젝트입니다. 업무페이지의 스캐너 선택창에 `kyeongwan-scanner` 이름을 노출하기 위한 독립 Source를 만듭니다. 기존 Canon, HP, UniTwain, VOIM 또는 WIA 드라이버 파일과 레지스트리를 수정하거나 덮어쓰지 않습니다. 빌드 결과는 고유한 하위 폴더 `C:\Windows\twain_32\KyeongwanScanner\KyeongwanScanner.ds`에 별도 설치합니다.

## Windows PC에서 빌드

1. Visual Studio 2022 또는 2026에서 **Desktop development with C++**, **MSVC v143 x64/x86 build tools**, Windows SDK를 설치합니다. `ARM` 또는 `ARM64` 도구가 아니라 **x64/x86** 도구가 필요합니다.
2. 이 폴더를 Windows PC로 복사합니다.
3. PowerShell 또는 명령 프롬프트에서 이 폴더로 이동해 빌드 스크립트를 실행합니다:

   ```bat
   .\build-win32.cmd
   ```

   스크립트가 `vswhere`로 안정화 버전과 Preview/Insiders 버전을 포함한 Visual Studio의 MSBuild를 찾아 Win32 Release로 빌드합니다. Visual Studio 구성 요소 ID 차이로 설치본 검색이 실패하지 않도록 별도 구성 요소 필터는 사용하지 않으며, v143이 빠진 경우 MSBuild가 해당 도구 누락 오류를 표시합니다. 산출물은 `bin\Win32\Release\KyeongwanScanner.ds`입니다. 기존 업무 프로그램 화면의 `(32-32)`가 실제 32비트 TWAIN 사용을 뜻하는지는 대상 PC에서 확인해야 합니다.

   `vswhere.exe`가 Visual Studio를 찾지 못하면 Visual Studio Installer에서 설치가 완료됐는지 확인합니다. `MSB8020` 또는 `v143` 도구 세트를 찾을 수 없다는 오류가 나오면 Installer의 개별 구성 요소에서 **MSVC v143 x64/x86 build tools**를 추가합니다. Visual Studio Installer 자체에서 메모리 접근 위반이 발생하면 이 스크립트로 해결할 수 없으며, Installer 문제를 먼저 복구해야 합니다.

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

Receiver가 저장하는 JPG 위치는 `%ProgramData%\KyeongwanScanner\VirtualSource\ready\<job-id>`입니다. `.ds` 설치 폴더와는 별도이며, Receiver와 TWAIN Source가 같은 데이터 폴더를 사용합니다. 업무페이지가 스캔을 요청하면 Source가 완성된 작업 하나를 찾아 JPG 페이지들을 TWAIN Native Transfer로 반환합니다. 마지막 페이지 전송을 업무페이지가 `MSG_ENDXFER`로 성공 확인하면 작업 폴더를 삭제합니다. 취소되거나 미완료된 작업은 `ready`에 남아 재시도할 수 있습니다.

이 설계에서 업무페이지는 JPG 폴더를 직접 열거하지 않습니다. 업무페이지는 TWAIN Source를 선택하고 스캔을 요청하며, Source가 지정 폴더의 JPG를 읽어 스캔 결과로 응답합니다.

## 현재 구현과 검증 상태

코드에는 TWAIN identity, 열기/닫기, JPG 작업 검색, Acquire 이벤트, 이미지 정보 응답 및 Native Transfer가 작성되어 있습니다. TWAIN 1.x 자료구조의 Win32 크기를 컴파일 시 검사하도록 했습니다. 2026-10-14 Windows VM에서 신청서 스캔이 정상 완료된 것을 사용자가 확인했습니다. 이 과정에서 Source Manager가 관리하는 identity ID를 보존하고, `DAT_EVENT`를 TWAIN 값 `0x0002`로 바로잡았습니다. 반환된 이미지와 입력 JPG가 일치하는지는 아직 확인 중입니다.

스캔 오류를 진단하기 위해 TWAIN DSM 요청의 그룹/자료형/메시지 번호, 반환 코드, 상태 코드, Source 상태와 페이지 인덱스를 기록합니다. 기록 파일은 `%ProgramData%\KyeongwanScanner\VirtualSource\twain-diagnostics.log`이며 1 MiB가 되면 이전 로그를 `.1`로 한 번 보관합니다. 이미지 내용, 파일명, 경로, 토큰은 기록하지 않습니다. 이 로그는 진단 코드가 포함된 새 Win32 빌드를 설치한 뒤 생성됩니다.

U.Scan과 TWAIN 로그를 한 번의 시도로 시간 대조하려면 `Capture-OneScan.ps1`을 VM으로 복사해 일반 PowerShell에서 실행합니다. 스크립트가 시작 시각을 기록하고, 사용자가 신청서 스캔 한 번을 끝낸 뒤 Enter를 누르면 그 시간 구간의 TWAIN 진단 로그와 `SKTLibCbn_날짜.log` 행을 바탕화면의 `Kyeongwan-one-scan-diagnostics.txt`에 모읍니다. 이미 실행한 시도는 `-FromTime 'yyyy-MM-dd HH:mm:ss'` 인수로 그 시각부터 현재까지 수집할 수 있습니다. 원본 로그는 수정하거나 삭제하지 않습니다. 공유 전에 개인정보나 문서 내용이 없는지 확인하세요.

수집 결과의 `이번 스캔을 쉬운 말로 보면` 절은 열기, 스캔 시작, 진행 신호 거부, 이미지 전달을 한국어로 요약합니다. 아래 원본 TWAIN/U.Scan 로그도 함께 남겨 요약과 근거를 대조할 수 있습니다. 스크립트는 UTF-8 BOM으로 저장되어 Windows PowerShell 5.1에서 한글을 표시합니다. 원본 로그 파일은 수정하거나 삭제하지 않습니다.

### 진단 로그를 직접 읽는 법

`TWAIN source diagnostics`의 각 줄은 포탈이 TWAIN Source에 보낸 요청 한 건과 그 응답입니다. `dg`, `dat`, `msg`는 요청 종류를 16진수로 쓴 값입니다. `dg=0x00000001`은 제어 요청, `dg=0x00000002`는 이미지 요청입니다. C++를 몰라도 아래 숫자를 찾아 순서를 읽을 수 있습니다.

| 로그의 `dat` / `msg`    | 의미                                                     |
| ----------------------- | -------------------------------------------------------- |
| `dat=0x0003 msg=0x0001` | Source identity 조회 (`DAT_IDENTITY / MSG_GET`)          |
| `dat=0x0003 msg=0x0401` | Source 열기 (`DAT_IDENTITY / MSG_OPENDS`)                |
| `dat=0x0001`            | 스캔 설정 확인/변경 (`DAT_CAPABILITY`)                   |
| `dat=0x0009 msg=0x0502` | 실제 Acquire 시작 (`DAT_USERINTERFACE / MSG_ENABLEDS`)   |
| `dat=0x0002 msg=0x0601` | 앱 이벤트 전달 (`DAT_EVENT / MSG_PROCESSEVENT`)          |
| `dat=0x0101 msg=0x0001` | 현재 이미지 정보 요청 (`DAT_IMAGEINFO / MSG_GET`)        |
| `dat=0x0104 msg=0x0001` | 이미지 데이터 가져오기 (`DAT_IMAGENATIVEXFER / MSG_GET`) |
| `dat=0x0005 msg=0x0701` | 페이지 전송 완료 확인 (`DAT_PENDINGXFERS / MSG_ENDXFER`) |

`rc`는 Source의 응답입니다. `0`은 성공, `1`은 실패, `4`는 Source가 이벤트를 소비해 앱에 알릴 이벤트가 있다는 뜻, `5`는 Source와 관계없는 이벤트, `6`은 이미지 전송 완료입니다. 실패한 줄의 `cc`는 이유입니다. 예를 들어 `cc=13`은 지원하지 않는 요청, `cc=10`은 잘못된 값입니다. `state=4`는 Source가 열림, `state=5`는 Acquire 중, `state=6`은 이미지 전송 준비, `state=7`은 전송 중입니다. `page=1/3`이면 세 장 중 첫 장을 다루고 있다는 뜻입니다.

정상 흐름은 대략 `MSG_OPENDS` → capability 요청 → `MSG_ENABLEDS` → 이벤트 전달 → `DAT_IMAGEINFO` / `DAT_IMAGENATIVEXFER` → `MSG_ENDXFER` 순서입니다. Open 다음에 바로 Close만 있으면 Acquire 전 중단입니다. `MSG_ENABLEDS` 뒤에 같은 이벤트 요청이 `rc=1 cc=13`으로 반복되면 이벤트 처리가 실패한 것입니다. 로그가 길어도 우선 이 열과 순서만 보면 중단 지점을 찾을 수 있습니다. 진단 로그에 기록되는 `identityId`는 Source Manager가 준 식별자이며 숫자 자체를 설정하거나 고칠 필요는 없습니다.

구조체 대조 기준은 [TWAIN Working Group의 공식 `twain.h`](https://github.com/twain/twain-dsm/blob/master/TWAIN_DSM/src/twain.h)입니다. x86 DSM이 `C:\Windows\twain_32` 아래의 `.ds` 파일을 하위 폴더까지 검색한다는 설치 경로 근거는 [공식 DSM README](https://github.com/twain/twain-dsm/blob/master/TWAIN_DSM/README.txt)에 있습니다.

기존 장치 파일은 건드리지 않도록 구성했지만, 페이지의 자체 장치 검색 방식과 실제 Source 호환성은 대상 PC에서 목록을 확인해야만 확정할 수 있습니다.

## 기존 Source를 유지하는 병렬 진단 설치

현재 설치된 `kyeongwan-scanner`를 제거하거나 덮어쓰지 않고 진단 빌드를 나란히 설치할 수 있습니다. Windows에서 `build-diagnostic-win32.cmd`를 실행하면 `bin\Win32\Diagnostic\KyeongwanScannerDiagnostic.ds`가 생성됩니다. 이를 VM으로 복사하고, 관리자 PowerShell에서 다음 스크립트를 실행합니다:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-KyeongwanScannerDiagnostic.ps1 -BuiltSource .\KyeongwanScannerDiagnostic.ds
```

이 Source의 표시 이름은 `kyeongwan-scanner-diagnostic`이며 설치 폴더는 `C:\Windows\twain_32\KyeongwanScannerDiagnostic`입니다. 업무 포털의 신청서 스캔 설정에서 이 이름을 임시 선택해 진단할 수 있습니다. 진단 빌드는 성공한 요청을 포함해 모든 TWAIN 요청을 같은 `twain-diagnostics.log`에 기록하며, capability 번호와 EnableDS UI 인수도 남깁니다. 기존 `kyeongwan-scanner`, VOIM, WIA 장치의 파일 및 설정은 설치 스크립트가 변경하지 않습니다. 시험 후 진단 Source를 제거할 때만 `Uninstall-KyeongwanScannerDiagnostic.ps1`을 사용합니다. 소유권/해시가 일치하지 않으면 제거를 중단합니다.

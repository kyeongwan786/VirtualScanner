# Scanner Detector

Windows용 32비트 진단 도구입니다. 설치된 32비트 TWAIN Sources와 WIA 스캐너를 각각 조회해 화면에 표시합니다.

## 빌드 및 실행

- Windows에서 .NET Framework 4.8 Developer Pack과 .NET SDK가 필요합니다.
- `dotnet build ScannerDetector.csproj -c Release` 실행 후 `bin/Release/net48/ScannerDetector.exe`를 실행합니다.
- 프로그램 제목과 보고서의 `Process: 32-bit`를 확인하세요. TWAIN 목록 조회는 x86 프로세스에서만 동작합니다.
- 관리자 권한은 보통 필요하지 않습니다. 판매점 프로그램과 같은 Windows PC에서 실행해야 비교할 수 있습니다.

결과를 저장한 뒤 판매점 프로그램 설정 화면의 이름과 비교하세요. `(32-32)` 표기 자체는 프로그램별 표시 방식일 수 있으므로, 동일한 이름이 실제 TWAIN 목록에 있는지로 판단하는 것이 좋습니다. 이 도구는 장치를 열거나 스캔하지 않습니다.

NEW_WORK Windows EXE 빌드 안내
==============================

이 패키지는 Windows x64에서 NEW_WORK GUI를 빌드하기 위한 소스입니다.
macOS에서 Windows EXE를 만들 수 없어, 이 ZIP에 완성된 EXE는 들어 있지 않습니다.

빌드
----
1. ZIP을 Windows PC에 압축 해제합니다.
2. 인터넷 연결과 Python 3.12 x64 설치를 확인합니다.
3. build_windows.bat을 더블클릭합니다.
4. 성공하면 dist\new_work.exe가 생성됩니다. 이전 빌드와 구분하려면 기존
   dist\new_work.exe를 지운 다음 다시 빌드하세요.

이 소스에는 PaddleX OCR 선택 의존성 설치와 해당 메타데이터를 EXE에 포함하는
수정이 들어 있습니다. 이전 빌드 EXE에는 이 수정이 없으므로 다시 빌드하세요.
OCR 의존성이 빠져 있으면 빌드가 오류를 표시하고 중단됩니다.
빌드는 `.venv-build`를 새로 만들고 pip 캐시를 사용하지 않아, 오래되거나 손상된
wheel 캐시로 인한 SHA-256 불일치를 피합니다. 해시 오류가 다시 나오면 사용하는
패키지 인덱스/프록시가 파일을 바꾸거나 손상시키는지 점검하세요.

OCR 초기화가 멈추거나 오류가 나면 build_windows_debug.bat을 실행해 콘솔 창이
포함된 진단용 EXE를 빌드하세요. dist\new_work.exe를 명령 프롬프트에서 실행하고
OCR을 눌러 콘솔에 출력되는 실제 오류를 확인할 수 있습니다.

실행
----
dist\new_work.exe를 실행합니다. 최초 OCR 실행 시 PaddleOCR 모델을 내려받으므로
VM의 인터넷 연결이 필요합니다. OCR 결과 sk_report.txt는
C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt에 저장되며, 기존 파일이 있으면
덮어씁니다. sk_report.txt에는 신분증 개인정보가 포함될 수 있으므로 공유하지 마세요.

주의
----
EXE는 one-file 빌드라 VM에는 dist\new_work.exe 하나만 옮기면 됩니다. OCR 모델은
빌드 파일에 포함되지 않으며 VM 첫 OCR 실행 시 사용자 캐시로 내려받습니다. 따라서
VM이 Paddle 모델 다운로드 서버에 접속할 수 있어야 합니다. 이 패키징 수정은 OCR
초기화 의존성 오류를 고치지만 모델 다운로드 연결 문제를 해결하지는 않습니다.

실제 Windows에서 빌드 및 실행 검증은 아직 수행되지 않았습니다. 빌드 PC와 VM의
Windows 아키텍처가 맞는지 확인하고, VM에서 첫 실행 후에도 오류가 나면 실행 로그와
빌드 폴더의 build\new_work\warn-new_work.txt를 확인해야 합니다.

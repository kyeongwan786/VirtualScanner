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
빌드 시작 시 `NEW_WORK build: clean virtual environment, no pip cache, official PyPI index`
문구가 표시되어야 합니다. 빌드는 `.venv-build`를 새로 만들고 pip 캐시를 사용하지
않으며 PyPI 공식 인덱스에서 내려받습니다. 해시 오류가 다시 나오면 이 문구가
표시됐는지 먼저 확인하세요. 표시됐는데도 계속되면 네트워크 프록시/보안 프로그램이
다운로드 파일을 바꾸거나 손상시키는지 확인해야 합니다.

OCR 초기화가 멈추거나 오류가 나면 build_windows_debug.bat을 실행해 콘솔 창이
포함된 진단용 EXE를 빌드하세요. dist\new_work.exe를 명령 프롬프트에서 실행하고
OCR을 눌러 콘솔에 출력되는 실제 오류를 확인할 수 있습니다.

실행
----
dist\new_work.exe를 실행합니다. PP-OCRv5 검출·한국어 인식·영문 인식 모델은
EXE에 포함되어 있어 VM에서 모델 다운로드 연결이 필요하지 않습니다. SK를 선택하면
OCR 결과는 C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt에 저장되며 기존 파일을
같은 파일명으로 덮어씁니다. KT를 선택하면 같은 폴더의 report.txt를 덮어쓰고,
C:\IMAGE\ORG.jpg와 C:\IMAGE\CR.jpg도 고정 파일명으로 덮어씁니다. 번호는 화면에서
하이픈을 사용해 보여 주지만 SK 보고서에는 숫자만 저장합니다. 발급일자는 화면에 점을
표시하고 SK 보고서에는 8자리 숫자로 저장합니다. 보고서와 이미지는 개인정보가 포함될
수 있으므로 공유하지 마세요.
파일 탐색기에서 JPG 한 장을 프로그램 창에 끌어다 놓으면 이미지가 표시되고 OCR이
시작됩니다.

주의
----
EXE는 one-file 빌드이므로 VM에는 dist\new_work.exe 하나만 옮기면 됩니다. OCR 모델
3개는 빌드 때 EXE 안에 포함됩니다. `ocr_models` 누락 시 패키징은 오류로 중단합니다.

실제 Windows에서 빌드 및 실행 검증은 아직 수행되지 않았습니다. 빌드 PC와 VM의
Windows 아키텍처가 맞는지 확인하고, VM에서 첫 실행 후에도 오류가 나면 실행 로그와
빌드 폴더의 build\new_work\warn-new_work.txt를 확인해야 합니다.

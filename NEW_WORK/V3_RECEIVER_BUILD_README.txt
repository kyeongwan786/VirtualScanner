NEW_WORK V3 Receiver 빌드 안내
==============================

V3 Receiver는 선택 PC로 전송된 작업 패키지를 Relay에서 받아 접수 폴더에 보관하고,
PDF 서류 스캔 대기열을 관리합니다. 신분증 OCR 값은 송신기에서 검토된 결과를 사용하며
Receiver에는 신분증 종류만 표시됩니다. **신분증 확인**을 눌렀을 때 SK의
`sk_report.txt` 또는 KT DB와 `ORG.jpg`/`CR.jpg`를 저장합니다.

KT CR.jpg는 기존 코드 크기 규격에 따라 유형 1001은 340×406, 그 외 유형은
320×420으로 저장합니다. 중앙에서 직접 배치한 크롭 좌표도 수신 이미지에 그대로
적용합니다.

신분증 없이 PDF만 전송된 패키지도 받습니다. 이때 SK/KT 결과는 수정하지 않습니다.
새 패키지가 와도 이전 접수 폴더와 ready 대기열은 유지됩니다. ID/PDF가 없는 경우도
화면에 각각 없음 상태를 표시합니다.

메인 화면은 HTML/CSS와 pywebview로 구현되어 macOS와 Windows에서 같은 폴더 화면을
사용합니다. 송신기에서 PDF를 JPG 페이지로 변환해 보내므로 수신기에는 변환된 JPG 썸네일과
페이지 수가 표시됩니다. 원본 PDF 파일명은 이미지를 묶어 구분하는 이름으로만 남습니다.
**스캔 대기열에 추가**를 누르면 그 이미지 묶음만 `ready`에 복사되고 버튼은 **스캔 취소**로
바뀝니다. 취소해도 수신한 JPG 원본은 보존하고 ready 복사본만 대기 상태로 되돌립니다.

스캔 목록의 서류는 스캔 완료 전에도 **대기열로 내리기**로 되돌릴 수 있습니다.
Receiver는 ready에 올린 복사본만 제거하고 받은 원본 페이지는 보존합니다. TWAIN이
해당 서류를 읽는 중이면 진행 중인 스캔이 실패할 수 있습니다.

Windows x64에서 Python 3.12 x64를 설치하고 build_receiver_windows_v3.bat을
실행하세요. 결과는 dist\new_work_receiver_v3\new_work_receiver_v3.exe입니다.
이 ZIP은 빌드 소스이며 완성된 Windows EXE는 포함하지 않습니다.

macOS에서 Receiver 화면 실행
---------------------------

macOS에서도 같은 Receiver 화면을 실행하고 Relay에서 자료를 받아 신분증 확인을 하거나
PDF 페이지를 미리 볼 수 있습니다. macOS용 Python 3.12를 설치한 뒤
NEW_WORK 폴더에서 run_receiver_macos.command를 실행하세요. 처음 실행할 때
전용 가상환경을 만들고 requirements_receiver.txt의 패키지를 설치합니다.
터미널에서 직접 실행하려면 다음 명령도 사용할 수 있습니다.

    cd NEW_WORK
    chmod +x run_receiver_macos.command
    ./run_receiver_macos.command

macOS 앱으로 묶으려면 build_receiver_macos.command를 실행하세요. 결과는
dist/new_work_receiver_v3.app입니다. 이 앱은 실행한 Mac용으로 생성되며 Windows
실행 파일이 아닙니다. 앱 서명과 공증은 포함하지 않습니다.

가상 스캐너의 TWAIN Source와 업무페이지에서 실제 스캔하는 단계는 Windows용
구성입니다. macOS에서 Receiver UI, Relay 수신, 신분증 확인, JPG 미리보기와
로컬 대기열은 실행할 수 있지만, 업무페이지 스캔 연동은 Windows PC에서 확인해야
합니다. macOS에서의 Windows 스캔 호환성은 검증된 것으로 간주하지 않습니다.

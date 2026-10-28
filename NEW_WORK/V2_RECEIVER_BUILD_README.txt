NEW_WORK V2 Receiver 빌드 안내
==============================

V2 Receiver는 선택 PC로 전송된 OCR 작업을 Relay에서 받아 SK/KT 로컬 결과에
적용하며, PDF 서류 스캔 대기열도 관리합니다. 받은 OCR 값이 틀리면 Receiver에서
값을 수정해 같은 SK 보고서 또는 KT DB 접수 행에 다시 적용할 수 있습니다.

KT CR.jpg는 기존 코드 크기 규격에 따라 유형 1001은 340×406, 그 외 유형은
320×420으로 저장합니다. 중앙에서 직접 배치한 크롭 좌표도 수신 이미지에 그대로
적용합니다.

신분증 없이 PDF만 전송된 패키지도 받습니다. 이때 SK/KT 결과는 수정하지 않고 PDF
서류만 대기열에 추가하며, 신분증 결과 화면에는 서류 전용 작업임을 표시합니다.

Windows x64에서 Python 3.12 x64를 설치하고 build_receiver_windows.bat을
실행하세요. 결과는 dist\new_work_receiver\new_work_receiver.exe입니다.
이 ZIP은 빌드 소스이며 완성된 Windows EXE는 포함하지 않습니다.

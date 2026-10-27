NEW_WORK V2 Receiver 빌드 안내
==============================

V2 Receiver는 선택 PC로 전송된 OCR 작업을 Relay에서 받아 SK/KT 로컬 결과에
적용하며, PDF 서류 스캔 대기열도 관리합니다. 받은 OCR 값이 틀리면 Receiver에서
값을 수정해 같은 SK 보고서 또는 KT DB 접수 행에 다시 적용할 수 있습니다.

Windows x64에서 Python 3.12 x64를 설치하고 build_receiver_windows.bat을
실행하세요. 결과는 dist\new_work_receiver\new_work_receiver.exe입니다.
이 ZIP은 빌드 소스이며 완성된 Windows EXE는 포함하지 않습니다.

NEW_WORK V2 Windows 빌드 안내
============================

V2 중앙 앱은 중앙 PC에서 OCR 결과와 PDF 작업을 검토한 뒤 선택한 접수 PC의
V2 Receiver로 Relay 전송합니다.

빌드
----
Windows x64에서 Python 3.12 x64를 설치하고 build_windows.bat을 실행하세요.
성공하면 dist\new_work\new_work.exe가 생성됩니다.
이 ZIP은 빌드 소스이며 완성된 Windows EXE를 포함하지 않습니다.

실행 전에 Relay와 접수 PC의 자리 ID/토큰을 설정하고, 대상 접수 PC에서 V2
Receiver가 실행 중인지 확인하세요. 상세한 설정은 RECEIVER_README.md를 보세요.

전송 취소·초기화
----------------
V2 화면의 “전송 취소·초기화”는 진행 중 업로드나 Receiver 적용 대기 작업의
Relay 취소를 요청하고 현재 작업 화면 및 앱이 만든 임시 신분증 사본, PDF 페이지,
전송 ZIP을 정리합니다. 원본 신분증/JPG/PDF는 삭제하지 않습니다. 이미 Receiver가
작업을 적용한 뒤에는 취소할 수 없으며, Relay 응답 중인 요청이 끝난 다음 초기화됩니다.

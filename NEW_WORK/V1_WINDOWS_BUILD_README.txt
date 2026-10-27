NEW_WORK V1 Windows 빌드 안내
============================

V1은 OCR 창에서 저장할 때 현재 실행 중인 PC에 직접 결과를 적용합니다.
접수 PC 선택과 Relay 전송 UI가 없으며, V1은 사용할 접수 PC에서 실행하세요.

저장 동작
---------
- SK: C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt 갱신
- KT: C:\IMAGE\myCustomer.db의 T_SCAN_PAPER 갱신, C:\IMAGE\ORG.jpg와 CR.jpg 생성

빌드
----
Windows x64에서 Python 3.12 x64를 설치하고 build_windows_v1.bat을 실행하세요.
성공하면 dist\new_work_v1\new_work_v1.exe가 생성됩니다.
이 ZIP은 빌드 소스이며 완성된 Windows EXE를 포함하지 않습니다.

V1은 직접 저장하는 OCR 전용 프로그램이므로 별도 Receiver가 필요하지 않습니다.
접수 PC를 원격으로 선택해 전송하려면 V2 중앙 앱과 V2 Receiver를 쓰세요.

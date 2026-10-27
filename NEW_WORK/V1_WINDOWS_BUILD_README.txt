NEW_WORK V1 Windows 빌드 안내
============================

V1은 예전 OCR 창에 현재 OCR 알고리즘을 적용한 OCR 전용 버전입니다. PDF 추가,
접수 PC 선택, Relay 전송 기능이 없으며, 사용할 접수 PC에서 실행하세요.

저장 동작
---------
- SK: C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt 갱신
- KT: C:\IMAGE\myCustomer.db의 T_SCAN_PAPER 갱신, C:\IMAGE\ORG.jpg와 CR.jpg 생성

빌드
----
이 V1 전용 ZIP을 풀고 Windows x64에서 Python 3.12 x64를 설치한 뒤
build_windows.bat을 실행하세요.
성공하면 dist\new_work_v1\new_work_v1.exe가 생성됩니다.
이 ZIP은 빌드 소스이며 완성된 Windows EXE를 포함하지 않습니다.

V1은 직접 저장하는 OCR 전용 프로그램이므로 별도 Receiver가 필요하지 않습니다.
접수 PC를 원격으로 선택해 전송하려면 V2 중앙 앱과 V2 Receiver를 쓰세요.

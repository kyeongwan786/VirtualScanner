NEW_WORK V3 중앙 송신기 빌드 안내
================================

V3는 중앙 PC에서 쓰는 HTML/CSS 기반 송신기입니다. 신분증 이미지와 PDF를 각각
단독으로 또는 함께 추가할 수 있습니다. 이미지를 추가하면 OCR이 실행되고, PDF를
추가하면 페이지 JPG로 변환됩니다. OCR 결과와 종류별 idCrop 영역을 확인한 뒤
선택한 접수 자리의 Receiver로 보냅니다.

신분증 OCR 및 idCrop 검토 기능은 기존 처리 흐름을 사용합니다. idCrop 박스의
규격은 신분증 종류와 SK/KT 처리에 따른 기존 코드 규격을 따르며, 박스 크기는
고정이고 위치를 조정할 수 있습니다. PDF는 페이지별 JPG로 전송됩니다.

Windows 빌드
------------

Windows x64에서 Python 3.12 x64를 설치한 뒤 이 ZIP을 풀고
`build_windows_v3.bat`을 실행하세요. 성공하면 다음 실행 파일이 생성됩니다.

    dist\new_work_v3\new_work_v3.exe

이 ZIP은 OCR 모델 파일을 포함한 Windows 빌드 소스입니다. 완성된 Windows EXE는
포함하지 않습니다. Windows에서 직접 빌드해야 하며, 이 저장소 갱신 과정에서는
macOS에서 Windows EXE를 만들거나 검증하지 않았습니다.

수신 PC에는 별도 `NEW_WORK-Receiver-V3-Windows-Build.zip`의 Receiver를 설치하고,
Relay 주소와 중앙 토큰, 접수 자리별 Render Relay ID를 설정해야 합니다. 설정
절차와 수신 후 동작은 `RECEIVER_README.md`를 참고하세요.

V1은 별도 OCR 전용 프로그램이며 `NEW_WORK-V1-Windows-Build.zip`에서 빌드합니다.

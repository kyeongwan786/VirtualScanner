# NEW_WORK OCR standalone package

이 폴더는 NEW_WORK에서 JPG OCR과 `result.txt` 생성에 필요한 코드만 모은 독립 실행 패키지다. SK/KT DB 처리와 업무 GUI는 포함하지 않는다.

## Windows에서 실행

1. Python 3.12가 설치된 Windows에서 압축을 푼다.
2. `run_ocr.bat`을 실행한다. JPG 경로를 인자로 전달하지 않으면 파일 선택창이 열린다.
3. 첫 실행은 Python 패키지와 PaddleOCR 모델을 설치/다운로드하므로 인터넷 연결이 필요하다. 결과는 이 폴더의 `result.txt`에 기록된다.

명령행에서는 다음처럼 JPG와 출력 파일을 직접 지정할 수 있다:

```bat
run_ocr.bat "C:\Images\identity.jpg" "C:\Images\result.txt"
```

Windows 단일 실행 파일은 `build_windows.bat`으로 빌드한다. 산출물은 `dist\new_work_ocr.exe`다. 이는 Windows 빌드 전용이며 macOS에서 만든 실행 파일로 대체할 수 없다.

OCR은 PP-OCRv5 한국어 파이프라인을 사용한다. 사용자 제공 코드표 전체를 포함하며, `kaitIdType`/`recogSubCd`에는 숫자형 문서 코드를, `idType`에는 문자형 유형을 각각 기록한다. 예: 운전면허증 `1102/D`; 유형 선택에서 코드표의 다른 하위 코드를 선택할 수 있다. 운전면허번호는 `##-##-######-##` 12자리 형식으로 추출하고, 번호가 여러 OCR 줄로 나뉜 경우 면허번호 표기가 인접해 있을 때 이어 붙인다. 외국인등록증으로 판별되면 영문 PP-OCRv5로 카드 전체를 추가 인식해 한국어 OCR 결과와 합친다(최초 사용 시 영문 모델 다운로드가 필요하며, 실패하면 한국어 OCR 결과를 유지한다). 등록번호 영역도 확대 재인식한다. 등록번호 상자 좌표를 원본 JPG 기준으로 환산해 뒷자리 첫 숫자를 제외한 6자리 영역의 `mskPos`를 계산하고, 한글 라벨 오인식이 이름으로 저장되는 것을 막는다. OCR 인식값과 이미지 좌표에서 문서 유형/일부 업무 필드를 추출하며, `base64DATA`는 선택한 원본 JPG, `idCrop`은 OpenCV 얼굴 검출 상자 주위의 크롭 JPG를 Base64 및 URL 인코딩해 기록한다. 얼굴을 못 찾거나 검출이 모호하면 `idCrop=`이 빈 값으로 남는다. 추출값과 크롭 영역은 실제 업무 사용 전에 확인해야 한다.

`result.txt`에는 신분증 개인정보와 이미지 인코딩 값이 들어갈 수 있으므로 외부 공유나 저장소 커밋에 주의한다. 프로젝트 `.gitignore`에서 이 파일을 제외한다.

# NEW_WORK

통합 GUI와 첨부 원본의 정적 분석·이식 작업 디렉터리다. JPG 입력, 한국어 PaddleOCR 문자 인식, 원문 표시/검토, SK 보고서 생성 및 접점 `classes/sk_report.txt` 전달이 연결돼 있다. KT 저장은 최신 기존 `T_SCAN_PAPER` 행을 갱신하고 `unique()` 규칙으로 새 `TMP_ELEMENT_ID`를 만들며, 연결된 `T_SCAN_RESOURCES` 키도 함께 갱신한다. 또한 선택한 원본을 `ORG.jpg`, 얼굴 사진 영역 크롭을 `CR.jpg`로 저장한다. Windows 경로는 `C:\IMAGE\test.db`, `C:\IMAGE\ORG.jpg`, `C:\IMAGE\CR.jpg`; macOS 소스 실행은 프로젝트 내 `IMAGE/` 폴더를 사용한다. TEMP/INDEX/SUBINDEX/PAPER/RESOURCE 신규 행은 만들지 않는다. `report.txt`는 생성하지 않는다. 저장 버튼의 Windows 실제 동작은 아직 현장에서 확인해야 한다.

KT 저장은 기존 DB의 최신 `T_SCAN_PAPER` 행을 조회해 OCR 검토값 10개 필드와 `T_CODE_OCR`에서 조회한 문서 분류 메타데이터를 UPDATE하고, 새 `TMP_ELEMENT_ID`를 생성해 해당 `T_SCAN_RESOURCES` 행과 함께 변경한다. OCR `job_id`는 UI 작업 추적용 UUID이며 DB `TMP_ELEMENT_ID`로 쓰지 않는다. `T_SCAN_TEMP`, `T_SCAN_INDEX`, `T_SCAN_SUBINDEX` 및 `T_SCAN_PAPER_TEST`는 건드리지 않는다. 문서 종류·유형·국적 코드는 선택된 문서 유형 코드에 맞춰 반영한다. LG 처리는 사용자 지시에 따라 범위에서 제외한다. 기능별 상태는 [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md), 정적 분석 근거는 [`ANALYSIS.md`](ANALYSIS.md)를 참고한다.

## 개발 실행

Windows x64 Python 3.12에서 다음 명령으로 개발 의존성과 OCR 런타임을 설치한다.

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

`run.py`와 Windows EXE는 동일한 CPU PP-OCRv5 모바일 설정과 프로젝트에 포함된 같은 모델 파일 세 개를 사용한다: `PP-OCRv5_mobile_det`, `korean_PP-OCRv5_mobile_rec`, `en_PP-OCRv5_mobile_rec`. 파일 경로를 명시해 실행 중 모델을 새로 내려받지 않는다. 방향 분류, 문서 펴기, 텍스트 방향 보정은 끄고, 텍스트 검출 변의 최대 크기는 640으로 고정했다. OCR 추론은 UI를 막지 않도록 별도 스레드에서 실행한다. `requirements.txt`도 실행 패키지 버전을 고정한다. [PaddleOCR 한국어 모델·벤치마크](https://www.paddleocr.ai/v3.3.1/en/version3.x/pipeline_usage/OCR.html)

JPG 한 장을 프로그램 창 위에 끌어다 놓으면 이미지가 표시되고 OCR이 바로 실행된다. `가져오기` 버튼으로 파일을 선택해도 같은 흐름으로 처리한다. TkinterDnD2의 Tcl/Tk 리소스는 Windows EXE에 함께 패키징한다.

모델 파일은 `ocr_models/`에 포함되며 Windows EXE 안에 함께 패키징된다. VM에서 OCR 모델을 내려받기 위한 인터넷 연결은 필요하지 않다. Mac과 Windows는 같은 가중치와 파이프라인 설정을 사용하지만, CPU 네이티브 연산과 운영체제 차이에 따른 인식 결과는 Windows에서 확인해야 한다.

인식 결과는 선택한 신분증 종류에 맞는 입력칸에 표시된다. 사용자는 원본 사진과 대조해 값을 수정할 수 있으며, 업무 필드는 사람이 확인해야 한다. KT DB에는 원본 호환을 위해 일부 스캐너/검증 플래그 상수를 기록하지만, 이 프로그램이 해당 센서 측정을 수행하는 것은 아니다.

SK 포탈은 기존대로 UTF-8 `sk_report.txt`를 저장한다. Windows 위치는 `C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt`이고, macOS 소스 실행은 프로젝트 폴더다. KT 저장은 `report.txt`는 만들지 않는다. Windows에서는 `C:\IMAGE\test.db`, `C:\IMAGE\ORG.jpg`, `C:\IMAGE\CR.jpg`에 저장한다. DB는 최신 `T_SCAN_PAPER` 행의 OCR 10개 컬럼, 선택 신분증 코드의 `T_CODE_OCR` 분류 메타데이터, 새 `TMP_ELEMENT_ID`를 UPDATE하며, 기존 `T_SCAN_RESOURCES` 키도 함께 바꾼다. 원본 이미지는 `ORG.jpg`, 얼굴 사진 영역 크롭은 `CR.jpg`로 같은 이름에 덮어쓴다. 새 문서 행이나 Base64 리소스는 INSERT하지 않으며, 기존 paper/resource 행이나 얼굴 사진 crop을 만들 수 없으면 저장을 중단한다. `test.db`에는 개인정보가 포함될 수 있으므로 공유하지 않는다.

화면에서는 주민등록번호/외국인등록번호에 하이픈을, 발급일자에 점을 표시한다. SK 파일에는 `ctzCorpNum`, `isueDt`, `drvLicenIsueNum`의 숫자만 저장하고, `isueDt`는 `YYYYMMDD`로 기록한다. OCR 문자에서 주민번호·이름·발급일·발급처·주소·운전면허번호를 규칙으로 찾아 검토 입력칸에 미리 채운다. 확실하게 하나로 찾지 못한 값은 비워 두므로 사람이 OCR 원문을 확인해야 한다. `mskPos`는 하드코딩하지 않는다. 주민등록증은 OCR이 13자리 번호와 줄 좌표를 하나만 찾으면, 외국인등록증은 번호 칸을 확대 OCR해 얻은 13자리 번호 상자 좌표를 원본 JPG 좌표로 환산해, 뒷자리 첫 숫자를 제외한 6자리 영역의 좌표 4개를 쉼표로 기록한다. 이 좌표 추정과 필드 규칙은 합성 테스트만 통과했으며 실제 신분증 이미지에서 대조해야 한다. OCR 분류 결과 예: 주민등록증 `1001/R`, 운전면허증 `1102/D`, 복지카드 `1301/S`, 외국인등록증 `1401/F`. 운전면허번호는 12자리 형식으로 추출하며 OCR이 번호를 여러 줄로 나눈 경우 주변에 면허번호 표기가 있을 때만 이어 붙인다. 외국인등록증으로 판별되면 영문 OCR 결과를 한국어 OCR 결과와 합친다. 문서 분류는 이미지 문구를 바탕으로 한 추정이며 신분증 진위 확인이 아니다.

## 테스트

```bat
python -m unittest discover -s tests -v
```

테스트는 PaddleOCR 엔진을 가짜 객체로 대체해 어댑터 설정과 결과 파싱을 검사한다. 기능 테스트 SQLite는 임시 DB를 사용하고, 별도 reference audit 테스트만 첨부 DB의 스키마·행 수·고아 키 개수를 읽기 전용으로 확인하며 레코드 값은 출력하지 않는다. 신분증 이미지 콘텐츠는 테스트 입력으로 쓰지 않는다.

## Windows 단일 EXE 빌드

Windows x64에서 `build_windows.bat`을 실행하면 PyInstaller one-file 빌드를 시도해 `dist\new_work.exe`를 만든다. 현재 개발 호스트는 macOS이므로 이 BAT, Windows 실행, GUI 호환성 및 단일 EXE의 Paddle 네이티브 종속성 포함 여부는 검증되지 않았다.

PaddleX OCR 파이프라인은 런타임에 OCR 선택 의존성의 설치 메타데이터를 검사한다. 빌드 환경에는 `paddlex[ocr]`가 설치되며, `package_windows.py`가 OCR 추가 패키지의 메타데이터도 EXE에 포함한다. 이전 패키징에서 이를 빠뜨리면 `A dependency error occurred during pipeline creation` 오류가 모델 다운로드 전에 발생할 수 있다. 이 수정이 반영된 EXE를 Windows에서 다시 빌드해야 한다.

## 남은 주요 작업

- `skt.exe`의 59컬럼 이관을 독립 복사 DB에서 구현·비교한다. JPG 입력으로 물리 스캐너 검증을 통과한 것처럼 만들지 않는다.
- `com_sk.exe`, `com_kt.exe`의 전체 처리와 출력 파일 규칙을 더 분석하고 이식한다.
- 접점의 스캐너 설정 필드를 포함한 원본 설정을 더 복구하고 Windows에서 SK/KT 결과를 비교한다.
- Windows 대상 PC에서 한국어 ID 샘플의 인식 정확도/속도와 Paddle 런타임 배포를 검증한다.
- LG는 현 사용자 요청에 따라 구현 범위에서 제외한다.

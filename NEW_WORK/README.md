# NEW_WORK

통합 GUI와 첨부 원본의 정적 분석·이식 작업 디렉터리다. JPG 입력, 한국어 PaddleOCR 문자 인식, 원문 표시/검토, SK 보고서 생성 및 접점 `classes/sk_report.txt` 전달, KT 최신행 UPDATE 복사본과 `ORG.jpg`/`CR.jpg` 생성 및 접점 `IMAGE/` 전달이 연결돼 있다. SK 보고서는 DB 없이 OCR 검토 객체에서 직접 생성한다. KT 원본 DB는 읽기만 하며 결과는 작업용 `myCustomer.db` 복사본이다. `com_sk`/`com_kt`의 최신 `m.jpg` 선택 규칙과 중앙 크롭 치수, JPEG 품질 흐름을 이식했다. `skt.exe`의 기존 59컬럼 이관은 새 JPG 경로에서 호출하지 않으며 컬럼별 출처는 [`DATABASE_FIELD_AUDIT.md`](DATABASE_FIELD_AUDIT.md)에 기록했다. 원본 EXE와 Windows 동작 비교는 아직 필요하다.

`skt.exe`의 기존 `T_SCAN_TEMP` SELECT와 인덱스·하위 인덱스·문서·리소스 레코드 생성은 원본 장치 입력 이관용 분석 자료로만 보존하며 새 JPG 경로에서는 실행하지 않는다. JPG/OCR 경로는 물리 스캐너 인증이나 센서 검증을 통과한 것으로 기록하지 않는다. LG 처리는 사용자 지시에 따라 범위에서 제외한다. 기능별 상태는 [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md), 정적 분석 근거는 [`ANALYSIS.md`](ANALYSIS.md)를 참고한다.

## 개발 실행

Windows x64 Python 3.12에서 다음 명령으로 개발 의존성과 OCR 런타임을 설치한다.

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

기본 엔진은 CPU에서 PaddleOCR 한국어 PP-OCRv3 모바일 파이프라인을 사용한다. PaddleOCR 공식 표의 한국어 문자 인식 단계 CPU 시간은 v3 모바일 8.76ms, v5 모바일 21.20ms로 기재되어 있어 속도 우선 기본값은 v3로 정했다. v3의 평균 인식 점수는 v5보다 낮게 공개되어 있으므로 정확도가 더 중요하면 `NEW_WORK_OCR_VERSION=PP-OCRv5`로 전환한다. 이 수치는 인식 모듈만의 결과로 detector 등을 포함한 전체 속도를 뜻하지 않는다. `NEW_WORK_OCR_DEVICE=gpu` 환경 변수도 지정할 수 있으며 호환 GPU Paddle 런타임이 필요하다. 방향 분류, 문서 펴기, 텍스트 방향 보정 모듈은 꺼서 OCR 단계를 줄였다. OCR 추론은 UI를 막지 않도록 별도 스레드에서 실행한다.

최초 OCR 실행 때 PaddleOCR가 모델 파일을 내려받을 수 있도록 네트워크 접근이 필요하다. 단일 EXE 빌드가 완료되거나 오프라인 배포가 검증된 상태는 아니다. PaddleOCR 모델 캐시 포함과 Windows 대상 PC 성능·정확도는 별도로 검증해야 한다.

인식 결과는 줄별 텍스트와 PaddleOCR 신뢰도로 표시된다. 줄을 선택하고 검토 입력칸을 선택한 뒤 삽입할 수 있다. 업무 필드는 사람이 확인·수정해야 하며, 센서/진위 검증 값을 생성하지 않는다.

## 테스트

```bat
python -m unittest discover -s tests -v
```

테스트는 PaddleOCR 엔진을 가짜 객체로 대체해 어댑터 설정과 결과 파싱을 검사한다. 기능 테스트 SQLite는 임시 DB를 사용하고, 별도 reference audit 테스트만 첨부 DB의 스키마·행 수·고아 키 개수를 읽기 전용으로 확인하며 레코드 값은 출력하지 않는다. 신분증 이미지 콘텐츠는 테스트 입력으로 쓰지 않는다.

## Windows 단일 EXE 빌드

Windows x64에서 `build_windows.bat`을 실행하면 PyInstaller one-file 빌드를 시도해 `dist\new_work.exe`를 만든다. 현재 개발 호스트는 macOS이므로 이 BAT, Windows 실행, GUI 호환성 및 단일 EXE의 Paddle 네이티브 종속성 포함 여부는 검증되지 않았다.

## 남은 주요 작업

- `skt.exe`의 59컬럼 이관을 독립 복사 DB에서 구현·비교한다. JPG 입력으로 물리 스캐너 검증을 통과한 것처럼 만들지 않는다.
- `com_sk.exe`, `com_kt.exe`의 전체 처리와 출력 파일 규칙을 더 분석하고 이식한다.
- 접점의 스캐너 설정 필드를 포함한 원본 설정을 더 복구하고 Windows에서 SK/KT 결과를 비교한다.
- Windows 대상 PC에서 한국어 ID 샘플의 인식 정확도/속도와 Paddle 런타임 배포를 검증한다.
- LG는 현 사용자 요청에 따라 구현 범위에서 제외한다.

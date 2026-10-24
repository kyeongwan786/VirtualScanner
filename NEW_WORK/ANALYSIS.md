# NEW_WORK 원본 분석 및 이식 목록

분석 기준: `idscanner/`의 첨부 원본, `idscanner/NEW_WORK_AGENT_MASTER_PROMPT_20261017.md` 전체, 저장소 내 기존 개발 지침, 그리고 읽기 전용으로 확인한 Downloads의 U.Scan 구형 백업 및 OCR 참고 소스. 사용자가 이후 PaddleOCR 신규 구현을 지시했으며, LG는 범위에서 제외했다. 이는 명세의 초기 가정에 대한 최근 사용자 지시다.

## 원본 파일 식별

| 파일 | 확인된 형식 | 현재까지 확인한 점 | 분석 상태 |
|---|---|---|---|
| `skt(5).exe` | Windows x64 PE, Node.js/pkg 앱 | 실행 스냅샷에 `skt.release`와 `main.js` 경로가 포함됨. `strings`에서 메인 소스의 주요 함수/SQL을 회수했고 이식 포인트를 확인함. 전체 파일 `skt_main_extracted.js`는 저장소와 Downloads 검색에서 찾지 못함. | 부분 분석 |
| `work_250106(4).exe` | Windows x64 PyInstaller GUI, Python 3.13 | 메인모듈 정적 역어셈블. SK/KT 자식 프로세스 완료 후 결과 복사, 접점 데이터 및 전역 paper UPDATE 함수 확인. | 정적 흐름 확인, Windows 실행 미검증 |
| `com_sk(4).exe` | Windows x64 PyInstaller GUI, Python 3.13 | `fetch_data`, `save_data`, `find_latest_image`, `save_report` 정적 역어셈블. SELECT 필드, 유형별 코드, 크롭 및 보고서 조건 확인. | 정적 흐름 확인, Windows 실행 미검증 |
| `com_kt(1).exe` | Windows x64 PyInstaller GUI, Python 3.13 | `fetch_data`, `update_data`, `save_data`, `find_latest_image`, `save_image` 정적 역어셈블. SELECT/UPDATE와 이미지 흐름 확인. | 정적 흐름 확인, Windows 실행 미검증 |
| `myCustomer.db` | SQLite 3, 18개 테이블 | 아래 스키마/건수/관계를 읽기 전용으로 조사함. 운영 자료로 간주하고 수정하지 않음. | 원본 구조 확인 |
| `uscan(20261017-075504).skt` | SQLite 3, 18개 테이블 | `myCustomer.db`와 별도 파일이며 스키마는 동일, 일부 스캔 데이터가 다름. 읽기 전용으로 조사함. | 원본 구조 확인 |
| `sk_report.txt` | UTF-8 key/value, CRLF | 정확히 13줄·13개 고유 필드. 필드명은 아래 참조. 실제 인물/이미지 값은 이 문서에 복사하지 않음. | 형식 확인 |
| `kt_idcrop.txt` | JSON 유사 필드 조각 | `image` 키의 JPEG Base64와 `imageSize` 필드. 독립 JSON 문서가 아니라 선행 쉼표가 있는 조각. | 형식 확인 |
| `kt_save.txt` | JSON 유사 필드 조각 | `imageData` 키의 JPEG Base64와 `imageDataSize` 필드. 독립 JSON 문서가 아니라 선행 쉼표가 있는 조각. | 형식 확인 |

## 확인된 데이터 구조

두 SQLite 파일은 각각 18개 테이블을 가지며 테이블 이름과 컬럼 정의는 같다. 파일 크기와 SHA-256이 다르고, `uscan(20261017-075504).skt`에 `T_SCAN_INDEX`, `T_SCAN_SUBINDEX`, `T_SCAN_PAPER`, `T_SCAN_RESOURCES`의 레코드가 각각 1개 더 있다. 나머지 테이블의 레코드 건수는 조사 시점에 같았다. 두 파일의 공통 레코드 원문은 보고서에 노출하지 않았다.

| 테이블 | myCustomer 행 수 | uscan 행 수 | 관계/비고 |
|---|---:|---:|---|
| `T_SCAN_INDEX` | 399 | 400 | `INDEX_KEY` 기준 상위 인덱스 |
| `T_SCAN_SUBINDEX` | 399 | 400 | `SUB_INDEX_KEY` 및 `INDEX_KEY`로 상위 인덱스 연결 |
| `T_SCAN_PAPER` | 399 | 400 | `TMP_ELEMENT_ID`, `SUB_INDEX_KEY`를 갖는 문서/이미지 레코드 |
| `T_SCAN_RESOURCES` | 399 | 400 | `TMP_ELEMENT_ID` 기준 이미지 리소스 |
| `T_SCAN_TEMP` | 50 | 50 | 확정 전 임시 문서 레코드, 82개 컬럼 |
| `T_SCAN_UNKNOWN` | 0 | 0 | 미분류 레코드 테이블 |
| `FORM_TBL` / `GROUP_TBL` / `ITEM_TBL` | 1,991 / 5,878 / 10,162 | 동일 | 양식/그룹/항목 메타데이터 |
| `T_CODE_OCR` / `T_CODE_USCAN` | 6,811 / 4,003 | 동일 | OCR/업무 코드 자료 |

주요 스캔 테이블 스키마는 `T_SCAN_INDEX` 8개, `T_SCAN_SUBINDEX` 6개, `T_SCAN_PAPER` 80개, `T_SCAN_RESOURCES` 5개, `T_SCAN_TEMP` 82개 컬럼이다. 다수 컬럼은 TEXT이며 nullable이다. 실제 데이터에서 NULL과 빈 문자열이 서로 구분되어 나타난다. 두 DB 모두 선언된 외래 키는 없다. 대신 조사한 두 DB 모두 `T_SCAN_PAPER → T_SCAN_SUBINDEX → T_SCAN_INDEX`와 `T_SCAN_PAPER ↔ T_SCAN_RESOURCES`의 키 연결에서 고아 레코드를 찾지 못했다. 구현 시 이 연결은 명시적으로 보존해야 한다.

두 DB 전체 18개 테이블의 컬럼 순서, 선언 자료형, NULL 허용값, 기본값, PK 순서, 실제 NULL 셀/빈 TEXT 셀 수는 [`db_schema_columns.csv`](db_schema_columns.csv)에 기록했다. 이 파일에는 레코드 값은 포함하지 않는다.

참고용 구형 U.Scan 설치 백업의 MyBatis SQLite 매퍼에서 확인한 동작:

- 인덱스 목록은 `T_SCAN_INDEX`, 하위 인덱스 목록은 `T_SCAN_SUBINDEX`에서 조회하며 `DATA_DIV_CD`, `INDEX_KEY`/`SUB_INDEX_KEY`, `APPLF_SEQ` 조건과 키 정렬을 사용한다.
- 문서 목록은 `T_SCAN_PAPER`를 하위 인덱스 및 상위 인덱스와 조인하고, 발송 대기(`SEND_YN = 'N'`) 조건 및 양식/스캔 메타데이터를 결합한다.
- `T_SCAN_TEMP.insertIdCard`는 OCR 코드로 `T_CODE_OCR`에서 `FORM_CD`와 `SDOC_CD`를 조회해 저장하며, 후속 확인 단계는 임시 행을 `T_SCAN_PAPER`에 기록한다.
- 이미지 데이터는 `T_SCAN_RESOURCES`에서 별도 조회/저장된다. 매퍼는 삭제/정리 쿼리도 포함하므로 통합 구현 전 각 작업의 범위를 보존해야 한다.
- 이는 구형 백업의 Java/MyBatis 흐름이다. 제공된 네 개 EXE와 동일 버전·동일 동작이라고 간주하지 않는다.

첨부 EXE는 정적으로 분석했으며 Windows에서 실행하지 않았다. 구형 U.Scan MyBatis 매퍼 결과는 첨부 EXE와 버전 동일성을 단정하지 않는다. Python GUI 메인 함수 흐름은 아래 역어셈블 표에 구체적으로 기록한다.

복구된 문자열에서 추가 확인된 접점 GUI 동작은 접점 파일 로드/저장, 새 접점/수정/삭제, 스캔 설정 경로 선택, 선택한 접점으로 하위 프로그램 실행 및 결과 파일 복사다. MOVEWORD에는 `T_SCAN_PAPER` 전체 레코드를 대상으로 `SCAN_ID`, `SEND_ORG_ID`, `SCAN_NM`, `IP_ADDR`, `MAC_ADDR`, `ID_SCANR_SER_NUM`을 갱신하는 SQL 문자열이 있다(`WHERE TMP_ELEMENT_ID IS NOT NULL`). 이 변경을 모두 보존할지, 신분확인 관련 값 갱신을 제외할지는 별도 안전 규칙 검토가 필요하며, 새 프로그램의 운영 DB에 그대로 적용하지 않는다.

추가로 Node pkg 스냅샷의 일반 문자열 영역에서 `skt` 메인 스크립트의 소스 행을 회수했다. 실제 호출 순서는 `device() → scan() → migrate()`이며, `device()`는 `EZS_TWAIN_SELECT` 명령, `scan()`은 `EZS_LIB_SCAN` 명령에 DB/라이브러리/데이터 폴더/타임스탬프/접점 코드를 전달한다. `migrate()`는 `T_SCAN_TEMP`에서 `TMP_ELEMENT_ID DESC LIMIT 1`로 한 건을 읽고 `T_SCAN_INDEX`, `T_SCAN_SUBINDEX`, `T_SCAN_RESOURCES`, `T_SCAN_PAPER` 순으로 INSERT한 뒤 해당 임시 행을 삭제한다. INSERT 컬럼을 실제로 세어 `T_SCAN_PAPER` 대상은 59개임을 확인했다. 본문에서 코드 유형 `R/D/S`, `MSK_POS` 검사 및 OCR 필드 수정 입력도 확인된다. 이 결과는 실행 추적이 아닌 EXE 내 문자열/소스 행 복구에 근거하므로 환경 변수 값과 실패 시 DB 상태는 Windows에서 별도 검증 대상이다.

복구된 `migrate()`에는 JPG OCR이 측정할 수 없는 장치/검증 컬럼도 들어 있다. 예를 들어 `ID_SCANR_USE_YN='Y'`, 드라이버명 고정값, IR/UV/TR 값, `SELF_YN='Y'`, 난수 `ID_COLOR_SCORE`가 설정된다. 이 값들은 원본 신규 INSERT 경로의 내용이며 현재 KT 저장은 그 INSERT를 사용하지 않는다. 원본 59개 컬럼의 출처는 [`DATABASE_FIELD_AUDIT.md`](DATABASE_FIELD_AUDIT.md)에 기록했다.

**원본 `skt.exe` 경로**는 `device()`, `scan()`, `pSktLibCbnRun.exe`, `T_SCAN_TEMP` 조회, 네 운영 테이블 INSERT 및 처리 TEMP DELETE를 호출한다. 원본 `T_SCAN_PAPER` 59컬럼 INSERT는 새 문서를 등록하는 경로다. 현재 NEW_WORK의 KT 저장은 새 문서를 만들지 않고, 기존 최신 `T_SCAN_PAPER` 행의 OCR 필드와 코드표 분류 메타데이터를 갱신한다. 함께 새 `TMP_ELEMENT_ID`를 발급해 연결된 resource 키를 갱신하며, 다른 테이블이나 기존 메타데이터는 보존한다. OCR 검토 객체의 `job_id`는 DB ID로 쓰지 않는다. SK는 OCR 검토 객체에서 직접 보고서를 생성한다.

`com_kt`와 `com_sk`의 Python 3.13 메인 모듈 바이트코드를 xdis로 정적 역어셈블해 함수 본문과 분기 순서를 추가 복구했다. 바이너리를 실행한 것은 아니다. 복구한 구체 동작은 아래에 기록한다.

## com_kt / com_sk / work_250106 호출 흐름 복구

| 실행 파일/함수 | 복구된 동작 | NEW_WORK 이식 |
|---|---|---|
| `com_kt.fetch_data` | `T_SCAN_PAPER`에서 10개 필드(`RECOG_SUB_CD`, `ID_TYPE`, `CTZ_CORP_NUM`, `CUST_NM`, `ISUE_DT`, `DRV_LICEN_ISUE_NUM`, `ISUE_PLC`, `NATION_CL_CD`, `ADDR`, `MSK_POS`)를 `ORDER BY SCAN_DT DESC LIMIT 1`로 읽음 | 동일 최신행 조회를 복사 DB 안에서 수행 |
| `com_kt.update_data` | 위 10개 값 전체를 `UPDATE T_SCAN_PAPER ... WHERE SCAN_DT=(SELECT MAX(SCAN_DT)...)`로 갱신. GUI가 바꾸는 값은 이름, 주민번호, 주소, 발급일, 면허번호, 발급기관 6개이고 나머지 4개는 조회 행의 값을 유지. 타임스탬프 최댓값 동률 행은 모두 UPDATE 대상 | 동일 기존 OCR 컬럼·최대 SCAN_DT 조건. 추가로 `ADDR_ORIGIN`을 검토 주소로 갱신하고, 국가유공자증 코드 `1302`일 때 `WLF_NUM`과 `WLF_NUM_ORIGIN`을 검토값으로 갱신. 운영 원본 보호를 위해 DB 백업본을 갱신해 `myCustomer.db` 생성 |
| `com_kt.find_latest_image` | 하위 폴더에서 수정시각 최신 m-suffix 이미지를 찾아 카드 중앙 크롭을 `CR.jpg`로 생성 | 현재 UI에서 선택한 원본을 사용하고 얼굴 사진 영역을 crop해 `CR.jpg`로 저장 |
| `com_kt.save_data` | 원본을 JPEG quality 100으로 `ORG.jpg`, 카드 중앙 크롭을 JPEG quality 100으로 `CR.jpg`에 재인코딩한 뒤 DB 업데이트 진행 | `ORG.jpg`는 선택 원본, `CR.jpg`는 얼굴 사진 crop으로 저장. 두 파일을 `C:\IMAGE`에 같은 이름으로 덮어씀 |
| `com_sk.fetch_data` / `save_data` | 위 10개 컬럼 최신행을 조회하고 문서유형 선택에 따라 `(RECOG_SUB_CD, ID_TYPE)`를 거주자 `(1001,R)`, 운전면허 `(1102,D)`, 외국인 `(1401,F)`, 복지 `(1301,S)`, 보훈 `(1302,N)`으로 구성. 외국인은 `NATION_CL_CD='F'`, `MSK_POS=''` 설정 | 문서 유형 선택 컨트롤과 코드값 입력 연결. Windows 원본 대비 검증 필요 |
| `com_sk.save_report` | `sk_report.txt`에 11개 텍스트 매핑을 순서대로 `quote_plus` 기록. `RECOG_SUB_CD` 뒤에 `recogSubCd` 추가 기록. 뒤에 `base64DATA`; 코드 `1001/1101/1102`이고 크롭이 유효할 때만 `idCrop` 기록 | 조건부 `idCrop`, UTF-8/CRLF 및 키 순서를 구현 |
| `work_250106.run_program` | SK는 `com_sk.exe` 종료 후 저장경로의 `classes/sk_report.txt`로 복사. KT는 `com_kt.exe` 종료 후 `C:\\work\\myCustomer.db`, `ORG.jpg`, `CR.jpg`를 저장경로의 `IMAGE`로 복사 | 현재 요청 범위는 로컬 `C:\\IMAGE`의 DB와 두 이미지 저장이며, 접점 공유 전달 여부는 별도 |

`work_250106` 접점 레코드는 접점명, `SCAN_ID`, `SEND_ORG_ID`, `SCAN_NM`, `IP_ADDR`, `MAC_ADDR`, `ID_SCANR_SER_NUM`, 저장경로를 포함한다. 별도 `modify_file`은 `TMP_ELEMENT_ID IS NOT NULL`인 `T_SCAN_PAPER` 행 전체에서 스캐너/조직 식별 컬럼 6개를 변경한다. 이 글로벌 UPDATE는 개인정보/업무 DB에 미칠 영향 때문에 아직 이식하지 않았다. 원본 접점의 설정 컬럼 입력과 실제 업무 연계를 더 확인할 때까지 공유폴더 및 DB 템플릿 경로만 NEW_WORK 접점 설정에 저장한다.

크롭 입력은 원본 앱이 파일 대화상자로 고른 이미지가 아니라 원본 `data` 디렉터리에서 찾은 최신 m-suffix 파일이다. NEW_WORK는 사용자가 선택한 JPG 하나만 작업별 격리 폴더에 `*_m.jpg`로 복사한 뒤 같은 함수를 적용해 다른 작업의 이미지가 mtime 경쟁에 끼어들지 않게 했다. JPG를 물리 스캐너 이미지와 동등한 인증/센서 검증으로 표시하지 않는다.

신규 SK 보고서 경로에는 paper SELECT가 없다. `new_work.database.reader`는 KT의 기존 OCR 필드와 국가유공자 보훈번호 `WLF_NUM` 최신행 SELECT를 제공한다. 신규 SK 보고서는 DB 파일이 없는 환경에서도 생성되며, 합성 입력에서 SQLite 접근 없이 실행되는 테스트가 있다.

두 DB를 선언 PK 대신 실제 업무 키(`INDEX_KEY`, `SUB_INDEX_KEY`, `TMP_ELEMENT_ID`)로 다시 비교했다. 공통 레코드는 모두 동일했고 `uscan.skt`에만 한 계열(인덱스 1, 하위 인덱스 1, 문서 1, 리소스 1)이 추가되어 있다. 추가 문서의 비개인 코드 조합은 `ID_TYPE=R`, `RECOG_SUB_CD=1001`, `FORM_CODE=A01`이며, 미수정 임시 테이블은 두 DB에서 동일하다. 따라서 레코드 차이는 완성 문서 1건이 더 있는 상태로 확인되지만, 제공 파일만으로 그 레코드가 언제/어떤 실행으로 추가됐는지는 확정할 수 없다. `com_kt`에서 이 1건을 삭제했다고 단정하지 않는다.

## SK 보고서와 KT 출력 형식

현재 KT OCR 저장 버튼은 `com_kt`의 기존행 편집 흐름을 적용한다. 최신 `T_SCAN_PAPER`의 OCR 값과 신분증 유형 코드를 읽어 반영한다. 검토 주소는 `ADDR`와 `ADDR_ORIGIN`에 저장하고, 국가유공자증은 검토 보훈번호를 `WLF_NUM`과 `WLF_NUM_ORIGIN`에 저장한다. 선택한 코드는 활성 `T_CODE_OCR`와 대조해 `FORM_CODE`, `APPLF_SEQ`, `SDOC_CD`, `DOC_TYP_CD`도 갱신한다. `unique()`로 만든 새 `TMP_ELEMENT_ID`는 paper 행과 연결된 `T_SCAN_RESOURCES` 행에 함께 UPDATE한다. 이미지 Base64 및 나머지 컬럼은 유지한다. `T_SCAN_TEMP`, `T_SCAN_INDEX`, `T_SCAN_SUBINDEX`, `T_SCAN_UNKNOWN`, `T_SCAN_CONF`는 수정하지 않으며 `skt.exe`의 `migrate()`/59컬럼 신규 INSERT 경로도 호출하지 않는다. OCR의 `OcrResult.job_id`는 UUID로 UI 작업을 추적할 뿐, DB `TMP_ELEMENT_ID`가 아니다.

`sk_report.txt`의 13개 필드명은 다음과 같다: `kaitIdType`, `recogSubCd`, `idType`, `ctzCorpNum`, `custNm`, `isueDt`, `drvLicenIsueNum`, `isuePlc`, `nationClCd`, `addr`, `mskPos`, `base64DATA`, `idCrop`. 파일은 한 줄당 `key=value`이며 일부 데이터 값에는 줄바꿈이 포함되지 않았다. 현 샘플에서 `drvLicenIsueNum` 값은 빈 문자열이었다. 두 이미지 필드는 URL 디코딩 뒤 JPEG이며 각각 1008×640 및 340×406 픽셀임을 헤더에서 확인했다. 이 규격을 다른 문서 유형에 일반화하지 않는다.

두 KT 파일의 이미지 Base64는 모두 30,015바이트 JPEG(320×420)로 디코딩되고 바이트가 같다. 파일 간 키 이름이 `image`와 `imageData`로 다르고, 크기 필드는 `imageSize`와 `imageDataSize`로 다르다. 두 파일은 독립 JSON이 아닌 속성 조각이며, `new_work/carrier/kt_payload.py`에서 실제 키와 선언 크기를 검증해 읽는다. 현재 저장 흐름은 이 payload 대신 사용자가 선택한 JPG로 `ORG.jpg`와 얼굴 사진 `CR.jpg`를 만든다.

## 참고 가능한 OCR 코드

- Downloads의 `SK_260607_readable.py`는 원본과 동일한 OCR 소스가 아니라, 별도 `SK_260607` 실행 파일의 불완전한 디컴파일을 바탕으로 만든 비실행용 재구성이다. 보고서 파싱과 브라우저 `r.data` 필드 매핑 참고에는 쓸 수 있지만, 일반 OCR 엔진이나 `skt(5).exe` 원본으로 취급하지 않는다.
- Downloads의 `IDCardSellphyCrop-printfix`는 카드 방향 판별/크롭/인쇄 프로젝트다. Tesseract 방향 판별 의존성은 있으나, 첨부된 자체 OCR의 필드 인식 모듈로 확인되지 않았다.
- `skt_main_extracted.js`는 확인한 경로에 없었다. `skt(5).exe`의 main.js 스냅샷은 식별했지만 소스 추출/복구가 아직 되지 않았다.

## OCR 구현 결정

PaddleOCR 3.x 어댑터를 추가하고 `lang="korean"`, 속도 우선 기본값 `ocr_version="PP-OCRv3"`을 사용한다. PaddleOCR의 모델 표는 한국어 PP-OCRv3 모바일 인식기의 CPU 표준 추론을 8.76ms, PP-OCRv5를 21.20ms로 제시하며, 평균 정확도는 각각 60.21%, 88.0%다. 이에 따라 기본은 v3로 하고 환경 변수 `NEW_WORK_OCR_VERSION=PP-OCRv5`로 정확도 우선 모델을 선택하게 했다. 해당 시간은 문자 인식 모듈만의 표이며 JPG 전체 파이프라인이나 Windows 대상 PC의 속도로 간주하지 않는다. 실제 detector 포함 속도와 정확도는 해당 PC·샘플에서 검증해야 한다. [공식 한국어 모델 표](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html)

현재 어댑터는 PaddleOCR 줄 텍스트/신뢰도를 `OcrResult.lines`에 보존한다. `values`의 업무 필드 자동 분류는 ID별 레이아웃 검증 자료가 없어 비워 두며, GUI에서 줄을 직접 선택해 편집 입력칸에 넣을 수 있다. 이 동작은 OCR 엔진 실물 런타임이 아닌 가짜 엔진 테스트로 파싱/연결을 확인한다.

## SK report 출력 모듈

`new_work/carrier/sk_report.py`는 확인된 텍스트 키 순서, `quote_plus` 값 인코딩, UTF-8, CRLF 및 마지막 줄바꿈을 직렬화한다. 입력에서 모든 텍스트 키를 요구하고 `None`을 빈 문자열로 조용히 바꾸지 않는다. `base64DATA`는 항상 기록하며 `idCrop`은 유형 코드가 `1001/1101/1102`이고 crop 파일이 있을 때만 기록한다. 크롭은 `new_work.image.legacy_crop`에서 생성한다.

제공 `idscanner/sk_report.txt`를 메모리에서 파싱 후 같은 직렬화기로 재생성했을 때 원본 파일 바이트와 일치함을 확인했다. 명령 출력에는 개인정보를 노출하지 않았다. 이 비교는 출력 형식을 검증하지만 `com_sk.exe`의 이미지 크롭/필드 생성 흐름 전체를 기능 동등하게 검증한 것은 아니다.

## 기능별 이식 목록 및 현황

| 기능 | 확인/구현 현황 | 다음에 필요한 작업 |
|---|---|---|
| JPG 입력 및 PaddleOCR | 어댑터 및 UI 연결 | Windows에서 Paddle 런타임/모델 설치 후 실제 JPG 인식·속도 검증 |
| OCR 문자 표시·수정·검토 | 줄 단위 원문/점수 UI 연결 | 엔진 결과 기반 실물 검증, ID 레이아웃 자동 분류는 별도 미구현 |
| 신규 JPG의 SK 보고서 생성 | DB 없는 직접 생성 요구 확인 | SK 모듈이 SQLite 없이 작동하는 테스트 유지 |
| 기존 `skt.exe` DB 이관 | 원본 호출·59컬럼 SQL 정적 복구, 새 JPG 경로에는 불필요 | 신규 JPG 처리에서 사용하지 않음. 컬럼 분류는 `DATABASE_FIELD_AUDIT.md` |
| 인덱스·하위 인덱스·문서·리소스 관계 | DB/구형 매퍼 원본 동작 일부 확인, 통합 이식 미완료 | 제공 EXE와 구형 백업 간 버전 차이 확인 후 독립 DB 복사본에서 대조 |
| SK 보고서/이미지 | 함수 흐름·필드순서·조건부 crop 원본 동작 확인. 직렬화, 선택 JPG의 격리 및 원본 좌표 크롭 이식 | 지정 유형별 선택값 반영, 실제 첨부 `com_sk.exe`와 Windows 기능 비교 |
| KT 최신행 UPDATE / 이미지 | SELECT·10개 SQL 대상·6개 GUI 입력+4개 유지·UPDATE 조건·JPEG/크롭 확인. 격리 DB 출력 이식 | 실제 EXE Windows 대조; 합성 DB에서 타임스탬프 동률 행도 시험 |
| GUI / 접점 설정 / 전달 | 실행·복사 흐름 및 목적 상대경로 확인. 공유경로, KT DB 경로 관리와 SK/KT 버튼 이식 | 원본 접점 8개 필드 중 스캐너 설정 입력, Windows 공유폴더에서 호환 확인 |
| SK / KT 버튼 | NEW_WORK 검토 완료 상태와 연결 | Windows에서 보고서 및 KT 산출물 받아들이는 업무페이지 통합 검증 |
| LG 처리 | 범위 제외 | 사용자의 최신 지시에 따라 구현하지 않음 |
| 매장 공유폴더 결과 전달 | 접점 경로 UI, SK/KT 상대경로 및 덮어쓰기 확인 구현 | 실제 SMB 공유 권한/경로 검증 |
| 단일 Windows EXE 빌드 | 빌드 BAT 작성 | Windows x64에서 실행·Paddle 런타임 수집·산출물 검증 |

## 환경 및 검증 제한

현재 분석 호스트는 macOS다. Windows EXE를 실행하지 않았고, Windows GUI 호환성이나 빌드를 확인하지 않았다. Python 3.13 바이트코드는 xdis로 정적 역어셈블했으며 실행 추적은 아니다. DB 원본은 URI `mode=ro`로 조회했고 갱신은 합성/독립 복사 DB에서만 검사했다. `DATABASE_FIELD_AUDIT.md`는 회수한 JS의 실제 59개 INSERT 인자 출처와 JPG 경로 금지값을 분류한다. 기능 동등성 검증은 운영 원본이 아닌 독립 복사본에서 해야 한다.

다음 단계는 Windows x64/Python 3.12 환경에서 PaddleOCR 실제 추론, onedir 빌드 및 원본 EXE와의 산출물 동등성을 검증하는 것이다. LG 원본은 현재 범위에서 필요하지 않다.

# `skt.exe` T_SCAN_PAPER INSERT 필드 감사

## 적용 범위

이 표는 `idscanner/skt(5).exe`의 pkg snapshot에서 회수한 `migrate()` SQL 및 값 배열을 기준으로 하며, 원본 `main.js`가 `T_SCAN_PAPER`에 59개 컬럼을 INSERT한다는 사실을 기록한다. 현재 NEW_WORK의 KT 저장은 사용자가 확인한 기존 데이터가 있다는 지시에 따라 이 INSERT/migrate 경로를 호출하지 않고, 기존 최신 `T_SCAN_PAPER` 행에 `com_kt.update_data`와 같은 10개 필드를 UPDATE한다. 새 TEMP/PAPER/INDEX/RESOURCE 행을 만들거나 삭제하지 않는다.

분류: `임시행 전달`은 `T_SCAN_TEMP` row에서 읽음, `검토값`은 기존 migrate에서 검토 후 변경값 사용, `환경값`은 접점/호스트 설정, `생성값`은 프로그램 실행 중 계산, `상수`는 원본 코드 고정값. 표의 코드는 원본 구현 설명이지 새 JPG에 이 값을 채우라는 지시가 아니다.

| # | T_SCAN_PAPER 컬럼 | 원본 값 출처/분류 | 원본 코드 및 안전성 메모 |
|---:|---|---|---|
| 1 | `TMP_ELEMENT_ID` | `unique()` / 생성값 | 새 키 생성 |
| 2 | `SCAN_DT` | `row['SCAN_DT']` / 임시행 전달 | 스캔 시각 |
| 3 | `SCAN_ID` | `SCAN_ID` / 환경값 | 접점 설정 |
| 4 | `INPUT_TYPE` | `row['INPUT_TYPE']` / 임시행 전달 | 입력 유형 |
| 5 | `USE_YN` | `row['USE_YN']` / 임시행 전달 | 임시행 값 그대로 |
| 6 | `IMG_SEQ` | `row['IMG_SEQ']` / 임시행 전달 | 이미지 순번 |
| 7 | `FORM_CODE` | `row['FORM_CODE']` / 임시행 전달 | 서식 코드 |
| 8 | `SEND_TYPE` | `'PS'` / 상수 | 발송 유형 |
| 9 | `SEND_ORG_ID` | `SEND_ORG_ID` / 환경값 | 접점 설정 |
| 10 | `SCAN_NM` | `SCAN_NM` / 환경값 | 접점 설정 |
| 11 | `RECOG_SUB_CD` | `row['RECOG_SUB_CD']` / 임시행 전달 | 인식 서식 코드 |
| 12 | `AUTO_YN` | `row['AUTO_YN']` / 임시행 전달 | 임시행 값 그대로 |
| 13 | `SDOC_CD` | `row['SDOC_CD']` / 임시행 전달 | 문서 코드 |
| 14 | `REG_CHK_YN` | `'Y'` / 상수 | 원본과 같은 상수값을 호환 목적으로 기록 |
| 15 | `APPLF_INFO_CD` | `row['APPLF_INFO_CD']` / 임시행 전달 | 업무 메타데이터 |
| 16 | `APPLF_SEQ` | `row['APPLF_SEQ']` / 임시행 전달 | 업무 순번 |
| 17 | `MSK_POS` | `row['MSK_POS']` / 임시행 전달 | 마스킹 위치 |
| 18 | `MANL_MSK_YN` | `row['MANL_MSK_YN']` / 임시행 전달 | 임시행 값 그대로 |
| 19 | `OCR_MSK_YN` | `row['OCR_MSK_YN']` / 임시행 전달 | 임시행 값 그대로 |
| 20 | `ID_KEY` | `unique()` / 생성값 | 내부 식별자 |
| 21 | `BIND_DTM` | `row['SCAN_DT']` / 임시행 전달 | 스캔시각을 복사 |
| 22 | `ID_LOG_KEY` | `unique(SEND_ORG_ID)` / 생성값 | 접점 ID를 재료로 생성 |
| 23 | `NATION_CL_CD` | `'I'` / 상수 | 일반 OCR 추론으로 만들지 않음 |
| 24 | `ID_TYPE` | `row['ID_TYPE']` / 임시행 전달 | 유형 코드 |
| 25 | `CTZ_CORP_NUM` | `row['CTZ_CORP_NUM_CHANGED']` / 검토값 | 사람이 수정한 신분증 번호 |
| 26 | `CUST_NM` | `row['CUST_NM_CHANGED']` / 검토값 | 사람이 수정한 이름 |
| 27 | `ISUE_DT` | `row['ISUE_DT_CHANGED']` / 검토값 | 사람이 수정한 발급일 |
| 28 | `ID_TYP_UPD_YN` | `row['ID_TYP_UPD_YN']` / 임시행 전달 | OCR/수정 상태 |
| 29 | `ID_IR_VALUE` | `row['ID_VALUE']` / 장치 관련 | ID_TYPE별 원본 상수 문자열을 호환 목적으로 기록하며 실제 IR 측정값은 아님 |
| 30 | `ID_UV_VALUE` | `row['ID_VALUE']` / 장치 관련 | ID_TYPE별 원본 상수 문자열을 호환 목적으로 기록하며 실제 UV 측정값은 아님 |
| 31 | `ID_SCANR_USE_YN` | `'Y'` / 상수 | 원본과 같은 상수값을 기록하며 실제 스캐너 사용 여부를 확인한 결과는 아님 |
| 32 | `SCANR_DRV_NM` | `'VOIM ID Card Scanner Source'` / 상수 | 원본과 같은 장치명 상수값을 기록 |
| 33 | `ID_OCR_UPD_YN` | `row['ID_OCR_UPD_YN']` / 임시행 전달 | OCR 수정 상태 |
| 34 | `OP_TYP` | `'02'` / 상수 | 원본 업무 코드 |
| 35 | `ISUE_PLC` | `row['ISUE_PLC_CHANGED']` / 검토값 | 사람이 수정한 발급기관 |
| 36 | `DRV_LICEN_ISUE_NUM` | `row['DRV_LICEN_ISUE_NUM_CHANGED']` / 검토값 | 사람이 수정한 면허번호 |
| 37 | `ADDR` | `row['ADDR_CHANGED']` / 검토값 | 사람이 수정한 주소 |
| 38 | `IP_ADDR` | `await ipAddress()` / 실행환경 | 현재 호스트 주소 |
| 39 | `MAC_ADDR` | `await macAddress()` / 실행환경 | 현재 호스트 MAC 주소 |
| 40 | `ID_TR_VALUE` | `row['ID_VALUE']` / 장치 관련 | ID_TYPE별 원본 상수 문자열을 호환 목적으로 기록하며 실제 TR 측정값은 아님 |
| 41 | `FORM_ALIGN_YN` | `'Y'` / 상수 | 원본과 같은 상수값을 기록하며 실제 정렬 검증 결과는 아님 |
| 42 | `FILE_ID` | `row['FILE_ID']` / 임시행 전달 | 리소스 파일 키 |
| 43 | `DOC_TYP_CD` | `row['DOC_TYP_CD']` / 임시행 전달 | 문서 유형 |
| 44 | `SELF_YN` | `'Y'` / 상수 | 자체/진위 판정 의미를 확인하지 않고 재사용 금지 |
| 45 | `DATA_DIV_CD` | `'PP'` / 상수 | 데이터 구분 |
| 46 | `ROTATE` | `row['ROTATE']` / 임시행 전달 | 원본 회전값 |
| 47 | `NATION_CL_CD_ORIGIN` | `'I'` / 상수 | 원본값 컬럼에 고정 코드 기록 |
| 48 | `ID_TYPE_ORIGIN` | `row['ID_TYPE']` / 임시행 전달 | 원본 유형 코드 |
| 49 | `CTZ_CORP_NUM_ORIGIN` | `row['CTZ_CORP_NUM']` / 임시행 전달 | 검토 전 번호 |
| 50 | `CUST_NM_ORIGIN` | `row['CUST_NM']` / 임시행 전달 | 검토 전 이름 |
| 51 | `ISUE_DT_ORIGIN` | `row['ISUE_DT']` / 임시행 전달 | 검토 전 발급일 |
| 52 | `ISUE_PLC_ORIGIN` | `row['ISUE_PLC']` / 임시행 전달 | 검토 전 발급기관 |
| 53 | `DRV_LICEN_ISUE_NUM_ORIGIN` | `row['DRV_LICEN_ISUE_NUM']` / 임시행 전달 | 검토 전 면허번호 |
| 54 | `ADDR_ORIGIN` | `row['ADDR']` / 임시행 전달 | 검토 전 주소 |
| 55 | `SWAP` | `'FALSE'` / 상수 | 원본 문자열 값 |
| 56 | `ID_COLOR_SCORE` | `Math.floor(Math.random() * 30 + 60)` / 난수 | 원본의 60–89 난수 생성 규칙을 그대로 사용 |
| 57 | `ETC_PAPER_YN` | `'N'` / 상수 | 기타 문서 표시 |
| 58 | `ID_SCANR_SER_NUM` | `ID_SCANR_SER_NUM` / 환경값 | 접점 설정의 장치 시리얼 |
| 59 | `SUB_INDEX_KEY` | `subGroupId` / 생성값 | 하위 인덱스와 paper 관계 키 |

## 원본 이관 경로와 현재 NEW_WORK 적용

- 전체 `T_SCAN_PAPER`는 80컬럼이지만 이 INSERT는 59컬럼만 지정한다. 나머지 21컬럼을 임의 초기화하거나 채우지 않는다.
- 원본 이관은 `T_SCAN_INDEX`, `T_SCAN_SUBINDEX`, `T_SCAN_RESOURCES`, `T_SCAN_PAPER` 생성과 처리한 임시행 삭제를 포함한다. 원본에서는 스캐너가 `T_SCAN_TEMP`를 준비한다.
- 현재 KT 저장은 기존 최신 `T_SCAN_PAPER` 행의 OCR 필드 10개와 문서 분류 메타데이터(`FORM_CODE`, `APPLF_SEQ`, `SDOC_CD`, `DOC_TYP_CD`)를 UPDATE한다. `T_CODE_OCR.OCR_CD`에서 선택한 신분증 코드에 대응하는 메타데이터를 읽으며, `ID_TYPE`은 앱의 신분증 코드 매핑을 사용한다. 동시에 새 `TMP_ELEMENT_ID`를 `unique()` 규칙으로 만들고, `T_SCAN_PAPER`와 같은 키를 쓰는 `T_SCAN_RESOURCES` 행의 키도 한 트랜잭션에서 함께 바꾼다. 새 행이나 리소스 Base64는 INSERT/수정하지 않는다. `T_SCAN_TEMP`, `T_SCAN_INDEX`, `T_SCAN_SUBINDEX`, `T_SCAN_CONF`, `T_SCAN_UNKNOWN`은 이 저장에서 건드리지 않는다. 이는 `skt.exe`의 신규 문서 이관이 아니라 `com_kt`의 기존행 수정 흐름을 유지하면서 분류값과 문서/이미지 키 연결을 갱신하는 동작이다.
- 현재 구현은 `SCAN_ID`, `SEND_ORG_ID`, `SCAN_NM`, `IP_ADDR`, `MAC_ADDR`, `ID_SCANR_SER_NUM`을 DB의 기존 최신 paper 행에서 가져오며, `SEND_ORG_ID`와 환경 변수가 있으면 환경값을 우선한다.
- 새 `TMP_ELEMENT_ID` 생성은 원본 `unique()`의 기록된 규칙을 따른다: 로컬 시각 `YYYYMMDDHHmmss` + `0-9A-Z` 임의 문자 2개 + 최신 행 `SEND_ORG_ID` 앞 6자. 기존 `ID_KEY`와 `ID_LOG_KEY`는 원본 `com_kt.update_data`처럼 유지한다. 현재 `OcrResult.job_id`는 UUID 작업 추적값이며 DB 키로 쓰지 않는다.

## 관련 테이블 전수 확인

`NEW_WORK/test.db`를 읽기 전용으로 열어 스키마, 행 수, PK/유니크 인덱스, 외래 키 선언 및 키 연결을 확인했다. DB에는 관련 테이블의 선언된 외래 키가 없으므로 코드에서 연결 무결성을 유지해야 한다.

| 테이블 | 컬럼 수 / 행 수 | 이 흐름에서 의미 있는 키·컬럼 | 저장 버튼 동작 |
|---|---:|---|---|
| `T_SCAN_CONF` | 13 / 3 | `C_SCAN_DIV`, `SCAN_DRIVER_NM` 등 장치 설정 | 미변경 |
| `T_SCAN_TEMP` | 82 / 54 | PK `TMP_ELEMENT_ID`; OCR/스캔 임시값, `ID_KEY`, `ID_LOG_KEY`, `SUB_INDEX_KEY`, `FILE_ID` | 미변경. OCR `job_id`와 대응하지 않음 |
| `T_SCAN_INDEX` | 8 / 411 | 유니크 `INDEX_KEY` | 미변경 |
| `T_SCAN_SUBINDEX` | 6 / 411 | 유니크 `SUB_INDEX_KEY`, 상위 `INDEX_KEY` | 미변경. 기존 paper 연결 유지 |
| `T_SCAN_PAPER` | 80 / 411 | PK `TMP_ELEMENT_ID`; OCR 10필드, `FORM_CODE`, `APPLF_SEQ`, `SDOC_CD`, `DOC_TYP_CD`, `ID_KEY`, `ID_LOG_KEY`, `SUB_INDEX_KEY`, `FILE_ID`, 기타 원본·스캐너 메타데이터 | 최신 기존 행의 OCR 10필드, 코드표 분류 메타데이터, `TMP_ELEMENT_ID` UPDATE |
| `T_SCAN_RESOURCES` | 5 / 411 | PK `TMP_ELEMENT_ID`; `VALUE` 이미지 Base64, 날짜/문서/구분 코드 | 기존 이미지 값을 보존하고 PK 키만 paper와 함께 변경 |
| `T_SCAN_UNKNOWN` | 80 / 0 | PK `TMP_ELEMENT_ID` 및 paper 유사 메타데이터 | 충돌 없는 키 생성 시 중복 확인만 함; 미변경 |
| `T_CODE_OCR` | 8 / 6811 | 유니크 `OCR_CD`; `FORM_CD`, `APPLF_SEQ`, `SDOC_CD`, `DOC_TYP_CD`, `DEL_YN` | 선택한 OCR 코드의 활성 행에서 paper 분류 메타데이터 조회 |
| `T_SCAN_PAPER_TEST` | 15 / 1 | 별도 테스트 테이블 | 사용하지 않음 |

실제 DB에서 `T_SCAN_SUBINDEX → T_SCAN_INDEX`, `T_SCAN_PAPER → T_SCAN_SUBINDEX`, `T_SCAN_PAPER ↔ T_SCAN_RESOURCES` 연결의 고아 행은 각각 0건이었다. Paper와 resource는 각각 411행이며, 두 테이블의 `TMP_ELEMENT_ID`는 유니크 인덱스로 보호된다. 이 때문에 paper ID를 바꾸면 같은 트랜잭션에서 resource의 ID도 바꿔야 한다. 임시행은 후속 이관용 별도 PK 공간이므로 건드리지 않는다. 저장 대상 코드 `1001`, `1101/1102/1201`, `1301/1302/1303`, `1401`, `2001/2002`, `3001`은 모두 활성 `T_CODE_OCR` 행에 있으며, 코드별 `FORM_CD`/`APPLF_SEQ`/`SDOC_CD`/`DOC_TYP_CD`를 읽어 반영한다. `test.db`는 개인정보를 포함할 수 있어 행 값은 출력하거나 보고서에 복사하지 않았다.
- 현재 구현은 원본의 고정값과 R/D/S별 `ID_VALUE` 문자열, 60–89 난수 `ID_COLOR_SCORE`도 재현한다. 이 필드는 기존 DB 호환 동작을 옮긴 것이며 실제 OCR 프로그램에서 센서 측정·진위 검증을 수행했다는 뜻은 아니다.

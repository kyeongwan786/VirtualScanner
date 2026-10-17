# `skt.exe` T_SCAN_PAPER INSERT 필드 감사

## 적용 범위

이 표는 `idscanner/skt(5).exe`의 pkg snapshot에서 회수한 `migrate()` SQL 및 값 배열을 기준으로 한다. 이 경로는 **구형 스캐너 실행 후 `T_SCAN_TEMP` 행을 최종 테이블로 옮기는 흐름**이다. NEW_WORK JPG 입력에서는 이 SELECT/INSERT/DELETE 경로를 호출하지 않는다. 아래의 고정값이나 센서·검증 필드도 새 JPG 처리에서 생성하지 않는다.

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
| 14 | `REG_CHK_YN` | `'Y'` / 상수 | 검증 완료를 뜻할 수 있어 JPG 흐름에서 재사용 금지 |
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
| 29 | `ID_IR_VALUE` | `row['ID_VALUE']` / 장치 관련 | IR 센서 결과로 기록됨. JPG OCR로 대체 금지 |
| 30 | `ID_UV_VALUE` | `row['ID_VALUE']` / 장치 관련 | UV 센서 결과로 기록됨. JPG OCR로 대체 금지 |
| 31 | `ID_SCANR_USE_YN` | `'Y'` / 상수 | 물리 스캐너 사용처럼 표시. JPG에서 금지 |
| 32 | `SCANR_DRV_NM` | `'VOIM ID Card Scanner Source'` / 상수 | 원본 장치 드라이버 표시. JPG에서 재사용 금지 |
| 33 | `ID_OCR_UPD_YN` | `row['ID_OCR_UPD_YN']` / 임시행 전달 | OCR 수정 상태 |
| 34 | `OP_TYP` | `'02'` / 상수 | 원본 업무 코드 |
| 35 | `ISUE_PLC` | `row['ISUE_PLC_CHANGED']` / 검토값 | 사람이 수정한 발급기관 |
| 36 | `DRV_LICEN_ISUE_NUM` | `row['DRV_LICEN_ISUE_NUM_CHANGED']` / 검토값 | 사람이 수정한 면허번호 |
| 37 | `ADDR` | `row['ADDR_CHANGED']` / 검토값 | 사람이 수정한 주소 |
| 38 | `IP_ADDR` | `await ipAddress()` / 실행환경 | 현재 호스트 주소 |
| 39 | `MAC_ADDR` | `await macAddress()` / 실행환경 | 현재 호스트 MAC 주소 |
| 40 | `ID_TR_VALUE` | `row['ID_VALUE']` / 장치 관련 | TR 센서 결과로 기록됨. JPG OCR로 대체 금지 |
| 41 | `FORM_ALIGN_YN` | `'Y'` / 상수 | 정렬 성공 표시. JPG에서 검증 사실로 사용 금지 |
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
| 56 | `ID_COLOR_SCORE` | `Math.floor(Math.random() * 30 + 60)` / 난수 | 60–89 점수를 임의 생성. JPG 처리에서 절대 생성 금지 |
| 57 | `ETC_PAPER_YN` | `'N'` / 상수 | 기타 문서 표시 |
| 58 | `ID_SCANR_SER_NUM` | `ID_SCANR_SER_NUM` / 환경값 | 접점 설정의 장치 시리얼 |
| 59 | `SUB_INDEX_KEY` | `subGroupId` / 생성값 | 하위 인덱스와 paper 관계 키 |

## 결론 및 NEW_WORK 적용

- 전체 `T_SCAN_PAPER`는 80컬럼이지만 이 INSERT는 59컬럼만 지정한다. 나머지 21컬럼을 임의 초기화하거나 채우지 않는다.
- 인덱스/하위 인덱스/리소스/paper 생성은 기존 스캐너 파이프라인에 종속된다. 이번 SK 직접 보고서 흐름과 KT 기존 최신행 수정 흐름에는 이 INSERT를 추가하지 않는다.
- JPG OCR 결과는 사람 검토를 거친 SK 보고서 값과 KT의 허용된 OCR 필드 입력에만 사용한다. 센서·장치·검증 컬럼은 미생성 상태로 둔다.
- 원본 코드가 검증 플래그를 상수 `'Y'`로 기록한다는 사실은 실제 인증이 이뤄졌다는 증거가 아니다.

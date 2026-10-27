# NEW_WORK 구현 현황

| 기능 | 원본 동작 확인 | 이식 완료 | 테스트 통과 | 현재 상태 / 남은 작업 |
|---|---|---|---|---|
| 첨부 원본 9종 구조 확인 | 대부분 정적 확인 | 해당 없음 | 해당 없음 | EXE 형식, 회수한 Python 3.13 함수 역어셈블, SQLite 구조/차이, 보고서·KT 조각 확인. Windows 실행 비교는 미확인 |
| 신규 JPG의 SK 경로 | 기존 SK GUI의 보고서 입력 흐름 및 13필드 규칙 확인 | `T_SCAN_TEMP`/paper SELECT 없이 OCR 검토 객체에서 report 직접 생성 | DB 연결을 금지한 실행 테스트, 합성 report byte 비교 통과 | `skt.exe` migrate는 기존 스캐너 산출물용 별도 흐름이며 신규 JPG 경로에서 호출하지 않음 |
| `skt.exe` 스캐너 이관 | 호출·59컬럼 INSERT·연결키 확인 | 신규 JPG 처리에는 적용하지 않음 | 정적 소스 감사 | 컬럼 출처/장치 필드는 `DATABASE_FIELD_AUDIT.md`; 물리 검증값을 JPG에 만들지 않음 |
| SK 검토값 → report 데이터 객체 | 원본 텍스트 매핑/유형 코드 확인 | DB 없이 검토 객체에서 직렬화기 직접 호출 | exact-byte synthetic fixture 통과 | OCR 줄에서 업무 필드 자동 매핑은 미구현이며 수기 검토값 사용 |
| DB 스키마 및 원본 파일 차이 | 실제 `uscan.skt`/`myCustomer.db` 읽기 전용 대조 | 개인정보를 출력하지 않는 metadata audit 도구 | 18 테이블/스키마/행수/관계 테스트 통과 | 값 차이는 분석 보고서의 식별 키 기준으로 제한 기록 |
| JPG 선택 및 미리보기 | NEW_WORK 기능 | 예 | 수동 Windows 확인 전 | Tkinter/Pillow 작성 |
| 신분증 JPG와 서류 PDF 동시 준비 | 사용자 요청 | 신분증 OCR과 PDF 준비 UI를 별도 패널로 분리; JPG 한 장과 여러 PDF를 각각 선택/드롭; 신분증 원본을 사용자 데이터의 `NEW_WORK/jobs/<job-id>/identity/`에 보존; OCR 및 PDF별 JPG 변환을 백그라운드에서 동시에 시작; PDF 페이지는 같은 작업의 `documents/<pdf>/`에 저장 | 미실행 | PDF별 상태/페이지 수/출력 위치 표시. PDF 서류 종류명 편집 기능은 아직 없음 |
| 접수 PC 선택 및 원격 저장 | 사용자 요청 | 기존 `KyeongwanReceiver` HTTPS 릴레이 API를 확장해 검토값과 문서별 페이지 맵을 암호화 메타데이터로 전달; 중앙 UI에서 Relay 자리 ID 선택; 모든 JPG 페이지를 순서대로 올린 뒤 Receiver의 로컬 적용 완료 상태를 조회 | 미실행 | `render.yaml`/서버 구현은 있으나 Render 실제 배포 상태와 URL은 대시보드에서 확인 필요. Render 재배포, 토큰/자리 설정 및 Windows 현장 확인 필요 |
| 접수 PC Receiver UI 및 스캔 대기열 | 사용자 요청 | Render Relay를 outbound HTTPS로 폴링; SK/KT 로컬 적용 후 ack; 신분증 미리보기, PDF 대기열 순서 변경, 맨 위의 연속 문서 묶음만 TWAIN `ready`에 공개 | 미실행 | Receiver가 로컬 적용을 마치면 Relay 이미지/민감 메타데이터를 지우고 상태 레코드만 유지. 완료한 TWAIN 작업이 `ready`에서 사라지면 다음 항목을 대기 상태로 전환. Windows 현장 확인 필요 |
| 한국어 PaddleOCR | 새 엔진 선택·연결 | 어댑터/UI 연결 | 가짜 엔진 어댑터 테스트 | PP-OCRv5 모바일 기본값. Paddle 공개 벤치마크 참고만 있으며 실제 신분증 성능 검증은 미완료 |
| OCR 문자 결과/신뢰도 표시 | 새 엔진 출력 형식에 맞춤 | 예 | 어댑터 결과 파싱 테스트 | 줄 단위 원문 표시, 선택 줄을 검토 필드에 삽입 가능 |
| ID_TYPES 문서 종류 추정 및 `sk_report.txt` | 사용자 제공 코드/라벨 목록 사용 | OCR 키워드 분류, GUI 유형 선택, 지정한 key=value 형식으로 Windows `C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt`에 UTF-8 기록(기존 파일 덮어쓰기); macOS 소스 실행은 프로젝트 폴더에 기록; 주민등록번호 OCR 상자 또는 외국인등록번호 확대 OCR 상자에서 뒷자리 첫 숫자를 제외한 6자리 `mskPos` 계산·인코딩 | 문서군별 숫자 코드/문자형 ID_TYPE 매핑, 복수 하위 코드 선택, 면허번호 단일·분할 OCR 및 결과 필드 합성 테스트 통과 | 6자리 폭은 OCR 줄 상자에서 문자 폭 비율로 추정하므로 실물 이미지 좌표 대조 필요; 개인정보 필드는 검토 입력이며 OCR 자동 추출 아님; 면허증 기본 선택은 원본 일반 선택값 1102이며 1101/1201은 유형 선택에서 지정; 실제 이미지 OCR 좌표와 하위 코드 구분 정확도는 추가 대조 필요 |
| `sk_report.txt` 원본 JPG 및 얼굴 사진 crop | 사용자 요청 | `base64DATA`에 JPG 원본, `idCrop`에 Haar 얼굴 검출 주변 사진 영역을 각각 Base64 후 UTF-8 URL 인코딩해 기록 | 합성 이미지에 대한 크롭/JPEG 인코딩 테스트 통과 | 실제 신분증 여러 종류에서 얼굴 검출·사진 프레임 포함 여부 확인 필요; 미검출/모호하면 `idCrop` 빈 값 |
| OCR 업무 필드 자동 분류 | OCR 텍스트 패턴 기반 부분 구현 | 주민등록번호·영문 이름·발급일·발급처·주소·면허번호를 검토 입력칸에 미리 입력; 외국인등록증 감지 즉시 전체 카드 영문 OCR 줄을 한국어 OCR 결과와 병합 | 합성 OCR 테스트 통과; 제공 JPG 두 장에서 등록번호와 마스킹 좌표 검출 확인; 외국인등록증 판별 후 카드 전체를 영문 OCR로 추가 인식하는 경로 추가 | 일반 OCR에서 제공 JPG 두 장에서 등록번호와 마스킹 좌표를 검출했으나 두 번째 카드의 한국어 OCR은 영문 성명이 불완전했음. PP-OCRv5 영문 모델은 실행 환경의 모델 호스트 접근 제한으로 다운로드·검증하지 못해 실제 Windows에서 첫 로드 및 이름 결과 확인 필요; 미인식 시 검토 입력에서 확인 |
| OCR 원본과 사람 검토값 분리 | NEW_WORK 기능 | 메모리 UI 완료 | 도메인 테스트 통과 | 영속 저장/작업 이력은 미구현 |
| SK 보고서 | 필드 순서/인코딩/조건부 crop 원본 확인 | 직렬화, 자동 원본 좌표 크롭, 유형 코드 입력, 버튼 생성·전달 연결 | 합성 데이터 테스트 통과; 제공 샘플 직렬화 바이트 일치 | Windows 원본과 기능 비교 필요 |
| KT OCR 결과 DB 저장 | `com_kt` 최신행 수정 흐름, OCR 코드표 및 `NEW_WORK/test.db` 관련 테이블 스키마/키 연결 확인 | 기존 최신 `T_SCAN_PAPER` OCR 필드와 `T_CODE_OCR` 분류 메타데이터를 UPDATE. 주소는 `ADDR`와 `ADDR_ORIGIN`에 반영하고, 국가유공자증(`1302`)이면 검토 보훈번호를 `WLF_NUM`과 `WLF_NUM_ORIGIN`에 기록. `unique()`로 만든 새 `TMP_ELEMENT_ID`와 연결된 `T_SCAN_RESOURCES.TMP_ELEMENT_ID`를 한 트랜잭션에서 갱신. TEMP/INDEX/SUBINDEX/PAPER/RESOURCE 신규 INSERT 없음. Windows 경로는 `C:\IMAGE\myCustomer.db` | 미실행 | Windows 실제 DB에서 저장 성공 및 이미지 키 연결 확인 필요 |
| KT DB / ORG.jpg / CR.jpg 저장 | 사용자가 정한 `C:\IMAGE` 파일명·덮어쓰기 경로와 원본/얼굴 crop 요구 | 저장 버튼에서 선택한 JPG를 `ORG.jpg`, 얼굴 사진 영역을 종류별 크기(`1001`: 340×406, 그 외: 320×420)로 `CR.jpg`에 덮어씀. 얼굴 검출 실패/모호 시 DB 저장 중단. DB 키/분류 갱신과 같은 KT 경로에서 호출 | `테스트.jpg`, `ㅋㅋ.jpg`, `마스킹안되는거.jpg`, `박말분.jpg` crop을 눈으로 확인; 두 출력 크기 확인 | Windows 얼굴 검출, 덮어쓰기 권한 및 저장 결과 확인 필요 |
| 접점 설정·결과 전달 | 원본 8개 접점 항목 및 SK `classes`, KT `IMAGE` 상대경로 확인 | 중앙 UI에서 Relay 자리 ID 선택; 검토값/문서 맵과 JPG 페이지를 기존 암호화 Relay API로 전송 | 미실행 | 기존 로컬 파일 전달 헬퍼는 별도 코드로 남음. NEW_WORK의 SMB 전송에는 사용하지 않음 |
| SK / KT 처리 버튼 | 원본 생성/복사 흐름 확인 | 검토 완료 상태에 연결 | 유닛 워크플로 테스트 통과 | 업무페이지가 산출물을 수용하는 Windows 현장 테스트 필요 |
| LG 처리 | 범위 제외 | 해당 없음 | 해당 없음 | 사용자 지시로 현재 개발 범위에서 제외 |
| 기존 접점별 공유폴더 전달 헬퍼 | 이전 로컬 파일 전달 흐름 | `transfer.py`에 임시복사/해시 검증/충돌 처리 | 기존 합성 파일시스템 테스트 결과 유지 | 현재 중앙 저장 버튼의 Relay 경로에서는 호출하지 않음 |
| Windows onedir package | 빌드 설정만 작성 | 아니오 | 아니오 | BAT 실행, Paddle 네이티브 라이브러리 수집 및 Windows 실행 검증 필요 |

## 남은 자료·환경

- 현 개발 호스트는 macOS다. Windows x64 Python 3.12 환경에서 PaddleOCR 설치, 한국어 JPG 인식 및 EXE 빌드를 확인해야 한다.
- 제공된 원본 PyInstaller GUI는 Python 3.13이다. 메인 함수는 정적 역어셈블했지만 바이너리 실행 비교는 Windows에서 해야 한다.
- 실제 PaddleOCR 모델 캐시를 단일 EXE에 포함할지, 첫 실행 다운로드를 허용할지 배포 조건을 결정하고 검증해야 한다.
- LG는 현재 요청에서 제외되었으므로 추가 자료 요청 항목에 포함하지 않는다.
# 중앙 OCR V1/V2 분리

- V1은 예전 로컬 저장 흐름이다. 실행한 접수 PC에서 SK 보고서를 직접 갱신하거나 KT DB 및 ORG/CR 이미지를 갱신한다. 대상 PC 선택과 PDF Relay 전송 UI는 없다.
- V2는 중앙 PC에서 선택한 접수 PC의 V2 Receiver로 OCR 작업과 PDF를 Relay 전송한다.
- V1은 OCR 전용이며 별도 Receiver가 필요 없다. V2 중앙 앱에는 선택 PC 전송용 V2 Receiver가 필요하다.
- 소스 빌드 ZIP은 `NEW_WORK-V1-Windows-Build.zip`, `NEW_WORK-V2-Windows-Build.zip`, `NEW_WORK-Receiver-V2-Windows-Build.zip`으로 제공한다.
- V2 작업 ZIP을 만들 때 신분증 JPG의 JPEG 종료 표시 뒤에 있는 0 패딩만 제거한다. 인코딩된 이미지와 화질은 다시 만들지 않으며, Receiver의 JPG 끝 검사와 호환되게 한다.
- Windows EXE 빌드와 실제 대상 PC의 파일/DB 반영은 아직 검증되지 않았다.

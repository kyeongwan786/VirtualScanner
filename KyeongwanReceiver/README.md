# Kyeongwan Scanner 송신기와 Receiver

`phone-dcim-transfer`의 PDF 드래그앤드롭, 페이지별 JPG 변환, Receiver로 전송하는 흐름을 참고해 만든 별도 구성이다. 휴대폰 DCIM 대신 **각 접수 PC의 가상 스캐너 대기 폴더**에 이미지를 보낸다.

## 목표 흐름

1. 중앙 PC에서 `central_gui.py`를 실행하고 접수 자리를 선택한다.
2. 텔레그램에서 받은 PDF를 창에 드래그앤드롭한다.
3. 송신기가 PDF 페이지를 JPG로 변환해 해당 자리의 Receiver로 전송한다. PDF 원본은 수정하거나 삭제하지 않는다.
4. Receiver는 가상 Source 공용 저장소 `%ProgramData%\KyeongwanScanner\VirtualSource\ready\<job-id>`에 작업별 JPG와 manifest를 저장한다.
5. 해당 자리의 TWAIN Source가 스캔 요청을 받으면 대기 폴더의 JPG를 업무페이지에 반환한다.

`KyeongwanScanner`는 `%ProgramData%\\KyeongwanScanner\\VirtualSource\\ready`에서 완성된 작업을 찾아 페이지 순서대로 TWAIN Native Transfer 이미지로 반환하도록 작성되어 있다. 이는 업무페이지가 파일 경로를 직접 읽는 동작이 아니라, TWAIN Source가 업무페이지의 Acquire 요청에 이미지 데이터를 응답하는 구조다. 실제 업무페이지에서 선택·스캔되는지는 Windows 대상 PC에서 아직 검증되지 않았다.

## 접수 PC Receiver 설정

각 접수 PC마다 `receiver.example.json`을 `receiver.json`으로 복사하고 `station_id`, `token`을 설정한다. 중앙 송신기 자리 목록의 URL, 자리 ID, token은 해당 Receiver 설정과 일치해야 한다.

필요 패키지 설치 후 실행한다.

```powershell
python -m pip install -r requirements.txt
python .\receiver.py --config .\receiver.json
```

Receiver는 기본 포트 `29742`에서 동작한다. 중앙 PC에서 접속할 수 있도록 Windows 방화벽에서 사내 Private 네트워크에 해당 포트를 허용해야 한다. 운영 배포용 EXE와 자동 시작 등록은 아직 없다.

## 중앙 PC 설정 및 사용

1. Python 3.10 이상을 준비하고 `requirements.txt`를 설치한다.
2. 첫 실행으로 생성된 `%APPDATA%\KyeongwanScanner\stations.json`을 편집한다. 각 항목에 사람이 알아볼 `label`, `station_id`, 접수 PC Receiver의 `url`, 그 Receiver와 같은 `token`을 넣는다. 형식 예시는 `stations.example.json`에 있다.
3. `central_gui.py`를 실행하고 목적 접수 자리를 선택한다.
4. PDF 파일을 드롭한다. 필요하면 폴더를 드롭해 안의 PDF/JPG도 보낼 수 있다.

```powershell
python -m pip install -r requirements.txt
python .\central_gui.py
```

PDF는 MuPDF로 페이지별 JPEG를 메모리에서 렌더링한다. 페이지가 단색으로 렌더링되면 PDFium으로 다시 렌더링해 빈 결과를 걸러낸다. 현재 기본값은 175 DPI, JPG 품질 85이며 창에서 조정할 수 있다.

## 네트워크 계약과 저장 형식

- `GET /health` — 준비 상태와 Receiver `station_id` 확인
- `PUT /v1/jobs/{jobId}/pages/{pageNumber}` — 인증 헤더 `X-Receiver-Token`과 JPG 본문으로 페이지 업로드
- `POST /v1/jobs/{jobId}/complete` — 페이지 수와 원본 파일명을 보내 작업 완료 선언
- `DELETE /v1/jobs/{jobId}/cancel` — 전송 중 작업 취소. 인증 헤더가 필요하며, 이미 `ready`에 공개된 작업은 취소할 수 없음

Windows 기본 데이터 폴더는 `%ProgramData%\\KyeongwanScanner\\VirtualSource`이며 `incoming`, `ready`, `completed`, `cancelled` 하위 폴더를 사용한다. 작업 완료 전에는 `incoming`에 보관한다. 모든 페이지가 연속해서 도착하고 완료 선언이 확인되면 작업 디렉터리를 `ready/<job-id>`로 이동한다. `manifest.json`에는 자리 ID, 원본 파일명, 페이지 순서, DPI, 페이지 SHA-256이 기록된다. 페이지는 `001.jpg`, `002.jpg`처럼 저장한다. 최대 100페이지, 페이지당 40 MiB다. 동일한 페이지의 재전송은 중복 처리한다.

중앙 GUI에서 전송 취소를 누르면 Receiver가 해당 작업을 `cancelled/<job-id>`로 옮겨 보존하고, `cancellation.json`을 기록한다. 이 폴더의 내용은 TWAIN Source가 읽지 않는다. 페이지가 아직 도착하지 않은 작업도 취소 ID를 기록해 이후 늦게 도착한 페이지를 거부한다. 이미 `ready`가 된 작업은 스캔될 수 있으므로 이 API로 취소할 수 없다. 취소한 파일은 자동 삭제하지 않는다.

TWAIN Source가 작업의 모든 페이지를 업무페이지에 성공적으로 반환하면 작업 폴더 전체를 `completed/<job-id>`로 이동해 보존한다. 스캔을 취소하거나 전송을 끝내지 못하면 작업은 `ready`에 남아 재시도할 수 있다. 자동 삭제는 하지 않는다.

## 보안 및 현재 한계

현재 HTTP는 평문이다. 인증 토큰과 문서 이미지가 네트워크 암호화 없이 전달되므로 사내의 신뢰된 네트워크에서만 개발 확인용으로 사용한다. `stations.json`에도 토큰이 저장되므로 Windows 사용자 계정 접근을 제한해야 한다. 배포 전에는 TLS 또는 동등한 보호를 추가한다.

TWAIN Source의 Acquire/이미지 반환 코드와 Receiver 대기열 연결은 작성되어 있다. 중앙 GUI에는 진행 중인 전송을 취소하는 기능이 있으며, 이미 도착한 부분 데이터는 삭제하지 않고 Receiver의 `cancelled` 폴더에 보존한다. 다만 Windows 빌드, Source 설치 후 목록 표시, 업무페이지에서 선택 및 실제 이미지 스캔은 아직 검증되지 않았다. 중앙 GUI의 Windows EXE 배포와 Receiver 자동 시작도 남아 있다. 코드가 준비된 것과 접수 PC에서 동작이 확인된 것은 구분한다.

# Kyeongwan Scanner 송신기와 Receiver

`phone-dcim-transfer`의 PDF 드래그앤드롭, 페이지별 JPG 변환, Receiver로 전송하는 흐름을 참고해 만든 별도 구성이다. 휴대폰 DCIM 대신 **각 접수 PC의 가상 스캐너 대기 폴더**에 이미지를 보낸다.

## 목표 흐름

1. 중앙 PC에서 `central_gui.py`를 실행하고 접수 자리를 선택한다.
2. 텔레그램에서 받은 PDF를 창에 드래그앤드롭한다.
3. 송신기가 PDF 페이지를 JPG로 변환해 해당 자리의 Receiver로 전송한다. PDF 원본은 수정하거나 삭제하지 않는다.
4. Receiver는 가상 Source 공용 저장소 `%ProgramData%\KyeongwanScanner\VirtualSource\ready\<job-id>`에 작업별 JPG와 manifest를 저장한다.
5. 해당 자리의 TWAIN Source가 스캔 요청을 받으면 대기 폴더의 JPG를 업무페이지에 반환한다.

`KyeongwanScanner`는 `%ProgramData%\\KyeongwanScanner\\VirtualSource\\ready`에서 완성된 작업을 찾아 페이지 순서대로 TWAIN Native Transfer 이미지로 반환하도록 작성되어 있다. 이는 업무페이지가 파일 경로를 직접 읽는 동작이 아니라, TWAIN Source가 업무페이지의 Acquire 요청에 이미지 데이터를 응답하는 구조다. 실제 업무페이지에서 선택·스캔되는지는 Windows 대상 PC에서 아직 검증되지 않았다.

## 보안 릴레이 구성 (새 흐름)

접수 PC가 중앙 서버로 **나가는 HTTPS 연결만** 사용한다. 중앙 송신기는 릴레이에 작업을 올리고, 각 VM의 `relay_receiver.py`가 자기 자리 작업을 가져온다. 접수 PC에 인바운드 포트를 열지 않는다. 기존 직접 접속 모드는 개발 호환용이며, HTTP라 민감 문서 운영에 사용하지 않는다.

### Mac을 임시 시험 서버로 사용

Mac과 Windows VM 사이에 이미 라우팅 가능한 사내/LAN 경로가 있을 때만 이 시험 구성이 연결된다. Mac은 Wi-Fi, Windows PC는 Ethernet이어도 같은 LAN일 수 있으므로 연결 종류만으로 판단하지 않는다. VMware NAT/브리지 모드는 바꾸지 말고, 라우팅이 없으면 공개 인터넷 포트나 라우터 전달을 추가하지 않는다. Mac의 LAN 주소가 바뀌지 않도록 DHCP 예약을 권장한다.

Mac에서 저장소의 `KyeongwanReceiver` 폴더로 이동해 시험용 CA와 서버 인증서를 만든다. `<MAC_LAN_IP>`에는 Mac의 사내 Wi-Fi/Ethernet IPv4 주소를 넣는다.

```zsh
cd KyeongwanReceiver
python3 -m pip install -r requirements.txt
python3 make_test_tls.py --name <MAC_LAN_IP>
python3 relay_server.py --init-config --config relay.json --station-id desk-01
```

`relay.json`에서 `host`를 `<MAC_LAN_IP>`, `tls_cert`를 `relay-server.crt`, `tls_key`를 `relay-server.key`로 지정한다. `central_allowed_ips`는 Mac 주소의 `/32`만 넣고, 각 자리의 `allowed_ips`에는 Relay 접속 로그에서 확인한 해당 VM의 출구 IP 하나만 `/32`로 넣는다. 이 값이 확인되기 전에는 넓은 네트워크 범위를 허용하지 않는다. Mac에서 앱의 네트워크 수신을 허용하라는 방화벽 창이 나오면 사내 테스트에 한해 허용한다. 공유기 포트 전달은 하지 않는다.

서버가 실행 중일 때 Windows VM 안의 PowerShell에서 TCP 경로를 확인한다.

```powershell
Test-NetConnection 192.168.0.12 -Port 8443
```

`TcpTestSucceeded : True`면 Mac의 해당 포트까지 네트워크 연결은 된다. 애플리케이션에서 `403`이 나오는 것과 TCP 연결 실패는 다른 상태다. `False`면 VM NAT/VPN/네트워크 경로 또는 Mac 방화벽을 확인한다. 이때 VM을 브리지로 바꾸거나 공유기에서 공인 포트를 열지 않는다.

Mac 송신 설정과 VM `receiver.json`에는 `ca_cert`로 `relay-test-ca.crt`를 지정한다. 그 공개 인증서만 VM으로 옮긴다. `relay-test-ca.key`는 Mac 밖으로 복사하지 않는다. 이 CA는 시험 전용이다. 업무 문서를 넣기 전에 임의의 시험 JPG로 전송과 TWAIN 대기열을 확인한다. 이 구성은 TLS 검증을 유지하지만 자체 발급 시험 인증서이므로 운영 인증서 대신 사용할 수 없다.

### 1. 승인된 중앙 서버 준비

서버는 항상 켜져 있고 중앙 송신 PC 및 접수 VM 모두에서 접근 가능한 회사 관리 서버여야 한다. 중앙 PC에 임시로 실행하는 것은 가능하지만, PC 절전/종료 시 전달이 멈춘다. 공개 서버 주소와 TLS 인증서는 회사 IT가 승인한 환경에서 준비해야 한다. 공용 IP에 개발용 서버를 직접 노출하지 않는다.

서버에 Python 3.10 이상과 `requirements.txt`를 준비한 뒤 최초 1회 실행한다.

```powershell
python -m pip install -r requirements.txt
python .\relay_server.py --init-config --config .\relay.json --station-id desk-01
```

`relay.json`, `relay-data.key`, `relay-provisioning.json`이 만들어진다. 마지막 파일에 중앙 송신기 토큰과 자리별 토큰이 평문으로 한 번 제공되므로 저장소에 올리거나 메신저/이메일로 보내지 말고, 회사 승인 비밀 전달 수단으로 각 PC에 한 번 전달한다. 서버 키 파일과 작업 데이터 폴더는 서버 관리자만 읽도록 제한한다. 별도 자리는 아래처럼 추가한다.

```powershell
python .\relay_server.py --config .\relay.json --add-station desk-02
```

서버 설정 기본값은 `127.0.0.1`에만 바인딩한다. 외부 서버에서 사용하기 전 `relay.json`에 TLS 인증서/개인키 경로, 중앙 송신 PC의 고정 출구 IP/CIDR, **각 자리별** 고정 출구 IP/CIDR을 명시해야 한다. 외부 주소로 바인딩하면서 TLS 파일이나 허용 IP 목록이 비어 있으면 서버가 시작을 거부한다. 설정 예시는 `relay.example.json`을 참고한다. 동적 IP만 있고 고정 출구 주소를 확보할 수 없다면 IP 허용 목록을 임의로 넓히지 말고 회사 IT가 제공하는 고정 출구 네트워크/VPN을 마련해야 한다. IP allow-list는 서버가 직접 관찰한 연결 상대 주소 기준이며 프록시 뒤 실제 접속 IP 전달은 설정돼 있지 않다.

문서 페이지는 릴레이 디스크에 Fernet으로 암호화해 저장한다. TLS는 전송 중 암호화에 사용한다. `relay-data.key`가 페이지 암호화를 푸는 키이므로 서버 데이터와 같은 백업/공유 위치에 무심코 복사하지 않는다. 더 강한 키 분리를 위해 운영 시 환경 변수 `KYEONGWAN_RELAY_DATA_KEY`에 별도 보관소에서 읽은 Fernet 키를 주입할 수 있다. 현재 구현은 클라이언트와 릴레이 사이의 TLS + 릴레이 저장 암호화이며 **종단간 암호화는 아니다**. 운영 호스트와 OS 계정은 회사가 관리하고, Windows라면 BitLocker 같은 전체 디스크 암호화도 켜야 한다.

### Render 무료 시험 배포

저장소 루트의 `render.yaml`은 무료 Render Web Service 시험 구성을 정의한다. Render 앞단이 공개 HTTPS를 종료하고 앱을 `PORT` 환경 변수의 `0.0.0.0` 포트로 연결한다. Render 실행 모드는 중앙 토큰과 자리별 토큰을 검증하며, VPN별 출구 주소가 달라질 수 있어 IP allow-list를 사용하지 않는다. Web Service는 인터넷에 공개되므로 이 설정은 임의 시험 문서 전용이다.

1. Render에 연결할 Git 브랜치에 PDF/JPG, 개인 키, 토큰이 포함되지 않았는지 먼저 확인한다.
2. 로컬에서 토큰을 만든다. 기존 `relay-provisioning.json`이 있으면 거기 발급된 값을 사용한다.

   ```zsh
   python3 KyeongwanReceiver/relay_server.py --init-config \\
     --config KyeongwanReceiver/relay.json --station-id desk-01
   ```

3. Render Dashboard에서 **New → Blueprint**로 저장소의 `render.yaml`을 선택한다. Blueprint가 요청하면 다음 비밀 값을 입력한다.
   - `KYEONGWAN_RELAY_CENTRAL_TOKEN`: `relay-provisioning.json`의 `central_token`
   - `KYEONGWAN_RELAY_STATIONS`: 자리 토큰 JSON 배열. 예: `[ {"station_id":"desk-01","token":"자리별_긴_랜덤_토큰"} ]`
4. `KYEONGWAN_RELAY_DATA_KEY`는 Render가 생성한다. 배포 후 Render가 부여한 `https://<서비스이름>.onrender.com` 주소를 중앙 자리 목록과 VM 설정에 넣는다. 공개 인증서이므로 `ca_cert`는 비워 둔다. 예시는 `stations.render.example.json`, `receiver.render.example.json`에 있다.

무료 서비스는 15분간 요청이 없으면 잠들고 로컬 파일은 재시작 후 보존되지 않는다. Receiver가 10초마다 폴링하면 보통 계속 요청이 들어오지만, VM이 작업을 가져가기 전에 Render 서비스가 재시작하면 대기 작업이 사라질 수 있다. 중앙 원본을 보존하고 재전송할 수 있는 시험에만 사용한다. Render는 HTTPS를 앞단에서 종료하고 앱에는 내부 HTTP로 전달하므로, 릴레이 저장 암호화는 유지되지만 종단간 암호화는 아니다. 서비스와 플랫폼은 처리 중인 문서에 접근할 수 있다.

### 2. 각 Windows VM에서 Receiver 실행

VM에 Python을 설치하지 않으려면 이 폴더를 Windows 호스트에서 PyInstaller로 빌드해 옮길 수 있다. 단, 실행 대상과 같은 Windows 아키텍처에서 빌드해야 한다. `receiver.relay.example.json`을 복사해 `receiver.json`으로 만들고, 해당 VM의 `station_id`, `relay_url`, 그 자리 전용 `token`을 넣는다. 설치 후 다음을 실행한다.

```powershell
python -m pip install -r requirements.txt
python .\relay_receiver.py --config .\receiver.json
```

이 프로그램은 10초마다 서버에 나가서 자기 자리 작업을 확인한다. 로컬 수신 포트나 VMware NAT 포트 전달은 필요하지 않다. 완료한 작업은 기존 `%ProgramData%\KyeongwanScanner\VirtualSource\ready`에 저장한다. Receiver 자동 시작/EXE 패키징은 아직 별도 작업이다.

### 3. 중앙 송신 GUI 설정

`%APPDATA%\KyeongwanScanner\stations.json` (macOS는 `~/Library/Application Support/KyeongwanScanner/stations.json`)을 다음 구조로 설정한다. `central_token`은 릴레이 송신용 토큰이며 자리 Receiver 토큰과 다르다.

```json
{
  "relay_url": "https://relay.example.com:8443",
  "central_token": "relay-provisioning.json에서 발급된 중앙 토큰",
  "stations": [
    {"label": "접수 자리 1", "station_id": "desk-01"},
    {"label": "접수 자리 2", "station_id": "desk-02"}
  ]
}
```

예시는 `stations.relay.example.json`에 있다. 송신기와 Receiver 모두 릴레이 인증서를 신뢰해야 한다. 저장소 폴더에서 `python KyeongwanReceiver/central_gui.py`를 실행하고 PDF/JPG를 선택한다. 실제 문서를 보내기 전에 회사가 승인한 시험 문서로 먼저 확인한다.

```powershell
python -m pip install -r requirements.txt
python .\central_gui.py
```

### macOS에서 중앙 송신기로 실행

macOS에서도 중앙 송신 GUI를 실행해 Windows VM의 Receiver로 PDF/JPG를 보낼 수 있습니다. 가상 TWAIN 스캐너와 Receiver는 Windows VM에 그대로 둡니다. Mac의 Python은 3.10 이상이어야 하며 Tkinter가 포함되어야 합니다. Homebrew Python을 쓴다면 해당 Python 버전에 맞는 `python-tk` formula도 설치합니다. 예를 들어 Homebrew Python 3.12에는 `brew install python-tk@3.12`를 실행합니다. python.org macOS 설치본은 자체 Tcl/Tk를 포함합니다. `tkinterdnd2`를 불러오지 못하거나 네이티브 드롭 기능을 사용할 수 없는 경우에도 GUI는 열리며, **파일 선택…** 버튼으로 파일을 보낼 수 있습니다.

저장소 폴더에서 별도 가상환경을 만들고 실행합니다:

```zsh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r KyeongwanReceiver/requirements.txt
python KyeongwanReceiver/central_gui.py
```

GUI의 **자리 목록 열기** 버튼은 `~/Library/Application Support/KyeongwanScanner/stations.json`을 엽니다. 위 릴레이 형식으로 설정하면 Mac과 VM 간 직접 접속이나 VMware 네트워크 모드 변경 없이 운용할 수 있습니다. Mac과 접수 VM 모두 릴레이 서버의 HTTPS 주소에 접근해야 합니다.

PDF는 MuPDF로 페이지별 JPEG를 메모리에서 렌더링한다. 페이지가 단색으로 렌더링되면 PDFium으로 다시 렌더링해 빈 결과를 걸러낸다. 현재 기본값은 175 DPI, JPG 품질 85이며 창에서 조정할 수 있다.

## 네트워크 계약과 저장 형식

보안 릴레이 API:

- `POST /v1/jobs`, `PUT /v1/jobs/{jobId}/pages/{pageNumber}`, `POST /v1/jobs/{jobId}/complete`, `DELETE /v1/jobs/{jobId}/cancel` — 중앙 토큰과 중앙 출구 IP로 제한
- `GET /v1/stations/{stationId}/jobs/next`, `GET /v1/stations/{stationId}/jobs/{jobId}/pages/{pageNumber}`, `POST /v1/stations/{stationId}/jobs/{jobId}/ack` — 해당 자리 전용 토큰과 그 자리 출구 IP로 제한
- `GET /health` — 비민감 준비 상태만 제공

Relay는 페이지를 암호화해 `relay-data/jobs`에 저장한다. 페이지당 최대 40 MiB, 작업당 최대 512 MiB, 작업당 최대 100페이지다. Receiver가 모든 페이지를 로컬 `ready`에 원자적으로 공개한 뒤 ack를 보내면 Relay는 확인 후 서버 사본을 삭제한다. Render에서는 Receiver가 내려받기 전에 서비스가 재시작되면 대기 작업이 유실될 수 있다.

- 구형 직접 Receiver API (개발 호환 전용): `GET /health`, `PUT /v1/jobs/{jobId}/pages/{pageNumber}`, `POST /complete`, `DELETE /cancel`
- `PUT /v1/jobs/{jobId}/pages/{pageNumber}` — 인증 헤더 `X-Receiver-Token`과 JPG 본문으로 페이지 업로드
- `POST /v1/jobs/{jobId}/complete` — 페이지 수와 원본 파일명을 보내 작업 완료 선언
- `DELETE /v1/jobs/{jobId}/cancel` — 전송 중 작업 취소. 인증 헤더가 필요하며, 이미 `ready`에 공개된 작업은 취소할 수 없음

Windows 기본 데이터 폴더는 `%ProgramData%\\KyeongwanScanner\\VirtualSource`이며 `incoming`, `ready`, `completed`, `cancelled` 하위 폴더를 사용한다. 작업 완료 전에는 `incoming`에 보관한다. 모든 페이지가 연속해서 도착하고 완료 선언이 확인되면 작업 디렉터리를 `ready/<job-id>`로 이동한다. `manifest.json`에는 자리 ID, 원본 파일명, 페이지 순서, DPI, 페이지 SHA-256이 기록된다. 페이지는 `001.jpg`, `002.jpg`처럼 저장한다. 최대 100페이지, 페이지당 40 MiB다. 동일한 페이지의 재전송은 중복 처리한다.

중앙 GUI에서 전송 취소를 누르면 Receiver가 해당 작업을 `cancelled/<job-id>`로 옮겨 보존하고, `cancellation.json`을 기록한다. 이 폴더의 내용은 TWAIN Source가 읽지 않는다. 페이지가 아직 도착하지 않은 작업도 취소 ID를 기록해 이후 늦게 도착한 페이지를 거부한다. 이미 `ready`가 된 작업은 스캔될 수 있으므로 이 API로 취소할 수 없다. 취소한 파일은 자동 삭제하지 않는다.

TWAIN Source가 마지막 페이지 반환을 업무페이지와 성공적으로 완료하고 `MSG_ENDXFER`를 받으면 작업 폴더를 삭제한다. 스캔을 취소하거나 반환을 끝내지 못하면 작업은 `ready`에 남아 재시도할 수 있다. Relay 사본은 그 전에 Receiver가 로컬 `ready`에 안전하게 복사한 뒤 삭제된다.

## 보안 및 현재 한계

일반 릴레이 모드는 HTTPS, 중앙/자리별 별도 bearer token, 출발지 IP/CIDR 허용 목록, 암호화된 페이지 저장을 구현한다. Render 시험 모드는 Render 앞단에서 HTTPS가 종료되므로 앱 내부 TLS와 IP allow-list를 사용하지 않고 긴 무작위 중앙/자리별 bearer token으로 인증한다. 모든 클라이언트 설정에는 해당 토큰이 필요하므로 계정 접근을 제한하고 토큰을 로그/스크린샷/저장소에 올리지 않는다. 두 모드 모두 종단간 암호화가 아니며 릴레이 프로세스는 처리 중 문서를 복호화한다. Render 무료 모드의 임시 저장소는 내구성 있는 큐가 아니므로 실제 접수 서류나 재전송을 보장해야 하는 업무에 사용하지 않는다.

Render Blueprint와 실행 모드는 저장소에 준비했지만 실제 Render 계정에 연결하거나 배포하지 않았고, 공개 서비스 URL도 아직 없다. 무료 Render 배포는 임시 파일시스템을 사용하며 네트워크/IP allow-list 없이 bearer token에 의존한다. Windows 빌드와 Render 공개 배포는 아직 검증되지 않았다. 구형 Receiver는 HTTP 직접 접속을 계속 제공하므로 릴레이로 전환하기 전에는 민감한 실제 문서에 사용하지 않는다.

TWAIN Source의 Acquire/이미지 반환 코드와 Receiver 대기열 연결은 작성되어 있다. 중앙 GUI에는 진행 중인 전송을 취소하는 기능이 있으며, 이미 도착한 부분 데이터는 삭제하지 않고 Receiver의 `cancelled` 폴더에 보존한다. 다만 Windows 빌드, Source 설치 후 목록 표시, 업무페이지에서 선택 및 실제 이미지 스캔은 아직 검증되지 않았다. 중앙 GUI의 Windows EXE 배포와 Receiver 자동 시작도 남아 있다. 코드가 준비된 것과 접수 PC에서 동작이 확인된 것은 구분한다.

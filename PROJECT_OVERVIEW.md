# kyeongwan-scanner 프로젝트 정리

## 만들려는 것

사무실에는 접수 업무를 하는 PC가 여러 대 있고, 각 PC는 접수 코드에 맞춰 업무를 처리한다. 휴대폰은 자리마다 한 대씩 있는 것이 아니라 대략 네 자리당 한 대다. 현재는 업무페이지에서 **문서 스캔**을 누르고 스캐너를 선택하면 VM에 연결된 휴대폰이 스캐너로 보이며, 휴대폰 DCIM에 넣어 둔 서류 이미지를 업무페이지로 가져온다.

목표는 서류 준비를 중앙 PC 한 곳에서 처리하고, 접수 자리에서는 파일을 직접 갖고 있지 않아도 자기 업무페이지의 스캔 기능으로 중앙에서 준비한 이미지를 가져오게 하는 것이다.

## 목표 사용 흐름

1. 텔레그램으로 받은 서류 PDF를 중앙 PC에서 이미지로 변환한다. 현재는 ALPDF로 변환하는 흐름을 사용한다.
2. 중앙 PC가 이미지와 접수 대상 자리 정보를 관리한다.
3. 각 접수 PC에는 수신 프로그램과 가상 스캐너를 둔다.
4. 직원이 해당 PC의 업무페이지에서 **문서 스캔**을 누르고 `kyeongwan-scanner`를 선택한다.
5. 가상 스캐너가 중앙 PC에서 그 자리의 접수 이미지를 받아 업무페이지에 스캔 결과로 전달한다.

여기서 가상 스캐너는 업무페이지와 중앙 이미지 전달 기능 사이의 연결부다. 업무페이지에는 로컬 스캐너 장치처럼 나타나고, 스캔 요청이 오면 중앙에서 준비한 이미지를 반환하는 것이 최종 동작이다.

## 현재 폴더별 역할과 구현 상태

### `phone-dcim-transfer` (옆 프로젝트)

기존 목적은 호스트 PC에서 PDF 페이지를 JPG로 만들고, VM 수신 프로그램을 거쳐 VM에 USB로 연결된 휴대폰의 DCIM으로 복사하는 것이다.

- 예전 방식은 VMware 공유 폴더에 이미지 파일을 놓고 VM 수신기가 휴대폰으로 복사한다.
- 현재 안내된 Fast TCP 방식은 호스트가 PDF 페이지 JPG 데이터를 TCP로 VM 수신기에 보내고, 수신기가 WPD/MTP를 사용해 휴대폰 DCIM에 기록한다. 이미지 데이터는 VM 공유 폴더에 임시 저장하지 않는다.
- 이 프로젝트는 **이미지를 휴대폰 DCIM으로 보내는 기능**이다. 업무페이지에 TWAIN/WIA 스캐너로 나타나거나 스캔 요청에 이미지를 반환하는 기능은 담당하지 않는다.

### `KyeongwanScanner`

Windows x86용 TWAIN Data Source를 만드는 프로젝트다. 업무페이지의 스캐너 선택 목록에 `kyeongwan-scanner`라는 이름을 노출하는 첫 단계를 목표로 한다. 기존 Canon, HP, UniTwain, VOIM 등의 장치 파일을 덮어쓰지 않고 별도 폴더에 설치하도록 구성돼 있다.

현재 코드는 TWAIN Source identity, 열기/닫기, `%ProgramData%\KyeongwanScanner\VirtualSource\ready`의 완성된 작업 검색, Acquire 이벤트 및 JPG의 Native Transfer 반환까지 작성되어 있다. 모든 페이지 반환 후 업무페이지가 `MSG_ENDXFER`로 완료를 확인하면 해당 작업 폴더를 삭제하며, 취소·실패·미완료 작업은 재시도할 수 있도록 `ready`에 남긴다. 송신 중 취소한 작업은 Receiver가 `cancelled`에 보존한다. **2026-10-14 Windows VM에서 identity 덮어쓰기와 `DAT_EVENT` 상수 오류를 수정한 진단 Source로 신청서 스캔이 정상 완료됐다고 사용자가 확인했다. 반환된 페이지가 어떤 `ready` 작업에서 왔는지는 아직 대조하지 않았다.**

### `KyeongwanReceiver`

`phone-dcim-transfer`의 드래그앤드롭 PDF 처리와 JPG 페이지 렌더링 방식을 참고한 중앙 송신 GUI와 접수 PC Receiver가 있다. 새 `relay_server.py`는 중앙 작업을 암호화해 임시 저장하고, `relay_receiver.py`는 각 VM에서 바깥으로 폴링해 지정된 자리 작업만 로컬 `%ProgramData%\KyeongwanScanner\VirtualSource\ready\<job-id>`에 공개한다. 일반 릴레이 모드는 HTTPS, 중앙/자리별 토큰과 출발 IP 허용 목록을 사용한다. Render 무료 시험 모드는 Render 앞단 HTTPS와 강한 bearer token을 사용하고 IP allow-list는 적용하지 않으며, 무료 인스턴스의 임시 저장소에 의존한다. Receiver가 안전하게 로컬에 저장한 뒤 ack하면 릴레이 사본을 삭제하고, TWAIN이 모든 페이지를 성공적으로 반환하면 VM의 작업 사본도 삭제한다. Render Blueprint는 준비됐지만 실제 배포하지 않았고, 무료 서비스는 유휴 시 잠들며 재시작 시 대기 작업을 잃을 수 있어 시험 문서 전용이다. 구형 `receiver.py` 직접 연결 방식은 개발 호환용으로 남아 있다. 릴레이와 TWAIN 연결은 Windows에서 아직 종단간 검증되지 않았다. 상세 실행 방법과 보안 한계는 [`KyeongwanReceiver/README.md`](KyeongwanReceiver/README.md)에 있다.

중앙 송신 GUI와 시험 릴레이는 macOS에서도 실행할 수 있다. `make_test_tls.py`는 사설 LAN 시험용 CA/서버 인증서를 만든다. 새 릴레이 모드에서는 Windows VM이 Mac 서버에 아웃바운드 HTTPS로 접속하므로 VM의 인바운드 포트 전달은 필요 없다. 다만 VM과 Mac 사이에 라우팅 경로가 있어야 하며 VMware NAT/브리지 전환은 하지 않는다. 이 시험용 인증서는 운영용이 아니다. 릴레이 서버 배포 및 macOS/Windows 간 전송은 실제 환경에서 아직 확인하지 않았다.

### `NEW_WORK` 중앙 준비 앱과 Receiver

`NEW_WORK`에는 신분증 JPG OCR과 서류 PDF 페이지 변환을 함께 준비하는 GUI가 있다. 중앙 사용자가 접수 PC와 SK/KT 포탈을 선택해 저장하면 이미 생성된 ZIP 전체와 OCR 검토값을 `KyeongwanReceiver/relay_server.py` HTTPS 릴레이에 전송한다. ZIP이 40 MiB보다 크면 최대 40 MiB 조각으로 보내며, Relay의 `new-work-zip-v1` 지원 배포가 필요하다. 접수 PC의 `receiver_run.py` UI는 Render에서 자기 자리 작업을 폴링해 ZIP을 검증·압축 해제하고, SK `sk_report.txt`를 교체하거나 해당 PC의 KT `myCustomer.db`를 갱신한 뒤에만 릴레이에 완료 응답을 보낸다. PDF는 Receiver의 자리별 대기열에서 순서를 정하고, 맨 위의 연속 문서만 가상 스캐너의 `ready` 폴더에 올린다.

중앙 OCR 미리보기의 `idCrop`/`CR.jpg` 영역은 종류별 고정 크기로 표시하고 드래그해 위치만 조정할 수 있다. 선택 좌표는 작업 패키지의 `reviewed_values._idCropBounds`로 전달되며 SK Base64 크롭과 Receiver KT `CR.jpg` 생성에 사용된다. KT 쪽 수동 영역은 이 좌표 필드를 지원하는 Receiver 빌드가 필요하다.

Receiver의 **받은 자료 초기화**는 로컬 수신 작업 패키지와 신분증/OCR/서류 메모리 참조, 스캔 목록 상태, 설정된 스캐너 `ready` 폴더 안의 작업을 강제 삭제한다. 진행 중인 페이지 요청이 끝난 뒤 정리하며, 적용이 시작된 작업은 적용과 Relay 응답을 마친 뒤 정리한다. 진행 중 스캔은 실패할 수 있다. 이미 적용된 SK/KT 대상 파일과 백업, Relay에 남은 미수신 작업은 유지한다.

Render 연결의 레포지토리 설정(`render.yaml`, 실행 코드, API)은 준비돼 있다. Receiver는 GUI가 열린 동안 2초 간격으로 새 작업을 확인하고, 중앙 PC는 1초 간격으로 적용 완료 응답을 확인한다. 이 간격 조정은 다음 폴링까지의 대기를 줄이며, 이미지 전송·Receiver 로컬 적용 시간은 별도로 든다. 다만 `KyeongwanReceiver/README.md`의 마지막 기록은 실제 Render 배포와 공개 URL이 없는 상태다. 사용자 Render 대시보드의 현재 상태는 이 저장소만으로 확인할 수 없다. 연결 전에는 배포 주소와 토큰을 확인해야 한다. Render 쪽은 HTTPS 웹 API이며 TCP 연결 위에서 동작한다. 릴레이와 Receiver, 중앙 PC 모두 실제 NEW_WORK 작업으로 아직 종단간 검증되지 않았다. 실행 및 설정 절차는 [`NEW_WORK/RECEIVER_README.md`](NEW_WORK/RECEIVER_README.md)에 있다.

## 지금까지 확인된 중요한 점

- 제공된 업무페이지 스캐너 선택 화면에는 TWAIN으로 보이는 장치와 `WIA-...` 장치가 함께 표시된다. 따라서 화면 전체가 TWAIN 전용이라고 단정할 수 없다.
- 화면의 `(32-32)` 표시는 32비트 호환을 시사하지만, 그것만으로 업무페이지가 어떤 API로 모든 장치를 열거하는지 확정되지는 않는다.
- `KyeongwanScanner`는 TWAIN 방식이다. 진단에서 확인한 결함은 DSM의 identity ID를 덮어쓴 것과 `DAT_EVENT`를 `0x0001`로 잘못 정의한 것이었다. identity 수정 뒤 U.Scan은 `MSG_ENABLEDS`까지 왔고, 잘못된 이벤트 자료형 값 때문에 `cc=13`이 반복되어 로딩이 멈추지 않았다. 이벤트 값을 `0x0002`로 수정한 뒤 사용자가 신청서 스캔 성공을 확인했다.
- Mac에서는 Windows용 TWAIN 바이너리를 빌드하거나 업무페이지와의 장치 호환성을 검증할 수 없다. 이 프로젝트의 C++ Source 빌드는 Windows의 Visual Studio C++ 도구가 필요하다.

## 완성까지 필요한 단계

1. **시험 이미지 일치 확인:** 스캔 전 `ready`의 작업과 페이지를 기록하고, 스캔 후 반환된 페이지가 그중 어느 작업인지 확인한다. 여러 작업이 쌓인 경우 Source는 수정 시각이 가장 오래된 유효 작업부터 선택한다.
2. **다중 페이지·재시도 확인:** 여러 페이지 전송, 취소, 실패 후 재시도를 확인한다.

TWAIN Source, Receiver 대기열 및 중앙 GUI의 송수신·취소 코드는 작성되어 있다. Windows VM에서 스캔 성공은 확인됐지만, 성공한 작업은 `MSG_ENDXFER` 완료 시 `ready`에서 삭제되므로 현재 남아 있는 대기 이미지와 사후 비교할 수 없다. 또 대기 작업이 여러 개면 가장 오래된 유효 작업을 반환한다. 다음 확인은 스캔 전 대기 작업을 기록해 반환 입력을 식별하는 것이다. 기존 `phone-dcim-transfer`는 구현 참고 대상이며, 그 프로젝트의 휴대폰 DCIM 전송 코드는 새 Receiver 큐와 연결되어 있지 않다.

## 기존 장치에 대한 설치 원칙

가상 Source는 고유한 이름과 별도 설치 경로를 사용한다. 기존 제조사 TWAIN/WIA 드라이버, 레지스트리 등록, 휴대폰 연결 설정은 수정하거나 덮어쓰지 않는 방향으로 유지한다. 현재 설치 스크립트는 동일한 프로젝트 파일이 이미 있으면 덮어쓰지 않도록 되어 있다. 실제 대상 PC에 설치하기 전에는 별도 파일만 추가하는지 설치 스크립트를 확인하고, 문제 발생 시 프로젝트 전용 파일만 제거한다.

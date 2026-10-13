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

현재 코드는 TWAIN Source identity, 열기/닫기, `%ProgramData%\KyeongwanScanner\VirtualSource\ready`의 완성된 작업 검색, Acquire 이벤트 및 JPG의 Native Transfer 반환까지 작성되어 있다. 모든 페이지 반환이 끝난 작업은 `completed`로 이동해 보존하며, TWAIN Acquire 취소·실패 작업은 `ready`에 남아 재시도할 수 있다. 송신 중 취소한 작업은 Receiver가 `cancelled`에 보존한다. **목록 표시, 선택, 업무페이지에 실제 이미지 반환은 대상 Windows PC에서 아직 확인되지 않았다.**

### `KyeongwanReceiver`

`phone-dcim-transfer`의 드래그앤드롭 PDF 처리와 JPG 페이지 렌더링 방식을 참고한 중앙 송신 GUI와 접수 PC Receiver가 있다. 중앙 GUI에서 자리를 선택하면 PDF 페이지를 JPG로 렌더링해 해당 Receiver로 보내고, Receiver는 모든 페이지가 도착한 작업만 `%ProgramData%\KyeongwanScanner\VirtualSource\ready\<job-id>`에 공개한다. TWAIN Source는 Acquire 때 이 대기열을 읽고 각 JPG를 스캔 이미지로 반환하도록 작성되어 있다. 중앙 GUI의 전송 취소는 부분 수신 데이터를 삭제하지 않고 `cancelled` 폴더에 보존한다. **이 연결은 아직 Windows에서 빌드 및 업무페이지로 검증되지 않았다.** 상세 실행 방법은 [`KyeongwanReceiver/README.md`](KyeongwanReceiver/README.md)에 있다.

## 지금까지 확인된 중요한 점

- 제공된 업무페이지 스캐너 선택 화면에는 TWAIN으로 보이는 장치와 `WIA-...` 장치가 함께 표시된다. 따라서 화면 전체가 TWAIN 전용이라고 단정할 수 없다.
- 화면의 `(32-32)` 표시는 32비트 호환을 시사하지만, 그것만으로 업무페이지가 어떤 API로 모든 장치를 열거하는지 확정되지는 않는다.
- `KyeongwanScanner`는 현재 TWAIN 방식으로 작성돼 있다. 따라서 첫 검증은 대상 PC에서 업무페이지에 `kyeongwan-scanner`가 표시되는지 확인하는 것이다. 필요하면 별도의 진단 도구로 TWAIN/WIA 목록을 비교한다.
- Mac에서는 Windows용 TWAIN 바이너리를 빌드하거나 업무페이지와의 장치 호환성을 검증할 수 없다. 이 프로젝트의 C++ Source 빌드는 Windows의 Visual Studio C++ 도구가 필요하다.

## 완성까지 필요한 단계

1. **목록 표시:** 업무페이지 설정에 `kyeongwan-scanner`가 나타나는지 확인한다.
2. **선택과 열기:** 해당 Source를 선택했을 때 오류 없이 열리는지 확인한다.
3. **스캔 요청:** 업무페이지의 스캔 동작이 TWAIN Source의 획득 요청까지 도달하는지 확인한다.
4. **시험 이미지 반환:** 테스트 JPG 한 장을 TWAIN 규격에 맞게 반환해 업무페이지에 표시되는지 확인한다.
5. **Receiver 종단 간 확인:** 중앙 GUI에서 해당 접수 자리로 보낸 PDF가 그 PC의 `ready`에 도착하고, 업무페이지 Acquire가 올바른 JPG를 반환하는지 확인한다. 여러 페이지, 취소, 실패 후 재시도도 확인한다.

TWAIN Source, Receiver 대기열 및 중앙 GUI의 송수신·취소 코드는 작성되어 있다. 목록 표시가 확인되기 전에는 업무페이지에서 이 Source가 실제로 선택되고 동작한다고 볼 수 없다. Windows 업무페이지의 실제 Acquire 성공은 검증 전이다. 기존 `phone-dcim-transfer`는 구현 참고 대상이며, 그 프로젝트의 휴대폰 DCIM 전송 코드는 새 Receiver 큐와 연결되어 있지 않다.

## 기존 장치에 대한 설치 원칙

가상 Source는 고유한 이름과 별도 설치 경로를 사용한다. 기존 제조사 TWAIN/WIA 드라이버, 레지스트리 등록, 휴대폰 연결 설정은 수정하거나 덮어쓰지 않는 방향으로 유지한다. 현재 설치 스크립트는 동일한 프로젝트 파일이 이미 있으면 덮어쓰지 않도록 되어 있다. 실제 대상 PC에 설치하기 전에는 별도 파일만 추가하는지 설치 스크립트를 확인하고, 문제 발생 시 프로젝트 전용 파일만 제거한다.

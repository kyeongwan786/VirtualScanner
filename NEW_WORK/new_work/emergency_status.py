"""Show the durable Relay emergency command and Receiver cleanup receipts."""

from __future__ import annotations

import sys

from new_work.relay_client import get_emergency_report
from new_work.relay_config import load_relay_config


def main() -> int:
    try:
        config = load_relay_config()
        if not config:
            raise ValueError("중앙 Relay 설정을 찾을 수 없습니다.")
        report = get_emergency_report(config)
    except Exception as exc:
        print(f"긴급 중단 현황을 가져오지 못했습니다: {exc}", file=sys.stderr)
        return 1

    print(f"긴급 잠금: {'활성' if report['active'] else '비활성'}")
    print(f"명령 ID: {report.get('event_id') or '-'}")
    print(f"명령 시각: {report.get('activated_at') or '-'}")
    print(f"영속 저장소: {'확인됨' if report.get('storage_persistent') else '확인되지 않음'}")
    sender = report.get("sender", {})
    print(f"송신기: {sender.get('status', 'pending')} · 종료 상태: {sender.get('shutdown', 'not_confirmed')}")
    if sender.get("cleanup_at"):
        print(f"  처리 시각: {sender['cleanup_at']}")
    if sender.get("detail"):
        print(f"  상세: {sender['detail']}")
    print("접수 자리 처리 결과:")
    for station in report.get("stations", []):
        status = station.get("status", "pending")
        label = {
            "pending": "대기 중 (아직 연결/응답 없음)",
            "cleanup_complete": "자료 정리 완료 · 종료 요청됨 (프로세스 종료는 별도 확인 불가)",
            "cleanup_failed": "정리 오류 · 확인 필요",
        }.get(status, status)
        print(f"- {station.get('station_id', '?')}: {label}")
        if station.get("cleanup_at"):
            print(f"  처리 시각: {station['cleanup_at']}")
        if station.get("detail"):
            print(f"  상세: {station['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

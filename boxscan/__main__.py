import argparse
import getpass
import ipaddress
import json
import os
import platform
import shutil
import time
from pathlib import Path

import uvicorn

from . import ocr, reference
from .api import create_app
from .store import JobStore
from .video import record_analysis, run_next


def main() -> None:
    parser = argparse.ArgumentParser(description="Pokémon GO Box Scan")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--lan-ip", type=ipaddress.IPv4Address)
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    commands.add_parser("doctor")
    analyze = commands.add_parser("analyze", help="실험: 추출한 스캔에 OCR·IV 판독 실행")
    analyze.add_argument("scan_id")
    export_cmd = commands.add_parser("export", help="박스를 Excel 파일로 내보내기")
    export_cmd.add_argument("--output", type=Path, help="저장할 .xlsx 경로 (기본: 현재 폴더)")
    backup_cmd = commands.add_parser("backup", help="박스 백업 만들기·검사·복원")
    backup_actions = backup_cmd.add_subparsers(dest="backup_action", required=True)
    backup_create = backup_actions.add_parser("create", help="백업 zip 만들기 (기본: data/backups/)")
    backup_create.add_argument("--output", type=Path)
    backup_create.add_argument("--with-scans", action="store_true", help="판독 프레임·분석 결과 포함")
    backup_create.add_argument("--with-videos", action="store_true", help="원본 영상도 포함 (--with-scans 필요)")
    backup_inspect = backup_actions.add_parser("inspect", help="백업을 바꾸지 않고 검사")
    backup_inspect.add_argument("path", type=Path)
    backup_restore = backup_actions.add_parser("restore", help="검사 후 현재 박스를 백업하고 복원")
    backup_restore.add_argument("path", type=Path)
    backup_restore.add_argument("--yes", action="store_true", help="확인 질문 없이 복원")
    ref = commands.add_parser("reference", help="레벨 계산용 기준 데이터 관리")
    ref.add_argument("action", choices=["update", "rebuild", "show"])
    raids_cmd = commands.add_parser("raids", help="현재 레이드·맥스배틀 보스 목록 갱신·확인")
    raids_cmd.add_argument("action", choices=["update", "show"])
    accuracy_cmd = commands.add_parser("accuracy", help="판독 후보와 저장한 값을 비교해 정확도·자동 저장 시뮬레이션 보기")
    accuracy_cmd.add_argument("scan_ids", nargs="*", help="비교할 스캔 ID (기본: 저장 기록이 있는 모든 스캔)")
    accuracy_cmd.add_argument("--details", action="store_true", help="틀린 값을 하나씩 보여 주기")
    accuracy_cmd.add_argument("--json", type=Path, help="결과를 JSON 파일로도 저장")
    args = parser.parse_args()
    if args.command == "accuracy":
        from . import accuracy
        try:
            report = accuracy.measure(args.data_dir, args.scan_ids or None)
        except FileNotFoundError as error:
            accuracy_cmd.error(str(error))
        print("\n".join(accuracy.format_report(report, args.details)))
        if args.json:
            args.json.write_text(json.dumps(accuracy.to_json(report), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"JSON: {args.json}")
        return
    if args.command == "serve":
        host = "127.0.0.1"
        password = None
        if args.lan_ip:
            networks = map(ipaddress.ip_network, ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])
            if not any(args.lan_ip in network for network in networks):
                serve.error("--lan-ip에 Mac의 집 네트워크 IPv4 주소를 지정하세요.")
            host = str(args.lan_ip)
            password = getpass.getpass("iPhone에서 입력할 임시 접속 암호(12자 이상): ")
            if len(password) < 12:
                serve.error("접속 암호는 12자 이상이어야 합니다.")
        uvicorn.run(create_app(args.data_dir, host, password, auto_backup=True), host=host, port=args.port,
                    proxy_headers=False)
        return

    if args.command == "backup":
        from . import backup, export

        def show(summary):
            box = summary["box"]
            print(f"만든 시각 {summary['created_at']} · 앱 {summary['app_version']} · "
                  f"박스 {box['pokemon']}개체(제거 {box['removed']}) · 이력 {box['revisions']}개 · "
                  f"기준 데이터 {summary['reference_version'] or '-'} · "
                  f"스캔 {summary['scans'] if summary['with_scans'] else '미포함'}"
                  f"{' · 원본 영상 포함' if summary['with_videos'] else ''}")

        if args.backup_action == "create":
            if args.with_videos and not args.with_scans:
                backup_create.error("--with-videos는 --with-scans와 함께 쓰세요.")
            manifest = backup.create(args.data_dir, args.output, args.with_scans, args.with_videos,
                                     export.app_version())
            print(f"백업을 만들었습니다: {manifest['path']} ({manifest['bytes']:,} bytes)")
            return
        try:
            summary = backup.inspect(args.path)
        except backup.BackupError as error:
            raise SystemExit(f"사용할 수 없는 백업입니다: {error}")
        show(summary)
        if args.backup_action == "inspect":
            print("검사를 통과했습니다. 아무것도 바꾸지 않았습니다.")
            return
        if not args.yes and input("현재 박스를 이 백업으로 바꿀까요? 현재 박스는 먼저 백업합니다. [y/N] ").strip().lower() != "y":
            raise SystemExit("복원하지 않았습니다.")
        result = backup.restore(args.data_dir, args.path, export.app_version())
        print(f"복원했습니다. 복원 전 박스 백업: {result['safety_backup']} · 추가한 스캔 {result['scans_added']}개")
        return

    if args.command == "export":
        from . import export
        from .box import BoxStore
        output = args.output or Path(export.filename())
        if output.suffix.lower() != ".xlsx":
            export_cmd.error("--output은 .xlsx 파일이어야 합니다.")
        entries = BoxStore(args.data_dir).list()
        try:
            data = reference.load(args.data_dir)
        except (OSError, ValueError, KeyError):
            data = None
        output.write_bytes(export.workbook_bytes(entries, data, export.app_version(), BoxStore(args.data_dir).candies()))
        print(f"{len(entries)}개체를 {output.resolve()}에 저장했습니다.")
        return

    if args.command == "reference":
        if args.action == "update":
            print("기준 데이터를 내려받습니다:")
            for name, url in reference.SOURCES.items():
                print(f"  {name} ← {url}")
            try:
                manifest = reference.update(args.data_dir)
            except (OSError, ValueError) as error:
                raise SystemExit(f"갱신하지 못했습니다. 기존 데이터를 유지합니다: {error}")
            print(f"버전 {manifest['version']} ({manifest['fetched_at']})")
        if args.action == "rebuild":
            try:
                manifest = reference.rebuild(args.data_dir)
            except (OSError, ValueError) as error:
                raise SystemExit(str(error))
            print(f"버전 {manifest['version']}의 정리 파일을 내려받기 없이 다시 만들었습니다.")
        data = reference.load(args.data_dir)
        if data is None:
            raise SystemExit("기준 데이터가 없습니다. `reference update`를 실행하세요.")
        print(f"현재 버전 {data.version} · 받은 시각 {data.fetched_at} · "
              f"포켓몬 {len(data.stats)}종 · 한국어 이름 {len(data.korean_names)}개 · "
              f"최대 레벨 {max(data.cpm)}")
        return

    if args.command == "raids":
        from . import raid_bosses
        if args.action == "update":
            print(f"현재 보스 목록을 내려받습니다 ({raid_bosses.SOURCE_NAME}):")
            for name, url in raid_bosses.SOURCES.items():
                print(f"  {name} ← {url}")
            try:
                manifest = raid_bosses.update(args.data_dir)
            except (OSError, ValueError) as error:
                raise SystemExit(f"갱신하지 못했습니다. 기존 목록을 유지합니다: {error}")
            for name, error in manifest["errors"].items():
                print(f"  {name}: 받지 못함 ({error})")
            from . import events
            print(f"이벤트 달력을 내려받습니다 ({events.SOURCE_NAME}): {events.SOURCE_URL}")
            try:
                print(f"  이벤트 {events.update(args.data_dir)['count']}개")
            except (OSError, ValueError) as error:
                print(f"  갱신하지 못했습니다. 기존 달력을 유지합니다: {error}")
        listing = raid_bosses.current(args.data_dir)
        if listing is None:
            raise SystemExit("저장된 보스 목록이 없습니다. `raids update`를 실행하세요.")
        data = reference.load(args.data_dir)
        if data is None:
            raise SystemExit("기준 데이터가 없습니다. `reference update`를 실행하세요.")
        result = raid_bosses.mapped(data, listing)
        print(f"목록 {result['version']} · 받은 시각 {result['fetched_at']}")
        for kind, title in (("raids", "레이드"), ("max_battles", "맥스배틀")):
            print(f"{title} {len(result[kind])}개")
            for boss in result[kind]:
                if boss["status"] == "ok":
                    print(f"  [{boss['tier_name']}] {boss['label']}" + "".join(f"\n      · {n}" for n in boss["notes"]))
                else:
                    print(f"  [매핑 실패] {boss['label']}: {boss['reason']}")
        return

    ffmpeg = shutil.which(os.environ.get("BOXSCAN_FFMPEG", "ffmpeg"))
    if args.command == "doctor":
        print(f"OS: {platform.platform()}")
        print(f"Python: {platform.python_version()}")
        print(f"FFmpeg: {ffmpeg or '없음 (Mac: brew install ffmpeg)'}")
        print(f"Swift (Apple Vision OCR): {ocr.swiftc() or '없음 (Mac: xcode-select --install)'}")
        print(f"Data directory: {args.data_dir.resolve()}")
        raise SystemExit(0 if ffmpeg else 1)
    if not ffmpeg:
        parser.error("FFmpeg가 필요합니다. doctor 명령으로 환경을 확인하세요.")
    store = JobStore(args.data_dir)
    if args.command == "analyze":
        from .analysis import RESULT, analyze_scan, summary_lines
        if not ocr.swiftc():
            analyze.error("Apple Vision OCR에는 Xcode 명령줄 도구가 설치된 Mac이 필요합니다.")
        job = store.get(args.scan_id)
        if job is None or job["status"] != "ready":
            analyze.error("프레임 추출을 마친 스캔만 분석할 수 있습니다.")
        record_analysis(store, args.scan_id, lambda scan_id: analyze_scan(store, scan_id, ffmpeg))
        job = store.get(args.scan_id)
        if job["analysis_status"] != "done":
            raise SystemExit(job["analysis_error"])
        result = json.loads((store.root / args.scan_id / RESULT).read_text(encoding="utf-8"))
        print("\n".join(summary_lines(result)))
        print(f"후보 결과: {store.root / args.scan_id / RESULT}")
        print("자동 판독 후보입니다. 원본 프레임과 비교한 뒤 사용하세요.")
        return
    from .analysis import make_analyzer
    analyzer, message = make_analyzer(store, ffmpeg)
    print(f"Watching {store.root}", flush=True)
    print(message, flush=True)
    try:
        while True:
            processed = run_next(store, ffmpeg, analyzer)
            if args.once:
                break
            if not processed:
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

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
    ref = commands.add_parser("reference", help="레벨 계산용 기준 데이터 관리")
    ref.add_argument("action", choices=["update", "rebuild", "show"])
    args = parser.parse_args()
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
        uvicorn.run(create_app(args.data_dir, host, password), host=host, port=args.port,
                    proxy_headers=False)
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
        output.write_bytes(export.workbook_bytes(entries, data, export.app_version()))
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

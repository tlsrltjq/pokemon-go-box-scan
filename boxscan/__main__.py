import argparse
import getpass
import ipaddress
import os
import platform
import shutil
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .store import JobStore
from .video import run_next


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

    ffmpeg = shutil.which(os.environ.get("BOXSCAN_FFMPEG", "ffmpeg"))
    if args.command == "doctor":
        print(f"OS: {platform.platform()}")
        print(f"Python: {platform.python_version()}")
        print(f"FFmpeg: {ffmpeg or '없음 (Mac: brew install ffmpeg)'}")
        print(f"Data directory: {args.data_dir.resolve()}")
        raise SystemExit(0 if ffmpeg else 1)
    if not ffmpeg:
        worker.error("FFmpeg가 필요합니다. doctor 명령으로 환경을 확인하세요.")
    store = JobStore(args.data_dir)
    print(f"Watching {store.root}", flush=True)
    try:
        while True:
            processed = run_next(store, ffmpeg)
            if args.once:
                break
            if not processed:
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

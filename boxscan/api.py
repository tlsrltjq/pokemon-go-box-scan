import base64
import binascii
import secrets
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from .store import JobStore
from .video import MAX_FRAMES

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
WEB = Path(__file__).with_name("web")


def create_app(
    data_dir: Path,
    host: str = "127.0.0.1",
    lan_password: str | None = None,
) -> FastAPI:
    if host != "127.0.0.1" and not lan_password:
        raise ValueError("LAN 모드에는 접속 암호가 필요합니다.")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = JobStore(data_dir)

    @app.middleware("http")
    async def protect_local_data(request: Request, call_next):
        if lan_password is not None:
            scheme, _, value = request.headers.get("authorization", "").partition(" ")
            try:
                credentials = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                credentials = b""
            if scheme.lower() != "basic" or not secrets.compare_digest(
                credentials, f"boxscan:{lan_password}".encode()
            ):
                return JSONResponse(
                    {"detail": "접속 암호가 필요합니다."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Box Scan", charset="UTF-8"'},
                )
        if request.method == "POST":
            expected = f"{request.url.scheme}://{request.headers['host']}"
            origin = request.headers.get("origin")
            if origin is not None and origin != expected:
                return JSONResponse({"detail": "다른 사이트의 요청입니다."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"] if host == "127.0.0.1" else [host],
    )
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/scans", status_code=202)
    async def upload(request: Request, filename: str):
        if (
            not filename or len(filename) > 255
            or any(ord(char) < 32 for char in filename)
            or "/" in filename or chr(92) in filename
            or Path(filename).suffix.lower() not in {".mp4", ".mov"}
        ):
            raise HTTPException(422, "MOV 또는 MP4 파일을 선택하세요.")
        if request.headers.get("content-type") != "application/octet-stream":
            raise HTTPException(415, "영상 바이너리 요청만 지원합니다.")
        scan_id = str(uuid4())
        folder = store.root / scan_id
        folder.mkdir()
        source = folder / "source"
        try:
            size = 0
            with source.open("xb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "영상은 200 MiB 이하로 잘라서 올려 주세요.")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "빈 파일입니다.")
            store.create(scan_id, filename)
        except BaseException:
            source.unlink(missing_ok=True)
            folder.rmdir()
            raise
        return {"id": scan_id, "status": "queued"}

    @app.get("/api/scans/{scan_id}")
    def scan(scan_id: UUID):
        job = store.get(str(scan_id))
        if job is None:
            raise HTTPException(404, "스캔을 찾을 수 없습니다.")
        job["frames"] = []
        if job["status"] == "ready":
            job["frames"] = [
                f"/api/scans/{scan_id}/frames/{int(path.stem.split('-')[-1])}"
                for path in store.frames(str(scan_id))
            ]
        return job

    @app.get("/api/scans/{scan_id}/frames/{number}")
    def frame(scan_id: UUID, number: int):
        job = store.get(str(scan_id))
        path = store.root / str(scan_id) / f"frame-{number:03d}.jpg"
        if not job or job["status"] != "ready" or not 1 <= number <= MAX_FRAMES or not path.is_file():
            raise HTTPException(404, "프레임을 찾을 수 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    return app

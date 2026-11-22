import base64
import binascii
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from . import backup, cleanup, export, live, max_battle, raid, reference, review
from .forms import display_name
from .species import TEMP_NAMES
from .analysis import LIVE_FILE, RESULT, detail_frame
from .box import BoxStore, CodeTaken, Conflict
from .store import JobStore
from .video import MAX_FRAMES

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_BACKUP_UPLOAD_BYTES = 8 * 1024 ** 3


async def _json_body(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON 요청만 지원합니다.")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "요청 형식이 올바르지 않습니다.") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "요청 형식이 올바르지 않습니다.")
    return payload
WEB = Path(__file__).with_name("web")


def create_app(
    data_dir: Path,
    host: str = "127.0.0.1",
    lan_password: str | None = None,
    live_manager: live.Manager | None = None,
) -> FastAPI:
    if host != "127.0.0.1" and not lan_password:
        raise ValueError("LAN 모드에는 접속 암호가 필요합니다.")
    store = JobStore(data_dir)
    box = BoxStore(store.root)
    capture = live_manager or live.Manager(store)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        capture.shutdown()  # finish the live scan with what it has read

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

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
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
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

    @app.get("/box")
    def box_page():
        return FileResponse(WEB / "box.html")

    @app.get("/raid")
    def raid_page():
        return FileResponse(WEB / "raid.html")

    @app.get("/max")
    def max_page():
        return FileResponse(WEB / "max.html")

    def load_reference():
        try:
            data = reference.load(store.root)
        except (OSError, ValueError, KeyError):
            data = None
        if data is None or not data.move_stats:
            raise HTTPException(409, "전투 계산용 기준 데이터가 없습니다. Mac에서 reference update를 실행하세요.")
        return data

    @app.get("/api/raid/options")
    def raid_options(species: str | None = None):
        data = load_reference()
        options = {
            "species": sorted(name for name, pokemon_id in data.korean_names.items() if pokemon_id in data.stats),
            "tiers": [{"id": key, **{k: v for k, v in rule.items() if k != "cpm"}}
                      for key, rule in raid.RULESET["tiers"].items()],
            "weather": [{"id": key, "name": name} for key, name in raid.WEATHER_NAMES.items()],
            "ruleset": raid.RULESET["version"],
        }
        if species:
            forms = data.forms(species)
            if not forms:
                raise HTTPException(404, "기준 데이터에서 그 종을 찾지 못했습니다.")
            pokemon_id = forms[0].pokemon_id
            learnset = (data.learnsets or {}).get(f"{pokemon_id}|Normal") or {}
            options["boss"] = {
                "forms": [{"id": f.form, "name": display_name(pokemon_id, f.form) or "기본 모습"} for f in forms],
                "temp": [{"id": k, "name": TEMP_NAMES.get(k, k)} for k in (data.temp_stats or {}).get(pokemon_id, {})],
                "fast": [{"id": m, "name": data.move_names.get(m, m)} for m in learnset.get("fast", [])],
                "charged": [{"id": m, "name": data.move_names.get(m, m)} for m in learnset.get("charged", [])],
            }
        return options

    @app.post("/api/raid/recommend")
    async def raid_recommend(request: Request):
        payload = await _json_body(request)
        data = load_reference()

        def text(key):
            value = payload.get(key)
            return value.strip() or None if isinstance(value, str) else None

        def number(key):
            value = payload.get(key)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                raise HTTPException(422, "숫자 입력을 확인하세요.") from None

        species = text("species")
        if not species:
            raise HTTPException(422, "보스 이름을 입력하세요.")
        try:
            result = raid.recommend(
                data, box.list(), species, text("tier") or "5", form=text("form"), temp=text("temp"),
                weather=text("weather") or "", time_limit_s=number("time_limit_s"),
                rejoin_delay_s=number("rejoin_delay_s"), boss_fast=text("boss_fast"),
                boss_charged=text("boss_charged"), include_mega=payload.get("include_mega") is True)
            if payload.get("compare_weather") is True:
                result["weather_comparison"] = raid.compare_weather(
                    data, box.list(), species, text("tier") or "5", form=text("form"), temp=text("temp"),
                    time_limit_s=number("time_limit_s"), rejoin_delay_s=number("rejoin_delay_s"),
                    boss_fast=text("boss_fast"), boss_charged=text("boss_charged"),
                    include_mega=payload.get("include_mega") is True)
        except raid.RaidError as error:
            raise HTTPException(422, str(error)) from None
        result["boss"]["temp_name"] = TEMP_NAMES.get(result["boss"]["temp"])
        return result

    @app.get("/api/max/options")
    def max_options(species: str | None = None):
        data = load_reference()
        options = {
            "species": sorted(name for name, pokemon_id in data.korean_names.items() if pokemon_id in data.stats),
            "tiers": [{"id": key, "name": rule["name"], "hp": rule["hp"], "time_limit": rule["time_limit"]}
                      for key, rule in max_battle.RULESET["tiers"].items()],
            "weather": [{"id": key, "name": name} for key, name in raid.WEATHER_NAMES.items()],
            "ruleset": max_battle.RULESET["version"],
        }
        if species:
            forms = data.forms(species)
            if not forms:
                raise HTTPException(404, "기준 데이터에서 그 종을 찾지 못했습니다.")
            pokemon_id = forms[0].pokemon_id
            learnset = (data.learnsets or {}).get(f"{pokemon_id}|Normal") or {}
            options["boss"] = {
                "forms": [{"id": f.form, "name": display_name(pokemon_id, f.form) or "기본 모습"} for f in forms],
                "charged": [{"id": m, "name": data.move_names.get(m, m)} for m in learnset.get("charged", [])],
            }
        return options

    @app.post("/api/max/recommend")
    async def max_recommend(request: Request):
        payload = await _json_body(request)
        data = load_reference()

        def text(key):
            value = payload.get(key)
            return value.strip() or None if isinstance(value, str) else None

        def number(key):
            value = payload.get(key)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                raise HTTPException(422, "숫자 입력을 확인하세요.") from None

        species = text("species")
        if not species:
            raise HTTPException(422, "보스 이름을 입력하세요.")
        trainers = number("trainers") or 1
        if trainers != int(trainers):
            raise HTTPException(422, "함께하는 트레이너 수는 1~4명입니다.")
        try:
            return max_battle.recommend(
                data, box.list(), species, text("tier") or "1", form=text("form"),
                weather=text("weather") or "", time_limit_s=number("time_limit_s"),
                boss_charged=text("boss_charged"), orbs=payload.get("orbs") is not False, trainers=int(trainers))
        except raid.RaidError as error:
            raise HTTPException(422, str(error)) from None

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

    @app.get("/api/live")
    def live_status():
        return {"session": capture.status(), "max_seconds": live.MAX_SECONDS}

    @app.get("/api/live/windows")
    def live_windows():
        try:
            return {"windows": capture.windows()}
        except live.LiveError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/live/start", status_code=202)
    async def live_start(request: Request):
        payload = await _json_body(request)
        window = payload.get("window_id")
        if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
            raise HTTPException(422, "캡처할 창을 선택하세요.")
        label = payload.get("label")
        label = label.strip()[:40] or None if isinstance(label, str) else None
        try:
            return {"id": capture.start(window, label)}
        except live.LiveError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/live/stop")
    def live_stop():
        session = capture.stop()
        if session is None:
            raise HTTPException(409, "실행 중인 실시간 캡처가 없습니다.")
        return session

    @app.get("/api/scans")
    def scan_list():
        rows = cleanup.scans(store, box.saved_counts())
        versions = cleanup.reference_versions(store.root)
        return {"scans": rows, "bytes": sum(row["bytes"] for row in rows),
                "reference": {"versions": versions, "bytes": sum(v["bytes"] for v in versions),
                              "prunable": sum(v["bytes"] for v in versions if not v["keep"])}}

    @app.post("/api/scans/{scan_id}/trim")
    def scan_trim(scan_id: UUID):
        try:
            return {"freed": cleanup.trim(store, str(scan_id))}
        except cleanup.CleanupError as error:
            raise HTTPException(409, str(error)) from None

    @app.delete("/api/scans/{scan_id}")
    def scan_delete(scan_id: UUID):
        try:
            return {"freed": cleanup.delete(store, str(scan_id))}
        except cleanup.CleanupError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/reference/prune")
    def reference_prune():
        return {"freed": cleanup.prune_reference(store.root)}

    @app.get("/api/scans/{scan_id}")
    def scan(scan_id: UUID):
        job = store.get(str(scan_id))
        if job is None:
            raise HTTPException(404, "스캔을 찾을 수 없습니다.")
        current = capture.status()
        job["live"] = ((current if current and current["scan_id"] == str(scan_id) else {"running": False})
                       if (store.root / str(scan_id) / LIVE_FILE).is_file()
                       or job["analysis_status"] == "live" else None)
        job["frames"] = []
        if job["status"] == "ready":
            job["frames"] = [
                f"/api/scans/{scan_id}/frames/{int(path.stem.split('-')[-1])}"
                for path in store.frames(str(scan_id))
            ]
        job["analysis"] = _analysis(store.root / str(scan_id), str(scan_id),
                                    job.pop("analysis_status"), job.pop("analysis_error"))
        for index, individual in enumerate(job["analysis"]["individuals"]):
            individual["approval"] = box.approval(str(scan_id), index)
            individual["code_match"] = _code_match(individual)
        return job

    def _code_match(individual: dict) -> dict | None:
        """Spec 6.3: the management code from the nickname, counted within the species read on screen,
        suggests the entry to update. A removed entry is shown, not selected."""
        code = (individual.get("code") or {}).get("value")
        if not code:
            return None
        species = (individual.get("species") or {}).get("value")
        if not species:
            return {"code": code, "status": "species_unknown"}
        entry = box.by_code(species, code)
        if entry is None:
            return {"code": code, "status": "not_found", "species": species}
        return {"code": code, "status": "deleted" if entry["deleted"] else "match", "pokemon_id": entry["id"],
                "species": entry.get("species"), "cp": entry.get("cp"), "revision": entry["revision"]}

    @app.post("/api/scans/{scan_id}/individuals/{index}/approve")
    async def approve(scan_id: UUID, index: int, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON 요청만 지원합니다.")
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "요청 형식이 올바르지 않습니다.") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "요청 형식이 올바르지 않습니다.")
        job = store.get(str(scan_id))
        result_path = store.root / str(scan_id) / RESULT
        if job is None or job["status"] != "ready" or not result_path.is_file():
            raise HTTPException(404, "분석을 마친 스캔을 찾을 수 없습니다.")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not 0 <= index < len(result["individuals"]):
            raise HTTPException(404, "판독 후보를 찾을 수 없습니다.")
        try:
            data = reference.load(store.root)
        except (OSError, ValueError, KeyError):
            data = None
        try:
            values = review.normalize(payload.get("values"), data)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        target = payload.get("target")
        if target is not None:
            try:
                target = str(UUID(str(target)))
            except ValueError:
                raise HTTPException(422, "갱신할 개체를 다시 선택하세요.") from None
            existing = box.get(target)
            if existing is None:
                raise HTTPException(409, "갱신할 개체가 박스에 없습니다.")
            found = review.warnings(existing, values)
            read_code = (result["individuals"][index].get("code") or {}).get("value")
            if read_code and read_code != existing["code"]:
                found.append(f"별명에서 읽은 관리번호는 {read_code}인데 {existing.get('species') or ''} "
                             f"{existing['code']}을(를) 갱신하려고 합니다.")
            if found and payload.get("acknowledge") is not True:
                return JSONResponse({"detail": "기존 개체와 다른 점이 있습니다.", "warnings": found},
                                    status_code=409)
        candidate = result["individuals"][index]
        source = {
            "scan_id": str(scan_id),
            "individual": index,
            "filename": job["filename"],
            "frames": candidate["frames"],
            "recognizer": result.get("recognizer"),
            "analysis_created_at": result.get("created_at"),
            "candidate": {key: (candidate.get(key) or {}).get("value")
                          for key in ("species", "cp", "max_hp")},
        }
        try:
            entry = box.approve(str(scan_id), index, values, source, target,
                                payload.get("expected_revision"))
        except Conflict as error:
            raise HTTPException(409, str(error)) from None
        resources = dict(candidate.get("resources") or {})
        if resources and not entry.get("duplicate"):
            if resources.get("family") and values.get("species") and data:
                # Saved with a corrected species: the candy belongs to that species' family.
                expected = data.candy_name(values["species"])
                resources["family"] = resources["family"] if resources["family"] == expected else expected
            box.record_resources(resources, {"scan_id": str(scan_id), "individual": index})
        return entry

    def _with_family(entries: list[dict]) -> list[dict]:
        try:
            data = reference.load(store.root)
        except (OSError, ValueError, KeyError):
            data = None
        for entry in entries:
            entry["candy_family"] = data.candy_name(entry.get("species")) if data else None
        return entries

    @app.get("/api/pokemon")
    def pokemon_list(species: str | None = None):
        return {"pokemon": _with_family(box.list(species))}

    @app.get("/api/candies")
    def candy_list():
        return box.candies()

    @app.patch("/api/candies/{family}")
    async def candy_edit(family: str, request: Request):
        payload = await _json_body(request)
        try:
            return box.set_candy(family, payload)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post("/api/pokemon", status_code=201)
    async def pokemon_add(request: Request):
        payload = await _json_body(request)
        try:
            data = reference.load(store.root)
        except (OSError, ValueError, KeyError):
            data = None
        try:
            values = review.normalize(payload.get("values"), data)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if not values.get("species"):
            raise HTTPException(422, "종 이름을 입력하세요.")
        note = payload.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 200):
            raise HTTPException(422, "메모는 200자 이하로 적어 주세요.")
        values["cp_source"] = "manual" if values.get("cp") else None
        return box.add(values, note or None)

    @app.get("/api/pokemon/{pokemon_id}")
    def pokemon_detail(pokemon_id: UUID):
        entry = box.get(str(pokemon_id))
        if entry is None:
            raise HTTPException(404, "박스에서 개체를 찾을 수 없습니다.")
        return entry

    @app.get("/api/exports/box.xlsx")
    def export_box():
        try:
            data = reference.load(store.root)
        except (OSError, ValueError, KeyError):
            data = None
        content = export.workbook_bytes(box.list(), data, export.app_version())
        return Response(content, media_type=export.MEDIA_TYPE, headers={
            "Content-Disposition": f'attachment; filename="{export.filename()}"'})

    @app.patch("/api/pokemon/{pokemon_id}")
    async def pokemon_edit(pokemon_id: UUID, request: Request):
        payload = await _json_body(request)
        existing = box.get(str(pokemon_id))
        if existing is None:
            raise HTTPException(404, "박스에서 개체를 찾을 수 없습니다.")
        revision = payload.get("expected_revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise HTTPException(422, "현재 revision을 함께 보내야 합니다.")
        try:
            data = reference.load(store.root)
        except (OSError, ValueError, KeyError):
            data = None
        try:
            values = review.normalize(payload.get("values"), data)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if "max_moves" not in (payload.get("values") or {}):
            values["max_moves"] = existing.get("max_moves") or []  # an older client did not send them
        # A CP the user typed is a manual value; an unchanged CP keeps where it came from.
        changed = (values["cp"], values["max_hp"]) != (existing.get("cp"), existing.get("max_hp"))
        values["cp_source"] = "manual" if changed else existing.get("cp_source")
        note = payload.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 200):
            raise HTTPException(422, "수정 메모는 200자 이하로 적어 주세요.")
        try:
            return box.edit(str(pokemon_id), values, revision, note or None)
        except Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/backups")
    def backups_list():
        return {"backups": backup.listing(store.root)}

    @app.post("/api/backups")
    async def backups_create(request: Request):
        payload = await _json_body(request)
        with_scans = payload.get("with_scans") is True
        manifest = backup.create(store.root, with_scans=with_scans,
                                 with_videos=with_scans and payload.get("with_videos") is True,
                                 app_version=export.app_version())
        return {"name": Path(manifest["path"]).name, "bytes": manifest["bytes"], "box": manifest["box"],
                "with_scans": manifest["with_scans"], "with_videos": manifest["with_videos"]}

    @app.get("/api/backups/{name}")
    def backups_download(name: str):
        path = backup.folder(store.root) / name
        if not backup.NAME.match(name) or not path.is_file():
            raise HTTPException(404, "백업 파일을 찾을 수 없습니다.")
        return FileResponse(path, media_type="application/zip", filename=name)

    @app.post("/api/backups/preview")
    async def backups_preview(request: Request):
        """Receive a backup zip, check it, and keep it until the user confirms the restore."""
        if request.headers.get("content-type") != "application/octet-stream":
            raise HTTPException(415, "백업 파일을 그대로 보내 주세요.")
        uploads = backup.folder(store.root) / "uploads"
        uploads.mkdir(exist_ok=True)
        token = uuid4().hex
        path = uploads / f"{token}.zip"
        size = 0
        try:
            with path.open("xb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BACKUP_UPLOAD_BYTES:
                        raise HTTPException(413, "백업 파일이 너무 큽니다.")
                    output.write(chunk)
            summary = backup.inspect(path)
        except backup.BackupError as error:
            path.unlink(missing_ok=True)
            raise HTTPException(422, str(error)) from None
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"token": token, "summary": summary}

    @app.post("/api/backups/restore")
    async def backups_restore(request: Request):
        payload = await _json_body(request)
        token, name = payload.get("token"), payload.get("name")
        if isinstance(token, str) and len(token) == 32 and all(c in "0123456789abcdef" for c in token):
            path = backup.folder(store.root) / "uploads" / f"{token}.zip"
        elif isinstance(name, str) and backup.NAME.match(name):
            path = backup.folder(store.root) / name
        else:
            raise HTTPException(422, "복원할 백업을 다시 선택하세요.")
        if not path.is_file():
            raise HTTPException(404, "백업 파일을 찾을 수 없습니다. 다시 올려 주세요.")
        try:
            result = backup.restore(store.root, path, export.app_version())
        except backup.BackupError as error:
            raise HTTPException(422, str(error)) from None
        if token:
            path.unlink(missing_ok=True)
        result["safety_backup"] = Path(result["safety_backup"]).name
        return result

    @app.post("/api/pokemon/{pokemon_id}/code")
    async def pokemon_code(pokemon_id: UUID, request: Request):
        payload = await _json_body(request)
        revision, number = payload.get("expected_revision"), payload.get("number")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise HTTPException(422, "현재 revision을 함께 보내야 합니다.")
        if isinstance(number, str) and number.strip().isdigit():
            number = int(number.strip())
        try:
            return box.set_code(str(pokemon_id), number, revision, payload.get("swap") is True)
        except CodeTaken as error:
            holder = error.holder
            return JSONResponse({"detail": str(error), "holder": {key: holder.get(key) for key in (
                "id", "code", "species", "cp", "nickname", "deleted")}}, status_code=409)
        except Conflict as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.delete("/api/pokemon/{pokemon_id}", status_code=204)
    def pokemon_delete(pokemon_id: UUID, revision: int):
        try:
            box.delete(str(pokemon_id), revision)
        except Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/scans/{scan_id}/evidence/{number}")
    def evidence(scan_id: UUID, number: int):
        job = store.get(str(scan_id))
        path = store.root / str(scan_id) / f"iv-{number:02d}.jpg"
        if not job or job["status"] != "ready" or not 1 <= number <= 99 or not path.is_file():
            raise HTTPException(404, "판독 근거 프레임을 찾을 수 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/scans/{scan_id}/frames/{number}")
    def frame(scan_id: UUID, number: int):
        job = store.get(str(scan_id))
        path = store.root / str(scan_id) / f"frame-{number:03d}.jpg"
        if (not job or job["status"] != "ready" or not 1 <= number <= max(MAX_FRAMES, live.MAX_FRAMES)
                or not path.is_file()):
            raise HTTPException(404, "프레임을 찾을 수 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    return app


def _analysis(folder: Path, scan_id: str, status: str | None, error: str | None) -> dict:
    """Summarize analysis.json for the browser. Raw OCR text stays on disk."""
    result_path = folder / RESULT
    if status is None:  # scans from before automatic analysis
        status = "done" if result_path.is_file() else "none"
    summary = {"status": status, "error": error, "individuals": [], "unassigned": 0}
    # A live capture shows its partial result while it runs.
    if status not in ("done", "live") or not result_path.is_file():
        return summary
    result = json.loads(result_path.read_text(encoding="utf-8"))
    readings = {frame["frame"]: frame for frame in result.get("frames", [])}
    base = f"/api/scans/{scan_id}"

    def appraisal(window: dict) -> dict:
        evidence = window.get("evidence")
        return {
            "iv": window["iv"],
            "stable_seconds": window["stable_seconds"],
            "start_seconds": window["start_seconds"],
            "evidence": f"{base}/evidence/{int(evidence[3:5])}" if evidence else None,
        }

    for individual in result["individuals"]:
        frames = individual["frames"]
        detail = detail_frame(individual, readings)
        summary["individuals"].append({
            "frames": [frames[0], frames[-1]],
            "seconds": [individual["start_seconds"], individual["end_seconds"]],
            "detail_frame": f"{base}/frames/{detail}",
            "species": individual["species"],
            "cp": individual["cp"],
            "max_hp": individual["max_hp"],
            "lucky": individual.get("lucky"),
            "shadow": individual.get("shadow"),
            "background": individual.get("background"),
            "max_state": individual.get("max_state"),
            "max_moves": individual.get("max_moves") or [],
            "state": individual.get("state"),
            "appraisals": [appraisal(w) for w in individual.get("appraisals", [])],
            "level": individual.get("level"),
            "moves": individual.get("moves"),
            "code": individual.get("code"),
            "nickname": individual.get("nickname"),
            "resources": individual.get("resources"),
        })
    summary["unassigned"] = len(result.get("unassigned_appraisals", []))
    summary["recognizer"] = result.get("recognizer")
    return summary

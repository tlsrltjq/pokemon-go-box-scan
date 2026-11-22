"""Disk use of scans and reference data, and cleanup the user asks for (nothing is removed automatically).

- Trim a scan: remove the source video and the sampled frames, but keep analysis.json, the IV
  evidence images and each individual's detail frame, so the candidates and the box history still
  show their pictures. A trimmed scan cannot be analyzed again.
- Delete a scan: remove its folder and its row. Box entries keep their values; only the history
  link to that scan stops working.
- Prune reference data: keep the current version and the one before it.

A scan that the worker or a live capture is still using cannot be trimmed or deleted.
"""
import json
import shutil
from pathlib import Path

from . import reference
from .analysis import RESULT, detail_frame
from .store import JobStore

BUSY = {"queued", "running"}
TRIMMED = ".trimmed"
KEEP_REFERENCE = 2


class CleanupError(Exception):
    """The request cannot go ahead; the message is shown to the user."""


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _busy(job: dict) -> bool:
    return job["status"] in BUSY or job.get("analysis_status") in (BUSY | {"live"})


def scans(store: JobStore, saved: dict[str, int]) -> list[dict]:
    """Every scan, newest first, with its size and how many candidates were saved to the box."""
    rows = []
    for job in store.all():
        folder = store.root / job["id"]
        if not folder.is_dir():
            continue
        frames = store.frames(job["id"])
        rows.append({
            "id": job["id"],
            "filename": job["filename"],
            "created_at": _created(folder),
            "bytes": _size(folder),
            "frames": len(frames),
            "video": (folder / "source").is_file(),
            "live": (folder / "live.json").is_file(),
            "status": job["status"],
            "analysis_status": job.get("analysis_status"),
            "individuals": _individuals(folder),
            "saved": saved.get(job["id"], 0),
            "busy": _busy(job),
            "trimmed": (folder / TRIMMED).is_file(),
        })
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


def _created(folder: Path) -> float:
    stat = folder.stat()
    return getattr(stat, "st_birthtime", stat.st_mtime)


def _individuals(folder: Path) -> int | None:
    path = folder / RESULT
    if not path.is_file():
        return None
    try:
        return len(json.loads(path.read_text(encoding="utf-8")).get("individuals", []))
    except (OSError, ValueError):
        return None


def _job(store: JobStore, scan_id: str) -> dict:
    job = store.get(scan_id)
    if job is None or not (store.root / scan_id).is_dir():
        raise CleanupError("스캔을 찾을 수 없습니다.")
    if _busy(job):
        raise CleanupError("처리 중이거나 캡처 중인 스캔은 정리할 수 없습니다. 끝난 뒤 다시 시도하세요.")
    return job


def trim(store: JobStore, scan_id: str) -> int:
    """Remove the video and frames except the ones the candidates show. Returns the bytes freed."""
    _job(store, scan_id)
    folder = store.root / scan_id
    keep: set[str] = set()
    path = folder / RESULT
    if path.is_file():
        result = json.loads(path.read_text(encoding="utf-8"))
        readings = {frame["frame"]: frame for frame in result.get("frames", [])}
        keep = {f"frame-{detail_frame(individual, readings):03d}.jpg" for individual in result["individuals"]}
    freed = 0
    for item in [folder / "source", *store.frames(scan_id), *folder.glob("*.partial")]:
        if item.is_file() and item.name not in keep:
            freed += item.stat().st_size
            item.unlink()
    (folder / TRIMMED).write_text("영상과 프레임을 비웠습니다.\n", encoding="utf-8")
    return freed


def delete(store: JobStore, scan_id: str) -> int:
    """Remove the whole scan. Returns the bytes freed."""
    _job(store, scan_id)
    folder = store.root / scan_id
    freed = _size(folder)
    shutil.rmtree(folder)
    store.delete(scan_id)
    return freed


def reference_versions(data_dir: Path) -> list[dict]:
    """Downloaded reference versions, newest first; `keep` marks what pruning would leave."""
    root = reference.root(data_dir)
    pointer = root / "current"
    current = pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else None
    versions = []
    for folder in root.iterdir() if root.is_dir() else []:
        if not folder.is_dir():
            continue
        fetched = ""
        try:
            fetched = json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("fetched_at", "")
        except (OSError, ValueError):
            pass
        versions.append({"version": folder.name, "fetched_at": fetched, "bytes": _size(folder),
                         "current": folder.name == current})
    versions.sort(key=lambda v: (v["current"], v["fetched_at"]), reverse=True)
    for index, version in enumerate(versions):
        version["keep"] = version["current"] or index < KEEP_REFERENCE
    return versions


def prune_reference(data_dir: Path) -> int:
    """Remove reference versions other than the current one and the newest older one."""
    freed = 0
    for version in reference_versions(data_dir):
        if not version["keep"]:
            freed += version["bytes"]
            shutil.rmtree(reference.root(data_dir) / version["version"])
    return freed

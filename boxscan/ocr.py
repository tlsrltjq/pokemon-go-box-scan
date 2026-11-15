"""Apple Vision OCR adapter. It runs only on the local Mac and makes no network calls."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).with_name("vision_ocr.swift")
TOOL_DIR = Path(__file__).resolve().parents[1] / ".tooling"
RECOGNIZER = "apple-vision-accurate-ko-en"


def swiftc() -> str | None:
    if sys.platform != "darwin":
        return None
    return shutil.which(os.environ.get("BOXSCAN_SWIFTC", "swiftc"))


def recognizer_version() -> str:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]
    return f"{RECOGNIZER}/{digest}"


def build_tool(tool_dir: Path = TOOL_DIR) -> Path:
    """Compile the Swift helper once per source revision and reuse the binary."""
    compiler = swiftc()
    if compiler is None:
        raise RuntimeError("Apple Vision OCR은 Xcode 명령줄 도구가 설치된 Mac에서만 실행할 수 있습니다.")
    binary = tool_dir / f"vision_ocr-{recognizer_version().rsplit('/', 1)[1]}"
    if binary.is_file():
        return binary
    tool_dir.mkdir(parents=True, exist_ok=True)
    partial = binary.with_suffix(".partial")
    subprocess.run(
        [compiler, "-O", str(SOURCE), "-o", str(partial)],
        check=True, capture_output=True, timeout=300,
    )
    partial.replace(binary)
    return binary


def recognize(images: list[Path], tool: Path | None = None) -> dict[Path, dict]:
    """Return OCR lines keyed by image path, in the order Vision reports them top to bottom."""
    if not images:
        return {}
    tool = tool or build_tool()
    completed = subprocess.run(
        [str(tool), *map(str, images)],
        check=True, capture_output=True, text=True, timeout=60 + 5 * len(images),
    )
    results = {}
    for line in completed.stdout.splitlines():
        item = json.loads(line)
        results[Path(item["path"])] = item
    return results

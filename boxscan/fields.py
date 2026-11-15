"""Turn OCR lines into field candidates and group frames by individual.

Every value stays a candidate with its source frames. Nothing here is an approved box entry.
"""
import re
from collections import Counter

CP = re.compile(r"[cC][pP]\s*(\d{3,4})\b")
HP = re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})\s*[Hh][Pp]")
# Appraisal caption: "이 마기라스(을)를 잡은 날과 장소는 ..." names the species, not the nickname.
SPECIES = re.compile(r"이\s+(\S.*?)\s*\((?:을|를)\)(?:를|을)?\s*잡은")
APPRAISAL_LABELS = {"공격", "방어", "방에", "HP", "Hp"}


def parse_frame(lines: list[dict]) -> dict:
    texts = [line["text"].strip() for line in lines]
    found: dict = {"screen": "UNKNOWN"}
    for text in texts:
        if "cp" not in found and (match := CP.search(text)):
            found["cp"] = int(match[1])
        if "hp" not in found and (match := HP.search(text)):
            current, maximum = int(match[1]), int(match[2])
            if 0 < current <= maximum:
                found["hp"] = {"current": current, "max": maximum}
        if "species" not in found and (match := SPECIES.search(text)):
            found["species"] = match[1]
    labels = APPRAISAL_LABELS.intersection(texts)
    if "공격" in labels and len(labels) >= 2 or "species" in found:
        found["screen"] = "APPRAISAL"
    elif "cp" in found or "hp" in found:
        found["screen"] = "DETAIL"
    return found


def vote(values: list) -> dict | None:
    if not values:
        return None
    (value, count), = Counter(values).most_common(1)
    return {"value": value, "votes": count, "observations": len(values)}


def group_frames(frames: list[dict]) -> list[dict]:
    """Split a time-ordered frame list where a confident value changes.

    A new group starts when a frame shows a CP, max HP or species that conflicts with the group
    so far and the next frame with such a value conflicts too. A single unmatched reading (for
    example a cropped CP) stays in the group. Users must confirm the boundaries.
    """
    groups: list[list[dict]] = []
    for index, frame in enumerate(frames):
        current = groups[-1] if groups else None
        if current is None:
            groups.append([frame])
        elif _conflicts(current, frame) and _confirmed(current, frames[index + 1:]):
            groups.append([frame])
        else:
            current.append(frame)
    return [_summarize(group) for group in groups]


def _confirmed(group: list[dict], following: list[dict]) -> bool:
    for frame in following:
        if any(_value(frame, key) is not None for key in ("cp", "max_hp", "species")):
            return _conflicts(group, frame)
    return False


def _conflicts(group: list[dict], frame: dict) -> bool:
    for key in ("cp", "max_hp"):
        value = _value(frame, key)
        seen = vote([v for f in group if (v := _value(f, key)) is not None])
        if value is not None and seen and seen["votes"] >= 2 and value != seen["value"]:
            return True
    species = frame.get("species")
    seen_species = {f["species"] for f in group if "species" in f}
    return bool(species and seen_species and species not in seen_species)


def _value(frame: dict, key: str):
    if key == "max_hp":
        return frame.get("hp", {}).get("max")
    return frame.get(key)


def _summarize(group: list[dict]) -> dict:
    return {
        "frames": [frame["frame"] for frame in group],
        "start_seconds": group[0]["seconds"],
        "end_seconds": group[-1]["seconds"] + group[-1].get("duration", 1),
        "cp": vote([f["cp"] for f in group if "cp" in f]),
        "max_hp": vote([f["hp"]["max"] for f in group if "hp" in f]),
        "species": vote([f["species"] for f in group if "species" in f]),
    }

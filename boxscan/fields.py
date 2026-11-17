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
# "반짝반짝 포켓몬" under the name marks a lucky Pokémon; OCR sometimes garbles the last word.
LUCKY = re.compile(r"반짝반짝\s*포")
# Shadow Pokémon have a "정화" (purify) button on the detail screen. Not yet checked against a
# real shadow recording, so it only prefills the checkbox.
PURIFY_BUTTON = re.compile(r"^정화$")
# Detail screen labels under the type row, and the special background note on the moves screen.
DYNAMAX = re.compile(r"^다이맥스$")
GIGANTAMAX = re.compile(r"^거다이맥")
BACKGROUND = re.compile(r"배경은")
MEGA_TIMER = re.compile(r"남은\s*시간")
# 맥스기술: names start with 다이 or 거다이; the row above shows "레벨 N" or LOCKED per move.
MAX_HEADER = re.compile(r"맥스기술")
MAX_MOVE = re.compile(r"^(?:거다이|다이)[가-힣]{1,8}$")
MAX_LEVEL = re.compile(r"레벨\s*(\d)")
# Move rows: a Korean name on the left and its power on the same row at the right, such as
# "지진 … 140+28". The second number is a bonus: "기술 날씨 보너스" or "메가진화 보너스" on the
# line below says which. Rows that also show a cost with a comma ("해방 … 100,000") or that are
# buttons are not moves.
POWER = re.compile(r"^(\d{1,3})(?:\s*\+\s*(\d{1,3}))?$")
NAME_EDGES = re.compile(r"^[^가-힣]+|[^가-힣]+$")
MOVE_NAME = re.compile(r"^[가-힣]{2,12}$")
COST = re.compile(r"\d{1,3}(?:,\d{3})+")
BUTTONS = {"해방", "해빙", "해벙", "애방", "강화", "강차", "감화", "교환", "교한", "교체", "합체"}
# "체육관 배틀 & 레이드배틀" tab header, also when OCR garbles it.
MOVE_HEADER = re.compile(r"체육관|채육관|제육|레이드|레미드")


def parse_moves(lines: list[dict], width: int | None) -> list[dict]:
    """Return move rows top to bottom with role fast, charged or unknown (header not visible)."""
    if not lines:
        return []
    width = width or max(line["box"][0] + line["box"][2] for line in lines)

    def centre(line):
        return line["box"][1] + line["box"][3] / 2

    header = next((centre(line) for line in lines if MOVE_HEADER.search(line["text"])), None)
    rows = []
    for line in lines:
        x, _, w, h = line["box"]
        name = NAME_EDGES.sub("", line["text"].strip())
        if x > 0.3 * width or not MOVE_NAME.match(name) or name in BUTTONS:
            continue
        if header is not None and centre(line) < header:
            continue
        same_row = [other for other in lines if other is not line
                    and abs(centre(other) - centre(line)) <= max(8, h * 0.6)]
        if any(COST.search(other["text"]) for other in same_row):
            continue
        powers = [match for other in same_row
                  if other["box"][0] > 0.7 * width and (match := POWER.match(other["text"].strip()))]
        if len(powers) != 1:
            continue
        boost_kind = None
        if powers[0][2]:
            below = [other["text"] for other in lines
                     if 0 < centre(other) - centre(line) <= max(30, h * 2.2)]
            boost_kind = ("mega" if any("메가" in text for text in below)
                          else "weather" if any("날씨" in text for text in below) else None)
        # "마지막일침+" with "메가진화 보너스" below: an additional charged attack that only a mega
        # evolved Pokémon has. It is not one of the regular move slots.
        plus = bool(re.search(r"[가-힣]\s*\+", line["text"])) or boost_kind == "mega"
        rows.append({"name": name, "power": int(powers[0][1]), "mega_plus": plus,
                     "boost": int(powers[0][2]) if powers[0][2] else None, "boost_kind": boost_kind,
                     "y": centre(line), "height": h})
    rows.sort(key=lambda row: row["y"])
    for index, row in enumerate(rows):
        # The fast move sits right under the tab header; a first row further down means the
        # fast move was not read in this frame.
        if row.pop("mega_plus"):
            row["role"] = "mega_plus"
        elif header is None:
            row["role"] = "unknown"
        elif index == 0 and row["y"] - header <= 3 * row["height"]:
            row["role"] = "fast"
        else:
            row["role"] = "charged"
        del row["y"], row["height"]
    return rows


def parse_max_moves(lines: list[dict]) -> list[dict]:
    """맥스기술 names paired with the 레벨 N / LOCKED status shown above each, by x position."""
    header = next((line for line in lines if MAX_HEADER.search(line["text"])), None)
    if header is None:
        return []
    top = header["box"][1]

    def x_centre(line):
        return line["box"][0] + line["box"][2] / 2

    below = [line for line in lines if line["box"][1] > top]
    names = [line for line in below if MAX_MOVE.match(NAME_EDGES.sub("", line["text"].strip()))
             and not GIGANTAMAX.match(line["text"].strip()) and line["text"].strip() != "다이맥스"]
    statuses = [line for line in below if MAX_LEVEL.search(line["text"]) or "LOCK" in line["text"].upper()]
    moves = []
    for line in sorted(names, key=x_centre):
        near = min(statuses, key=lambda s: abs(x_centre(s) - x_centre(line)), default=None)
        if near is not None and abs(x_centre(near) - x_centre(line)) > line["box"][2]:
            near = None
        level = MAX_LEVEL.search(near["text"]) if near else None
        moves.append({"name": NAME_EDGES.sub("", line["text"].strip()),
                      "level": int(level[1]) if level else None,
                      "locked": bool(near) and "LOCK" in near["text"].upper()})
    return moves


def parse_frame(lines: list[dict], width: int | None = None) -> dict:
    texts = [line["text"].strip() for line in lines]
    found: dict = {"screen": "UNKNOWN"}
    if moves := parse_moves(lines, width):
        found["moves"] = moves
    if max_moves := parse_max_moves(lines):
        found["max_moves"] = max_moves
    for text in texts:
        if "cp" not in found and (match := CP.search(text)):
            found["cp"] = int(match[1])
        if "hp" not in found and (match := HP.search(text)):
            current, maximum = int(match[1]), int(match[2])
            if 0 < current <= maximum:
                found["hp"] = {"current": current, "max": maximum}
        if "species" not in found and (match := SPECIES.search(text)):
            found["species"] = match[1]
        if LUCKY.search(text):
            found["lucky"] = True
        if PURIFY_BUTTON.match(text):
            found["purify_button"] = True
        if GIGANTAMAX.match(text):
            found["max_state"] = "gigantamax"
        elif DYNAMAX.match(text) and found.get("max_state") != "gigantamax":
            found["max_state"] = "dynamax"
        if BACKGROUND.search(text):
            found["background"] = True
        if MEGA_TIMER.search(text):
            found["mega_timer"] = True
    labels = APPRAISAL_LABELS.intersection(texts)
    if "공격" in labels and len(labels) >= 2 or "species" in found:
        found["screen"] = "APPRAISAL"
    elif "cp" in found or "hp" in found:
        found["screen"] = "DETAIL"
    elif "moves" in found:
        found["screen"] = "DETAIL_MOVES"
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
        "lucky": _lucky(group),
        "purify_button": _seen(group, "purify_button"),
        "moves": _moves(group),
        "max_state": _max_state(group),
        "max_moves": _max_moves(group),
        "background": _seen(group, "background"),
        "mega_timer": _seen(group, "mega_timer"),
    }


def _seen(group: list[dict], key: str) -> dict:
    """A screen label: True once seen; otherwise unknown (absence is not proof)."""
    votes = sum(1 for frame in group if frame.get(key))
    return {"value": True if votes else None, "votes": votes}


def _max_state(group: list[dict]) -> dict:
    states = [frame["max_state"] for frame in group if frame.get("max_state")]
    value = "gigantamax" if "gigantamax" in states else "dynamax" if states else None
    return {"value": value, "votes": len(states)}


def _max_moves(group: list[dict]) -> list[dict]:
    """Merge 맥스기술 across frames, keeping a read level over an unread one."""
    merged: dict[str, dict] = {}
    for frame in group:
        for move in frame.get("max_moves", []):
            known = merged.setdefault(move["name"], dict(move))
            if known["level"] is None and move["level"] is not None:
                known["level"], known["locked"] = move["level"], False
            known["locked"] = known["locked"] and move["locked"] if known["level"] is None else False
    return list(merged.values())


def _lucky(group: list[dict]) -> dict:
    """True when the label is seen; False only when enough detail frames lack it."""
    detail = sum(1 for frame in group if frame.get("screen") == "DETAIL" or frame.get("lucky"))
    votes = sum(1 for frame in group if frame.get("lucky"))
    if votes >= 2 or (votes == 1 and detail <= 2):
        value = True
    elif votes == 0 and detail >= 3:
        value = False
    else:
        value = None
    return {"value": value, "votes": votes, "observations": detail}


def _moves(group: list[dict]) -> dict:
    """Vote the fast move and up to two charged moves over the group's move rows."""
    rows = [row for frame in group for row in frame.get("moves", [])]
    fast = vote([row["name"] for row in rows if row["role"] == "fast"])
    fast_name = fast["value"] if fast else None
    frames_with_moves = sum(1 for frame in group if frame.get("moves"))
    counts = Counter()
    together = set()  # charged names seen in the same frame as another charged name
    for frame in group:
        names = {row["name"] for row in frame.get("moves", [])
                 if row["role"] == "charged" or row["role"] == "unknown" and row["name"] != fast_name}
        names.discard(fast_name)
        counts.update(names)
        if len(names) == 2:
            together.update(names)
    ranked = counts.most_common()
    top = ranked[0][1] if ranked else 0
    # The second charged move sits lower and is often scrolled out, so it may be read only once.
    # Keep it when it was frequent enough or appeared on screen together with the first.
    charged = [
        {"value": name, "votes": count, "observations": frames_with_moves}
        for name, count in ranked if count * 2 >= top or name in together
    ][:2]

    def power(name):
        found = [row for row in rows if row["name"] == name]
        return vote([(row["power"], row["boost"], row["boost_kind"]) for row in found])["value"]

    mega_plus = vote([row["name"] for row in rows if row["role"] == "mega_plus"])
    for item in ([fast] if fast else []) + charged + ([mega_plus] if mega_plus else []):
        item["power"], item["boost"], item["boost_kind"] = power(item["value"])
    return {"fast": fast, "charged": charged, "mega_plus": mega_plus, "source": "ocr"}

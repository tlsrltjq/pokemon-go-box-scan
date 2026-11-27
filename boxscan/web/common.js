// Helpers shared by the scan page and the box page.
const IV_KEYS = [["attack", "공격"], ["defense", "방어"], ["stamina", "HP"]];
// Marks after the species name: 이로치 *, 반짝반짝 포켓몬 ♣, 배경 ◆ (in this order).
const NAME_MARKS = [["shiny", "*", "이로치"], ["lucky", "♣", "반짝반짝 포켓몬"], ["background", "◆", "배경"]];

function nameMarks(values) {
  return NAME_MARKS.filter(([key]) => values?.[key] === true).map(([, mark]) => mark).join("");
}

function displayName(values) {
  const form = values?.form_name ? ` (${values.form_name})` : "";
  const shadow = values?.shadow === true ? " (그림자)" : "";
  return `${values?.species ?? "종 미확인"}${nameMarks(values)}${form}${shadow}`;
}

// 제크로무와 / …과: choose 와 or 과 by the last syllable's final consonant.
function withParticle(name) {
  const code = name.charCodeAt(name.length - 1) - 0xac00;
  return `${name}${code >= 0 && code < 11172 && code % 28 !== 0 ? "과" : "와"}`;
}

const MAX_NAMES = { dynamax: "다이맥스", gigantamax: "거다이맥스" };

function formatMaxState(value) {
  return MAX_NAMES[value] ?? "-";
}

function formatFlag(value) {
  return value === true ? "예" : value === false ? "아니요" : "-";
}

async function jsonResponse(response) {
  if (response.status === 204) return null;
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(typeof payload.detail === "string" ? payload.detail : "요청을 처리하지 못했습니다.");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "class") node.className = value;
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(name, value);
  }
  node.append(...children.filter(child => child !== null && child !== undefined));
  return node;
}

function field(label, name, value, attributes = {}) {
  return element("label", { class: "field" }, element("span", {}, label),
    element("input", { name, value: value ?? "", autocomplete: "off", ...attributes }));
}

function checkbox(label, name, checked) {
  return element("label", { class: "check" },
    element("input", { type: "checkbox", name, checked: checked ? "" : null }), element("span", {}, label));
}

function maxStateSelect(value) {
  return element("label", { class: "field wide" }, element("span", {}, "다이맥스"),
    element("select", { name: "max_state" },
      element("option", { value: "" }, "해당 없음"),
      element("option", { value: "dynamax", selected: value === "dynamax" ? "" : null }, "다이맥스"),
      element("option", { value: "gigantamax", selected: value === "gigantamax" ? "" : null }, "거다이맥스")));
}

// 맥스기술 editor: up to three moves, each a name and a level (1-3) or locked.
function maxMovesEditor(moves = []) {
  const rows = [0, 1, 2].map(i => {
    const move = moves[i] ?? {};
    const level = move.locked ? "locked" : move.level ? String(move.level) : "";
    return element("span", { class: "max-move" },
      element("input", { name: `max_name_${i}`, value: move.name ?? "", maxlength: "20", autocomplete: "off",
        placeholder: i === 0 ? "예: 다이웜" : "", "aria-label": `맥스기술 ${i + 1}` }),
      element("select", { name: `max_level_${i}`, "aria-label": `맥스기술 ${i + 1} 레벨` },
        ...[["", "레벨?"], ["1", "Lv.1"], ["2", "Lv.2"], ["3", "Lv.3"], ["locked", "잠김"]].map(([value, label]) =>
          element("option", { value, selected: value === level ? "" : null }, label))));
  });
  return element("span", { class: "max-edit" }, element("span", { class: "max-edit-label" }, "맥스기술"), ...rows);
}

function readMaxMoves(form) {
  return [0, 1, 2].map(i => {
    const name = form.elements[`max_name_${i}`].value.trim();
    const level = form.elements[`max_level_${i}`].value;
    return name ? { name, level: level && level !== "locked" ? Number(level) : null, locked: level === "locked" } : null;
  }).filter(Boolean);
}

// "애버라스 사탕 98 · XL 12 · 원시에너지 0"
function formatCandy(family, values) {
  if (!values) return "";
  const parts = [];
  if (values.candy != null) parts.push(`사탕 ${values.candy.toLocaleString()}`);
  if (values.xl != null) parts.push(`XL ${values.xl.toLocaleString()}${values.xl_hidden ? " (화면에 없어 0)" : ""}`);
  if (values.energy != null) parts.push(`${values.energy_kind ?? ""}에너지 ${values.energy.toLocaleString()}`);
  return parts.length ? `${family ? `${family} ` : ""}${parts.join(" · ")}` : "";
}

function formatBytes(bytes) {
  return bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

function formatIv(iv) {
  if (!iv) return "-";
  const total = iv.attack + iv.defense + iv.stamina;
  return `${iv.attack}/${iv.defense}/${iv.stamina} (${Math.round((total / 45) * 100)}%)`;
}

function formatLevel(level) {
  if (!level) return "-";
  if (level.value != null) return String(level.value);
  if (level.candidates?.length) return `${level.candidates.join(" 또는 ")} (후보)`;
  return level.status === "CONFLICT" ? "맞는 레벨 없음" : "-";
}

function formatMove(move) {
  return move ? `${move.name}${move.marker ?? ""}` : "-";
}

function formatMoves(moves) {
  if (!moves) return "-";
  const charged = (moves.charged ?? []).map(formatMove).join(", ");
  return `${formatMove(moves.fast)} / ${charged || "-"}`;
}

const BOX_FIELDS = [
  ["species", "종", value => value ?? "-"],
  ["nickname", "게임 별명", value => value ?? "-"],
  ["shiny", "이로치 *", formatFlag],
  ["lucky", "반짝반짝 ♣", formatFlag],
  ["background", "배경 ◆", formatFlag],
  ["shadow", "그림자", formatFlag],
  ["max_state", "다이맥스", formatMaxState],
  ["cp", "CP", value => value ?? "-"],
  ["max_hp", "최대 HP", value => value ?? "-"],
  ["iv", "IV", formatIv],
  ["level", "레벨", formatLevel],
  ["moves", "기술", formatMoves],
];

function comparisonTable(before, after) {
  const rows = BOX_FIELDS.map(([key, label, format]) => {
    const old = format(before?.[key]);
    const next = format(after?.[key]);
    return element("tr", { class: old !== next ? "changed" : null },
      element("th", { scope: "row" }, label), element("td", {}, old), element("td", {}, next));
  });
  return element("table", { class: "compare" },
    element("thead", {}, element("tr", {}, element("th", {}, ""), element("th", { scope: "col" }, "기존"),
      element("th", { scope: "col" }, "새 값"))),
    element("tbody", {}, ...rows));
}

// 메가독침붕을 / 메가리자몽Y를: 을 or 를 for the name's last syllable (a final consonant or not).
function objectParticle(name) {
  const code = name.charCodeAt(name.length - 1) - 0xac00;
  const consonant = code >= 0 && code < 11172 ? code % 28 !== 0 : /[LMNR0136789]$/i.test(name);
  return consonant ? "을" : "를";
}

const MEGA_MODE_NAMES = { off: "메가 안 씀", evolved: "메가진화해 본 개체만", all: "메가진화되는 개체 모두" };

// "메가물몬(03 · 메가 Lv.2)을", "메가물몬(메가진화해 봄)을", "메가물몬(아직 메가진화 안 함)을"
function megaWho(info) {
  const status = !info.evolved ? "아직 메가진화 안 함" : info.level ? `메가 Lv.${info.level}` : "메가진화해 봄";
  return `${info.member}(${info.code ? `${info.code} · ` : ""}${status})${objectParticle(info.member)}`;
}

// How much better outcome a is than b: more boss movesets cleared, else a faster clear, else more damage.
function megaGain(a, b) {
  if (!a || !b) return null;
  if (a.defeated_scenarios !== b.defeated_scenarios) {
    return { clears: a.defeated_scenarios - b.defeated_scenarios, clear_s: a.clear_s };
  }
  if (a.clear_s != null && b.clear_s != null) return { seconds: b.clear_s - a.clear_s };
  return { damage: a.damage - b.damage };
}

function megaBetter(gain) {
  if (!gain) return false;
  if (gain.clears) return gain.clears > 0;
  return gain.seconds != null ? gain.seconds > 0.5 : gain.damage >= 1;
}

function megaGainText(gain) {
  if (gain.clears) {
    return `처치하는 보스 기술 조합이 ${gain.clears}가지 늘어납니다` + (gain.clear_s != null ? ` (평균 ${gain.clear_s.toFixed(1)}초에 처치)` : "");
  }
  if (gain.seconds != null) return `평균 ${gain.seconds.toFixed(1)}초 빨리 처치합니다`;
  return `평균 ${Math.round(gain.damage).toLocaleString()} 더 줍니다`;
}

// Lines for a raid result's mega_comparison: what the mega-evolved (or not yet evolved) Pokémon would change.
function megaLines(comparison) {
  if (!comparison) return [];
  const { off, evolved, all } = comparison.options;
  const note = text => element("p", { class: "mega-note" }, text);
  const muted = text => element("p", { class: "muted" }, text);
  if (!comparison.candidates.all) return [muted("박스에 메가진화·원시회귀할 수 있는 개체가 없습니다.")];
  const lines = [];
  const chosen = comparison.options[comparison.mode];
  if (comparison.mode === "off") {
    const gain = evolved?.member ? megaGain(evolved, off) : null;
    if (megaBetter(gain)) lines.push(note(`${megaWho(evolved)} 넣으면 ${megaGainText(gain)}.`));
    else if (comparison.candidates.evolved) lines.push(muted("메가진화해 본 개체를 넣어도 처치 결과가 거의 같습니다."));
  } else if (chosen?.member) {
    const gain = megaGain(chosen, off);
    lines.push(megaBetter(gain) ? note(`${megaWho(chosen)} 넣어서 메가 없이 할 때보다 ${megaGainText(gain)}.`)
      : muted(`${megaWho(chosen)} 넣어도 메가 없이 할 때와 거의 같습니다.`));
  } else {
    lines.push(muted(comparison.mode === "evolved" && !comparison.candidates.evolved
      ? "메가진화해 본 개체가 없어 메가 없이 계산했습니다. 내 박스의 수정에서 메가진화해 본 개체를 표시할 수 있습니다."
      : "메가진화 개체를 넣어도 나아지지 않아 메가 없이 추천합니다."));
  }
  if (comparison.mode !== "all" && all?.member && !all.evolved) {
    const base = evolved?.member ? evolved : off;
    const gain = megaGain(all, base);
    if (megaBetter(gain)) {
      lines.push(note(`${megaWho(all)} 메가진화해서 넣으면 ` +
        `${evolved?.member ? "메가진화해 본 개체를 쓸 때보다 " : ""}${megaGainText(gain)}.`));
    }
  }
  const shown = chosen?.member ? chosen : null;
  if (shown?.plus_move && !shown.plus_confirmed) {
    lines.push(muted(`${shown.plus_move}${shown.plus_move.endsWith("+") ? "는" : objectParticle(shown.plus_move) === "을" ? "은" : "는"} ` +
      "메가 상태로 스캔해 확인하지 않아, 게임 데이터대로 쓸 수 있다고 가정했습니다."));
  }
  return lines;
}

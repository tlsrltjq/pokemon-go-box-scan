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
  if (values.xl != null) parts.push(`XL ${values.xl.toLocaleString()}`);
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

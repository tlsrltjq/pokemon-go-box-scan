// Helpers shared by the scan page and the box page.
const IV_KEYS = [["attack", "공격"], ["defense", "방어"], ["stamina", "HP"]];

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

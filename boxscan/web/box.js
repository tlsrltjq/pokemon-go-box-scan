const list = document.querySelector("#pokemon");
const status = document.querySelector("#status");
const search = document.querySelector("#search");
const sort = document.querySelector("#sort");
const CHANGES = { created: "새로 저장", updated: "갱신", deleted: "박스에서 제거" };
let entries = [];
let candies = {};

function moveLine(label, move) {
  if (!move) return null;
  return element("div", { class: "move" },
    element("span", { class: "move-role" }, label),
    element("strong", {}, move.name, move.marker ? element("span", { class: "marker", title: move.reason }, move.marker) : null),
    element("span", {}, ""),
    move.marker ? element("span", { class: "move-reason neutral" }, `* ${move.reason}`) : null,
  );
}

function revisionItem(revision) {
  const source = revision.source;
  const manual = source?.kind === "manual_edit";
  const codeChange = source?.kind === "code_change";
  const origin = manual ? (source.note ? ` · 메모: ${source.note}` : "")
    : source?.filename ? ` · ${source.filename} 프레임 ${source.frames[0]}–${source.frames.at(-1)}` : "";
  const label = codeChange ? `관리 번호 ${source.from ?? "-"} → ${source.to}${source.swapped ? " (맞바꿈)" : ""}`
    : manual ? "직접 수정" : CHANGES[revision.change];
  const item = element("li", {},
    element("p", {}, element("strong", {}, `${revision.revision}번째 기록 · ${label}`),
      ` · ${new Date(revision.created_at).toLocaleString("ko-KR")}${origin}`));
  if (revision.change !== "deleted" && !codeChange) item.append(comparisonTable(revision.before, revision.after));
  return item;
}

// Edit form for a saved entry; with no entry it adds a Pokémon typed in by hand.
function editForm(entry, onDone) {
  const iv = entry?.iv ?? {};
  const moves = entry?.moves ?? {};
  const charged = moves.charged ?? [];
  const numeric = { inputmode: "numeric" };
  const message = element("div", { class: "save-message", role: "status" });
  const submit = element("button", { type: "submit" }, entry ? "수정 저장" : "박스에 추가");
  const cancel = element("button", { type: "button", class: "secondary" }, "취소");
  const form = element("form", { class: "save-form edit-form" },
    element("div", { class: "fields" },
      field("종", "species", entry?.species, { maxlength: "30" }),
      field("CP", "cp", entry?.cp, numeric),
      field("최대 HP", "max_hp", entry?.max_hp, numeric),
      field("IV 공격", "iv_attack", iv.attack, numeric),
      field("IV 방어", "iv_defense", iv.defense, numeric),
      field("IV HP", "iv_stamina", iv.stamina, numeric),
      field("노말어택", "fast_move", moves.fast?.name, { maxlength: "30" }),
      field("스페셜어택 1", "charged_1", charged[0]?.name, { maxlength: "30" }),
      field("스페셜어택 2", "charged_2", charged[1]?.name, { maxlength: "30" }),
      field("게임 별명", "nickname", entry?.nickname, { maxlength: "30" })),
    element("div", { class: "checks" },
      checkbox("이로치 *", "shiny", entry?.shiny === true),
      checkbox("반짝반짝 포켓몬 ♣", "lucky", entry?.lucky === true),
      checkbox("배경 ◆", "background", entry?.background === true),
      checkbox("그림자", "shadow", entry?.shadow === true)),
    maxStateSelect(entry?.max_state ?? ""),
    element("label", { class: "field wide" }, element("span", {}, "메가 레벨 (메가진화 화면에서 확인)"),
      element("select", { name: "mega_level" }, ...[["", "모름"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"]].map(([value, label]) =>
        element("option", { value, selected: String(entry?.mega_level ?? "") === value ? "" : null }, label)))),
    element("div", { class: "field wide" }, maxMovesEditor(entry?.max_moves ?? [])),
    field(entry ? "수정 메모 (선택)" : "메모 (선택)", "note", "", { maxlength: "200" }),
    element("p", { class: "muted" }, entry ? "비운 칸은 값을 지웁니다. 레벨과 기술 * 표시는 저장할 때 다시 계산합니다. 이전 값은 이력에 남습니다."
      : "종은 꼭 입력하세요. 레벨과 기술 * 표시는 저장할 때 종·CP·최대 HP·IV로 계산합니다. 관리 번호는 그 종의 다음 번호가 붙습니다."),
    message,
    element("div", { class: "actions" }, submit, cancel));
  cancel.addEventListener("click", () => onDone(false));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const get = name => form.elements[name].value.trim();
    const ivValues = ["attack", "defense", "stamina"].map(key => get(`iv_${key}`));
    submit.disabled = true;
    message.replaceChildren();
    try {
      const added = await jsonResponse(await fetch(entry ? `/api/pokemon/${entry.id}` : "/api/pokemon", {
        method: entry ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: entry?.revision,
          note: get("note") || null,
          values: {
            species: get("species") || null,
            cp: get("cp") || null,
            max_hp: get("max_hp") || null,
            iv: ivValues.every(v => v === "") ? null : { attack: ivValues[0], defense: ivValues[1], stamina: ivValues[2] },
            fast_move: get("fast_move") || null,
            charged_moves: [get("charged_1"), get("charged_2")].filter(Boolean),
            shiny: form.elements.shiny.checked,
            lucky: form.elements.lucky.checked,
            background: form.elements.background.checked,
            shadow: form.elements.shadow.checked,
            max_state: form.elements.max_state.value || null,
            max_moves: readMaxMoves(form),
            mega_level: form.elements.mega_level.value || null,
            nickname: get("nickname") || null,
          },
        }),
      }));
      onDone(true, added);
    } catch (error) {
      message.textContent = error.status === 409 ? `${error.message} 목록을 새로 불러옵니다.` : error.message;
      if (error.status === 409) setTimeout(() => load(), 1500);
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

// The box as a table: one row per Pokémon, sortable by column. Clicking a row opens its details
// (edit form, history, notes) in a row right below.
const COLUMNS = [
  ["species", "종·번호"], ["cp", "CP"], ["iv", "IV"], ["level", "레벨"], ["hp", "최대 HP"],
  ["fast", "노말어택"], ["charged", "스페셜어택"], ["candy", "사탕 / XL"], ["state", "상태"], ["nickname", "별명"], ["recent", "갱신"], [null, ""],
];
const opened = new Set();

function ivPill(iv) {
  if (!iv) return element("span", { class: "muted" }, "-");
  const percent = ivPercent(iv);
  return element("span", {}, `${iv.attack}/${iv.defense}/${iv.stamina}`,
    element("span", { class: `iv-pill${percent === 100 ? " top" : percent >= 90 ? " high" : ""}` }, `${percent}%`));
}

function stateChips(entry) {
  const chips = [];
  if (entry.max_state) chips.push([formatMaxState(entry.max_state), "max"]);
  if (entry.shadow) chips.push(["그림자", "shadow"]);
  if (entry.fusion?.length) chips.push([entry.fusion.some(f => f.type === "FUSE") ? "합체" : "모습 변화", "plain"]);
  return chips.length ? element("span", { class: "badges" }, ...chips.map(([text, kind]) => element("span", { class: `badge state ${kind}` }, text)))
    : element("span", { class: "muted" }, "");
}

function moveName(move) {
  return move ? element("span", { title: move.reason ?? "" }, move.name, move.marker ? element("span", { class: "marker" }, move.marker) : null)
    : element("span", { class: "muted" }, "-");
}

function detailRow(entry) {
  const history = element("ol", { class: "history", hidden: "" });
  const toggle = element("button", { type: "button", class: "secondary" }, "이력 보기");
  const remove = element("button", { type: "button", class: "danger" }, "박스에서 제거");
  const editButton = element("button", { type: "button" }, "수정");
  const editArea = element("div", {});
  editButton.addEventListener("click", () => {
    if (editArea.childElementCount) {
      editArea.replaceChildren();
      return;
    }
    editArea.replaceChildren(editForm(entry, async saved => {
      editArea.replaceChildren();
      if (saved) {
        location.hash = entry.id;
        await load();
      }
    }));
  });
  toggle.addEventListener("click", async () => {
    if (!history.hidden) {
      history.hidden = true;
      toggle.textContent = "이력 보기";
      return;
    }
    const detail = await jsonResponse(await fetch(`/api/pokemon/${entry.id}`));
    history.replaceChildren(...detail.revisions.map(revisionItem));
    history.hidden = false;
    toggle.textContent = "이력 닫기";
  });
  remove.addEventListener("click", async () => {
    const name = `${displayName(entry)} ${entry.code ?? ""}`.trim();
    if (!confirm(`${name}을(를) 박스에서 제거할까요? 이력과 관리 번호는 남고, 번호는 다시 쓰지 않습니다.`)) return;
    try {
      await jsonResponse(await fetch(`/api/pokemon/${entry.id}?revision=${entry.revision}`, { method: "DELETE" }));
      await load();
    } catch (error) {
      status.textContent = error.message;
    }
  });
  const moves = entry.moves ?? {};
  const temp = entry.temp_observation;
  const codeForm = codeChanger(entry);
  const notes = [
    hasCode(entry) || !entry.code ? null : element("p", {}, element("span", { class: "hint-chip" },
      `추천 별명 ${entry.species ?? ""}${entry.code}`), " 게임 별명을 이렇게 바꾸면 다음 스캔 때 자동으로 고릅니다."),
    element("div", { class: "moves" },
      moveLine("노말어택", moves.fast),
      ...(moves.charged ?? []).map((move, i, all) => moveLine(all.length > 1 ? `스페셜어택 ${i + 1}` : "스페셜어택", move))),
    entry.mega_move ? element("p", {}, element("span", { class: "muted" }, "메가진화 추가 "), entry.mega_move.name) : null,
    entry.max_moves?.length ? element("p", {}, element("span", { class: "muted" }, "맥스기술 "),
      entry.max_moves.map(m => `${m.name} ${m.level ? `레벨 ${m.level}` : m.locked ? "잠김" : ""}`.trim()).join(", ")) : null,
    entry.cp_source === "calculated" ? element("p", { class: "muted" }, "CP·최대 HP는 메가진화 중 기록과 레벨로 계산한 기본 모습 값입니다.") : null,
    temp ? element("p", { class: "muted" }, `${temp.temp_name} 중 기록: CP ${temp.cp ?? "-"}, 최대 HP ${temp.max_hp ?? "-"}`) : null,
    element("p", { class: "muted" }, `처음 저장 ${new Date(entry.created_at).toLocaleString("ko-KR")} · 마지막 갱신 ${new Date(entry.updated_at).toLocaleString("ko-KR")}`),
  ];
  return element("tr", { class: "detail" }, element("td", { colspan: String(COLUMNS.length) },
    element("div", { class: "detail-grid" }, ...notes.filter(Boolean)),
    element("div", { class: "actions" }, editButton, toggle, remove),
    codeForm, editArea, history));
}

// Change the management number within the species; a number in use can be swapped after a confirm.
function codeChanger(entry) {
  const number = element("input", { name: "code", value: entry.code ?? "", inputmode: "numeric", maxlength: "3",
    "aria-label": "새 관리 번호", class: "code-input" });
  const button = element("button", { type: "submit", class: "secondary" }, "번호 바꾸기");
  const message = element("span", { class: "muted", role: "status" });
  const form = element("form", { class: "code-form" },
    element("span", { class: "max-edit-label" }, `${entry.species ?? ""} 관리 번호`), number, button, message);
  const send = swap => fetch(`/api/pokemon/${entry.id}/code`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number: Number(number.value), expected_revision: entry.revision, swap }),
  }).then(jsonResponse);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const wanted = Number(number.value);
    if (!Number.isInteger(wanted) || wanted < 1 || wanted > 999) {
      message.textContent = "1~999 사이 숫자로 입력하세요.";
      return;
    }
    button.disabled = true;
    try {
      let saved;
      let other = null;
      try {
        saved = await send(false);
      } catch (error) {
        if (!error.payload?.holder) throw error;
        other = error.payload.holder;
        const who = `${other.species ?? ""} ${other.code}번(CP ${other.cp ?? "-"}${other.nickname ? `, 별명 ${other.nickname}` : ""}${other.deleted ? ", 제거된 개체" : ""})`;
        if (!confirm(`${who}이(가) 이미 쓰고 있습니다. 두 개체의 번호를 서로 바꿀까요?`)) {
          message.textContent = "바꾸지 않았습니다.";
          return;
        }
        saved = await send(true);
      }
      const species = entry.species ?? "";
      alert(`${species} ${entry.code ?? "-"} → ${saved.code}번으로 바꿨습니다.\n게임 별명도 ${species}${saved.code}(으)로 바꿔 주세요.` +
        (other ? `\n맞바꾼 개체(지금 ${entry.code}번)의 별명은 ${species}${entry.code}(으)로 바꿔 주세요.` : ""));
      location.hash = entry.id;
      await load();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return form;
}

function entryRows(entry) {
  const moves = entry.moves ?? {};
  const isOpen = opened.has(entry.id);
  const row = element("tr", { class: `entry${isOpen ? " open" : ""}`, id: entry.id, tabindex: "0", "aria-expanded": String(isOpen) },
    element("td", { class: "sticky-col" }, element("span", { class: "name" }, displayName(entry)), " ",
      element("span", { class: "code-chip" }, entry.code ? `${entry.code}` : "-")),
    element("td", { class: "num" }, String(entry.cp ?? "-")),
    element("td", { class: "num" }, ivPill(entry.iv)),
    element("td", { class: "num" }, formatLevel(entry.level)),
    element("td", { class: "num" }, String(entry.max_hp ?? "-")),
    element("td", {}, moveName(moves.fast)),
    element("td", {}, ...(moves.charged?.length ? moves.charged.flatMap((move, i) => [i ? ", " : null, moveName(move)]).filter(Boolean)
      : [element("span", { class: "muted" }, "-")])),
    element("td", { class: "num", title: entry.candy_family ? `${entry.candy_family}의 사탕` : "" }, candyCell(entry)),
    element("td", {}, stateChips(entry)),
    element("td", {}, entry.nickname ?? "", hasCode(entry) || !entry.code ? null
      : element("span", { class: "muted", title: "추천 별명" }, ` → ${entry.species ?? ""}${entry.code}`)),
    element("td", { class: "muted" }, new Date(entry.updated_at).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })),
    element("td", {}, element("span", { class: "muted" }, isOpen ? "▲" : "▼")));
  const toggle = () => {
    if (opened.has(entry.id)) opened.delete(entry.id);
    else opened.add(entry.id);
    render();
  };
  row.addEventListener("click", toggle);
  row.addEventListener("keydown", event => { if (event.key === "Enter") toggle(); });
  return isOpen ? [row, detailRow(entry)] : [row];
}

function candyCell(entry) {
  const found = candies[entry.candy_family];
  if (!found || (found.candy == null && found.xl == null)) return element("span", { class: "muted" }, "-");
  return `${found.candy ?? "-"} / ${found.xl ?? "-"}`;
}

function tableHead() {
  return element("thead", {}, element("tr", {}, ...COLUMNS.map(([key, label], i) => {
    if (!key || !SORTS[key]) return element("th", { scope: "col", class: i === 0 ? "sticky-col" : null }, label);
    const active = sort.value === key;
    const th = element("th", { scope: "col", class: i === 0 ? "sticky-col" : null,
      "aria-sort": active ? (key === "species" ? "ascending" : "descending") : null },
      element("button", { type: "button" }, label));
    th.querySelector("button").addEventListener("click", () => {
      sort.value = key;
      sort.dispatchEvent(new Event("change"));
    });
    return th;
  })));
}

// A management code as a number: "01", "1", "001" and the earlier "PK1" are all number 1.
function codeNumber(value) {
  const match = String(value ?? "").trim().match(/^(?:PK)?\s?0*([1-9]\d{0,3})$/i);
  return match ? Number(match[1]) : null;
}

// The nickname already carries this entry's number: 망나뇽01 (or the earlier 망나뇽PK1).
function hasCode(entry) {
  const number = codeNumber(entry.code);
  const nickname = (entry.nickname ?? "").replace(/\s+/g, "").replace(/^R(?=[가-힣])/, "");
  // Only what follows the species name counts, so 폴리곤2 alone is not number 2.
  const rest = entry.species && nickname.startsWith(entry.species) ? nickname.slice(entry.species.length) : null;
  const tail = (rest ?? "").match(/^(?:PK)?0*([1-9]\d{0,3})$/i) ?? nickname.match(/PK0*([1-9]\d{0,3})$/i);
  return Boolean(number) && Boolean(tail) && Number(tail[1]) === number;
}

// Every word must match the species, the number (02 = 2) or the nickname: "마기라스 02".
function matches(entry, word) {
  const number = codeNumber(word);
  return (entry.species ?? "").includes(word) || (number !== null && codeNumber(entry.code) === number)
    || (entry.nickname ?? "").toUpperCase().includes(word.toUpperCase());
}

function ivPercent(iv) {
  return Math.round(((iv.attack + iv.defense + iv.stamina) / 45) * 100);
}

const levelValue = entry => entry.level?.value ?? entry.level?.candidates?.[0] ?? -1;
const SORTS = {
  recent: (a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
  cp: (a, b) => (b.cp ?? -1) - (a.cp ?? -1),
  iv: (a, b) => (b.iv ? ivPercent(b.iv) : -1) - (a.iv ? ivPercent(a.iv) : -1) || (b.cp ?? -1) - (a.cp ?? -1),
  level: (a, b) => levelValue(b) - levelValue(a) || (b.cp ?? -1) - (a.cp ?? -1),
  hp: (a, b) => (b.max_hp ?? -1) - (a.max_hp ?? -1),
  candy: (a, b) => (candies[b.candy_family]?.candy ?? -1) - (candies[a.candy_family]?.candy ?? -1),
  species: (a, b) => (a.species ?? "").localeCompare(b.species ?? "", "ko") || (a.code ?? "").localeCompare(b.code ?? "", "ko", { numeric: true }),
};

function render() {
  const words = search.value.trim().split(/\s+/).filter(Boolean);
  const shown = entries.filter(entry => words.every(word => matches(entry, word))).sort(SORTS[sort.value] ?? SORTS.recent);
  status.textContent = entries.length
    ? `${entries.length}개체 중 ${shown.length}개체를 보여줍니다.`
    : "아직 저장한 개체가 없습니다. 스캔 화면의 판독 카드에서 저장하세요.";
  list.replaceChildren(...(shown.length ? [element("table", { class: "box-table" }, tableHead(),
    element("tbody", {}, ...shown.flatMap(entryRows)))] : []));
}

async function load() {
  const [pokemon, resources] = await Promise.all([
    fetch("/api/pokemon").then(jsonResponse), fetch("/api/candies").then(jsonResponse)]);
  entries = pokemon.pokemon;
  candies = resources.candies;
  showCandies(resources);
  render();
  const id = location.hash.slice(1);
  if (id && entries.some(entry => entry.id === id)) {
    opened.add(id);
    render();
    const target = document.getElementById(id);
    target.classList.add("highlight");
    target.scrollIntoView({ block: "center" });
  }
}

// Candy per family and the account's stardust, editable by hand.
function showCandies(resources) {
  const panel = document.querySelector("#candy-panel");
  const families = Object.entries(resources.candies);
  panel.querySelector(".summary-meta").textContent = `${families.length}계열` +
    (resources.stardust ? ` · 별의모래 ${resources.stardust.value.toLocaleString()}` : "");
  const body = panel.querySelector("tbody");
  body.replaceChildren(...families.map(([family, values]) => {
    const input = (name, value) => element("input", { name, value: value ?? "", inputmode: "numeric", class: "candy-input",
      "aria-label": `${family} ${name}` });
    const save = element("button", { type: "button", class: "secondary" }, "저장");
    const row = element("tr", {},
      element("th", { scope: "row" }, family),
      element("td", {}, input("candy", values.candy)),
      element("td", {}, input("xl", values.xl)),
      element("td", {}, input("energy", values.energy), values.energy_kind ? ` ${values.energy_kind}` : ""),
      element("td", { class: "muted" }, new Date(values.updated_at).toLocaleDateString("ko-KR")),
      element("td", {}, save));
    save.addEventListener("click", async () => {
      const get = name => row.querySelector(`input[name=${name}]`).value.trim();
      save.disabled = true;
      try {
        await jsonResponse(await fetch(`/api/candies/${encodeURIComponent(family)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ candy: get("candy"), xl: get("xl"), energy: get("energy"), energy_kind: values.energy_kind }),
        }));
        await load();
      } catch (error) {
        status.textContent = error.message;
      } finally {
        save.disabled = false;
      }
    });
    return row;
  }));
}

// Add a Pokémon by hand (without a scan).
const addButton = document.querySelector("#add-button");
const addArea = document.querySelector("#add-area");
addButton.addEventListener("click", () => {
  if (addArea.childElementCount) {
    addArea.replaceChildren();
    return;
  }
  addArea.replaceChildren(element("h3", {}, "포켓몬 직접 추가"), editForm(null, async (saved, added) => {
    addArea.replaceChildren();
    if (saved && added) {
      status.textContent = `${added.species} ${added.code}번으로 추가했습니다.`;
      location.hash = added.id;
      await load();
    }
  }));
  addArea.querySelector("input[name=species]")?.focus();
});

// Backups
const backupList = document.querySelector("#backup-list");
const backupStatus = document.querySelector("#backup-status");
const restoreInput = document.querySelector("#restore-file");
const restorePreview = document.querySelector("#restore-preview");

function summaryText(summary) {
  const box = summary.box;
  return `${new Date(summary.created_at).toLocaleString("ko-KR")}에 만든 백업 · 박스 ${box.pokemon}개체` +
    (box.removed ? `(제거 ${box.removed})` : "") + ` · 이력 ${box.revisions}개` +
    (summary.with_scans ? ` · 스캔 ${summary.scans}개${summary.with_videos ? "(원본 영상 포함)" : ""}` : "");
}

async function loadBackups() {
  const { backups } = await jsonResponse(await fetch("/api/backups"));
  backupList.replaceChildren(...backups.slice(0, 10).map(item => element("li", {},
    element("a", { href: `/api/backups/${encodeURIComponent(item.name)}`, download: item.name }, item.name),
    ` · ${formatBytes(item.bytes)}${item.kind === "restore_safety" ? " · 복원 전 자동 백업" : ""}`)));
  if (!backups.length) backupList.replaceChildren(element("li", { class: "muted" }, "아직 만든 백업이 없습니다."));
}

document.querySelector("#backup-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector("button");
  button.disabled = true;
  backupStatus.textContent = "백업을 만들고 있습니다.";
  try {
    const result = await jsonResponse(await fetch("/api/backups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ with_scans: form.elements.with_scans.checked, with_videos: form.elements.with_videos.checked }),
    }));
    backupStatus.replaceChildren(`백업을 만들었습니다 (박스 ${result.box.pokemon}개체, ${formatBytes(result.bytes)}). `,
      element("a", { href: `/api/backups/${encodeURIComponent(result.name)}`, download: result.name }, "내려받기"));
    await loadBackups();
  } catch (error) {
    backupStatus.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

restoreInput.addEventListener("change", async () => {
  const file = restoreInput.files[0];
  restorePreview.replaceChildren();
  if (!file) return;
  restorePreview.textContent = "백업 파일을 검사하고 있습니다.";
  try {
    const { token, summary } = await jsonResponse(await fetch("/api/backups/preview", {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
    }));
    const confirmButton = element("button", { type: "button", class: "danger" }, "이 백업으로 복원");
    restorePreview.replaceChildren(
      element("p", {}, `검사 통과: ${summaryText(summary)}`),
      element("p", { class: "muted" }, "복원하면 현재 박스를 이 백업으로 바꿉니다. 현재 박스는 먼저 자동으로 백업합니다. 스캔은 없는 것만 추가합니다."),
      confirmButton);
    confirmButton.addEventListener("click", async () => {
      confirmButton.disabled = true;
      try {
        const result = await jsonResponse(await fetch("/api/backups/restore", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
        }));
        restorePreview.replaceChildren(element("p", {},
          `복원했습니다. 복원 전 박스는 ${result.safety_backup}로 저장했습니다. 추가한 스캔 ${result.scans_added}개.`));
        restoreInput.value = "";
        await Promise.all([load(), loadBackups()]);
      } catch (error) {
        restorePreview.textContent = error.message;
      }
    });
  } catch (error) {
    restorePreview.textContent = `사용할 수 없는 백업입니다: ${error.message}`;
  }
});

loadBackups().catch(error => { backupStatus.textContent = error.message; });
search.addEventListener("input", render);
try { sort.value = localStorage.getItem("boxscan-box-sort") || "recent"; } catch { /* storage blocked */ }
sort.addEventListener("change", () => {
  try { localStorage.setItem("boxscan-box-sort", sort.value); } catch { /* storage blocked */ }
  render();
});
load().catch(error => { status.textContent = error.message; });

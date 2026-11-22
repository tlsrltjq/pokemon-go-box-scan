const list = document.querySelector("#pokemon");
const status = document.querySelector("#status");
const search = document.querySelector("#search");
const sort = document.querySelector("#sort");
const CHANGES = { created: "새로 저장", updated: "갱신", deleted: "박스에서 제거" };
let entries = [];

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
  const origin = manual ? (source.note ? ` · 메모: ${source.note}` : "")
    : source?.filename ? ` · ${source.filename} 프레임 ${source.frames[0]}–${source.frames.at(-1)}` : "";
  const label = manual ? "직접 수정" : CHANGES[revision.change];
  const item = element("li", {},
    element("p", {}, element("strong", {}, `${revision.revision}번째 기록 · ${label}`),
      ` · ${new Date(revision.created_at).toLocaleString("ko-KR")}${origin}`));
  if (revision.change !== "deleted") item.append(comparisonTable(revision.before, revision.after));
  return item;
}

function editForm(entry, onDone) {
  const iv = entry.iv ?? {};
  const moves = entry.moves ?? {};
  const charged = moves.charged ?? [];
  const numeric = { inputmode: "numeric" };
  const message = element("div", { class: "save-message", role: "status" });
  const submit = element("button", { type: "submit" }, "수정 저장");
  const cancel = element("button", { type: "button", class: "secondary" }, "취소");
  const form = element("form", { class: "save-form edit-form" },
    element("div", { class: "fields" },
      field("종", "species", entry.species, { maxlength: "30" }),
      field("CP", "cp", entry.cp, numeric),
      field("최대 HP", "max_hp", entry.max_hp, numeric),
      field("IV 공격", "iv_attack", iv.attack, numeric),
      field("IV 방어", "iv_defense", iv.defense, numeric),
      field("IV HP", "iv_stamina", iv.stamina, numeric),
      field("노말어택", "fast_move", moves.fast?.name, { maxlength: "30" }),
      field("스페셜어택 1", "charged_1", charged[0]?.name, { maxlength: "30" }),
      field("스페셜어택 2", "charged_2", charged[1]?.name, { maxlength: "30" }),
      field("게임 별명", "nickname", entry.nickname, { maxlength: "30" })),
    element("div", { class: "checks" },
      checkbox("이로치 *", "shiny", entry.shiny === true),
      checkbox("반짝반짝 포켓몬 ♣", "lucky", entry.lucky === true),
      checkbox("배경 ◆", "background", entry.background === true),
      checkbox("그림자", "shadow", entry.shadow === true)),
    maxStateSelect(entry.max_state ?? ""),
    field("수정 메모 (선택)", "note", "", { maxlength: "200" }),
    element("p", { class: "muted" }, "비운 칸은 값을 지웁니다. 레벨과 기술 * 표시는 저장할 때 다시 계산합니다. 이전 값은 이력에 남습니다."),
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
      await jsonResponse(await fetch(`/api/pokemon/${entry.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: entry.revision,
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
            nickname: get("nickname") || null,
          },
        }),
      }));
      onDone(true);
    } catch (error) {
      message.textContent = error.status === 409 ? `${error.message} 목록을 새로 불러옵니다.` : error.message;
      if (error.status === 409) setTimeout(() => load(), 1500);
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

function entryCard(entry) {
  const history = element("ol", { class: "history", hidden: "" });
  const toggle = element("button", { type: "button", class: "secondary" }, "이력 보기");
  const remove = element("button", { type: "button", class: "danger" }, "박스에서 제거");
  const editButton = element("button", { type: "button", class: "secondary" }, "수정");
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
    const name = `${entry.code} ${displayName(entry)}`;
    if (!confirm(`${name}을(를) 박스에서 제거할까요? 이력과 관리 번호는 남고, 번호는 다시 쓰지 않습니다.`)) return;
    try {
      await jsonResponse(await fetch(`/api/pokemon/${entry.id}?revision=${entry.revision}`, { method: "DELETE" }));
      await load();
    } catch (error) {
      status.textContent = error.message;
    }
  });
  const moves = entry.moves ?? {};
  const badges = [];
  if (entry.max_state) badges.push([formatMaxState(entry.max_state), "max"]);
  if (entry.fusion?.length) badges.push([entry.fusion.some(f => f.type === "FUSE") ? "합체 가능" : "모습 변화 가능", "plain"]);
  const temp = entry.temp_observation;
  return element("article", { class: "card", id: entry.id },
    element("header", {},
      element("h3", { class: "card-title" }, displayName(entry), element("span", { class: "code-chip" }, entry.code ?? "번호 없음")),
      entry.iv ? element("span", { class: "badge neutral" }, `IV ${ivPercent(entry.iv)}%`) : null),
    badges.length ? element("div", { class: "badges" },
      ...badges.map(([text, kind]) => element("span", { class: `badge state ${kind}` }, text))) : null,
    element("dl", {},
      element("div", {}, element("dt", {}, "CP"), element("dd", {}, element("strong", {}, String(entry.cp ?? "-")))),
      element("div", {}, element("dt", {}, "최대 HP"), element("dd", {}, element("strong", {}, String(entry.max_hp ?? "-")))),
      element("div", {}, element("dt", {}, "레벨"), element("dd", {}, element("strong", {}, formatLevel(entry.level)))),
    ),
    element("p", {}, element("span", { class: "muted" }, "IV 공/방/체 "), element("strong", {}, formatIv(entry.iv))),
    entry.nickname ? element("p", {}, element("span", { class: "muted" }, "게임 별명 "), entry.nickname) : null,
    hasCode(entry) ? null : element("span", { class: "hint-chip", title: "게임 별명에 넣으면 다음 스캔 때 자동으로 고릅니다" },
      `별명에 ${entry.code} 넣기 전`),
    entry.cp_source === "calculated" ? element("p", { class: "muted" },
      "CP·최대 HP는 메가진화 중 기록과 레벨로 계산한 기본 모습 값입니다.") : null,
    temp ? element("p", { class: "muted" },
      `${temp.temp_name} 중 기록: CP ${temp.cp ?? "-"}, 최대 HP ${temp.max_hp ?? "-"}`) : null,
    element("div", { class: "moves" },
      moveLine("노말어택", moves.fast),
      ...(moves.charged ?? []).map((move, i, all) => moveLine(all.length > 1 ? `스페셜어택 ${i + 1}` : "스페셜어택", move))),
    entry.mega_move ? element("p", {}, element("span", { class: "muted" }, "메가진화 추가 "), entry.mega_move.name) : null,
    entry.max_moves?.length ? element("p", {}, element("span", { class: "muted" }, "맥스기술 "),
      entry.max_moves.map(m => `${m.name} ${m.level ? `레벨 ${m.level}` : m.locked ? "잠김" : ""}`.trim()).join(", ")) : null,
    element("p", { class: "muted" }, `마지막 갱신 ${new Date(entry.updated_at).toLocaleString("ko-KR")}`),
    element("div", { class: "actions" }, editButton, toggle, remove),
    editArea,
    history,
  );
}

// The nickname holds this entry's code exactly (PK1, also PK0001), not just as part of PK12.
function hasCode(entry) {
  const number = entry.code?.slice(2);
  return Boolean(number) && new RegExp(`PK\\s?0*${number}(?!\\d)`, "i").test(entry.nickname ?? "");
}

// Every word must match the species, the code (PK1 = PK0001) or the nickname: "마기라스 PK1".
function matches(entry, word) {
  const upper = word.toUpperCase();
  const code = upper.match(/^PK0*([1-9]\d*)$/);
  return (entry.species ?? "").includes(word) || (code ? entry.code === `PK${code[1]}` : false)
    || (entry.nickname ?? "").toUpperCase().includes(upper);
}

function ivPercent(iv) {
  return Math.round(((iv.attack + iv.defense + iv.stamina) / 45) * 100);
}

const SORTS = {
  recent: (a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
  cp: (a, b) => (b.cp ?? -1) - (a.cp ?? -1),
  iv: (a, b) => (b.iv ? ivPercent(b.iv) : -1) - (a.iv ? ivPercent(a.iv) : -1) || (b.cp ?? -1) - (a.cp ?? -1),
  species: (a, b) => (a.species ?? "").localeCompare(b.species ?? "", "ko") || (a.code ?? "").localeCompare(b.code ?? "", "ko", { numeric: true }),
};

function render() {
  const words = search.value.trim().split(/\s+/).filter(Boolean);
  const shown = entries.filter(entry => words.every(word => matches(entry, word))).sort(SORTS[sort.value] ?? SORTS.recent);
  status.textContent = entries.length
    ? `${entries.length}개체 중 ${shown.length}개체를 보여줍니다.`
    : "아직 저장한 개체가 없습니다. 스캔 화면의 판독 카드에서 저장하세요.";
  list.replaceChildren(...shown.map(entryCard));
}

async function load() {
  entries = (await jsonResponse(await fetch("/api/pokemon"))).pokemon;
  render();
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target) {
    target.classList.add("highlight");
    target.scrollIntoView({ block: "start" });
  }
}

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

const list = document.querySelector("#pokemon");
const status = document.querySelector("#status");
const search = document.querySelector("#search");
const CHANGES = { created: "새로 저장", updated: "갱신", deleted: "박스에서 제거" };
let entries = [];

function moveLine(label, move) {
  if (!move) return null;
  return element("div", { class: "move" },
    element("span", { class: "move-role" }, label),
    element("strong", {}, move.name, move.marker ? element("span", { class: "marker", title: move.reason }, move.marker) : null),
    element("span", {}, ""),
    move.marker ? element("span", { class: "move-reason" }, `* ${move.reason}`) : null,
  );
}

function revisionItem(revision) {
  const source = revision.source;
  const origin = source ? ` · ${source.filename} 프레임 ${source.frames[0]}–${source.frames.at(-1)}` : "";
  const item = element("li", {},
    element("p", {}, element("strong", {}, `rev ${revision.revision} · ${CHANGES[revision.change]}`),
      ` · ${new Date(revision.created_at).toLocaleString("ko-KR")}${origin}`));
  if (revision.change !== "deleted") item.append(comparisonTable(revision.before, revision.after));
  return item;
}

function entryCard(entry) {
  const history = element("ol", { class: "history", hidden: "" });
  const toggle = element("button", { type: "button", class: "secondary" }, "이력 보기");
  const remove = element("button", { type: "button", class: "danger" }, "박스에서 제거");
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
      element("h3", {}, `${entry.code} · ${displayName(entry)}`),
      element("span", { class: "badge neutral" }, `rev ${entry.revision}`)),
    badges.length ? element("div", { class: "badges" },
      ...badges.map(([text, kind]) => element("span", { class: `badge state ${kind}` }, text))) : null,
    element("dl", {},
      element("div", {}, element("dt", {}, "CP"), element("dd", {}, element("strong", {}, String(entry.cp ?? "-")))),
      element("div", {}, element("dt", {}, "최대 HP"), element("dd", {}, element("strong", {}, String(entry.max_hp ?? "-")))),
      element("div", {}, element("dt", {}, "레벨"), element("dd", {}, element("strong", {}, formatLevel(entry.level)))),
    ),
    element("p", {}, element("span", { class: "muted" }, "IV 공/방/체 "), element("strong", {}, formatIv(entry.iv))),
    entry.cp_source === "calculated" ? element("p", { class: "muted" },
      "CP·최대 HP는 메가진화 중 기록과 레벨로 계산한 기본 모습 값입니다.") : null,
    temp ? element("p", { class: "muted" },
      `${temp.temp_name} 중 기록: CP ${temp.cp ?? "-"}, 최대 HP ${temp.max_hp ?? "-"}`) : null,
    element("div", { class: "moves" },
      moveLine("노말어택", moves.fast),
      ...(moves.charged ?? []).map((move, i, all) => moveLine(all.length > 1 ? `스페셜어택 ${i + 1}` : "스페셜어택", move))),
    entry.max_moves?.length ? element("p", {}, element("span", { class: "muted" }, "맥스기술 "),
      entry.max_moves.map(m => `${m.name} ${m.level ? `레벨 ${m.level}` : m.locked ? "잠김" : ""}`.trim()).join(", ")) : null,
    element("p", { class: "muted" }, `마지막 갱신 ${new Date(entry.updated_at).toLocaleString("ko-KR")}`),
    element("div", { class: "actions" }, toggle, remove),
    history,
  );
}

function render() {
  const query = search.value.trim();
  const shown = entries.filter(entry => !query || (entry.species ?? "").includes(query));
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

search.addEventListener("input", render);
load().catch(error => { status.textContent = error.message; });

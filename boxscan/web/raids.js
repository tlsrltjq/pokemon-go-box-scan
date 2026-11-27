const form = document.querySelector("#raids-form");
const status = document.querySelector("#status");
const groups = document.querySelector("#groups");
const refreshButton = document.querySelector("#refresh");
let listing = null;
let run = 0;
const results = new Map();  // "raid-3" -> recommendation summary, for the quick party list

const GROUPS = [
  ["raids", false, "mega_legendary", "메가 전설·원시회귀"],
  ["raids", false, "5", "5성 (전설)"],
  ["raids", false, "mega", "메가"],
  ["raids", false, "3", "3성"],
  ["raids", false, "1", "1성"],
  ["raids", true, "5", "그림자 5성"],
  ["raids", true, "3", "그림자 3성"],
  ["raids", true, "1", "그림자 1성"],
];

function range(summary, unit = "") {
  const f = value => Math.round(Number(value)).toLocaleString();
  return summary.min === summary.max ? `${f(summary.avg)}${unit}` : `${f(summary.avg)}${unit} (${f(summary.min)}–${f(summary.max)})`;
}

function ago(iso) {
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours < 1) return "1시간 이내";
  if (hours < 48) return `${Math.floor(hours)}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

function showSource() {
  const source = document.querySelector("#source");
  if (!listing.version) {
    source.textContent = "저장된 보스 목록이 없습니다. 아래 갱신 버튼으로 오늘 목록을 받으세요.";
    return;
  }
  const when = new Date(listing.fetched_at).toLocaleString("ko-KR");
  source.replaceChildren(
    "출처 ", element("a", { href: listing.source_home, target: "_blank", rel: "noopener" }, listing.source),
    ` (레이드: LeekDuck 정리, 맥스배틀: Snacknap 정리) · 받은 시각 ${when} (${ago(listing.fetched_at)})` +
    ` · 레이드 ${listing.raids.length}개 · 맥스배틀 ${listing.max_battles.length}개` +
    (listing.failed ? ` · 매핑 실패 ${listing.failed}개` : ""));
  for (const [name, error] of Object.entries(listing.errors || {})) {
    source.append(element("br"), element("span", { class: "warn-text" }, `${name}을(를) 받지 못했습니다: ${error}`));
  }
  if ((Date.now() - new Date(listing.fetched_at).getTime()) / 3_600_000 >= 24) {
    source.append(element("br"), element("span", { class: "warn-text" }, "하루 넘게 지난 목록입니다. 레이드가 바뀌었을 수 있으니 갱신하세요."));
  }
}

function detailHref(boss) {
  const params = new URLSearchParams({ species: boss.species, tier: boss.tier });
  if (boss.form) params.set("form", boss.form);
  if (boss.temp) params.set("temp", boss.temp);
  if (boss.shadow) params.set("shadow", "1");
  const options = readOptions();
  if (options.weather) params.set("weather", options.weather);
  if (options.trainers && options.trainers !== "1") params.set("trainers", options.trainers);
  if (options.mega !== "off") params.set("mega", options.mega);
  return `/raid?${params}`;
}

function detailLink(boss) {
  if (boss.kind !== "raid") return element("a", { href: "/max" }, "맥스배틀 추천에서 자세히");
  const link = element("a", { href: detailHref(boss) }, "레이드 추천에서 자세히");
  link.addEventListener("click", () => { link.href = detailHref(boss); });  // the options may have changed
  return link;
}

function bossCard(boss) {
  const badges = [element("span", { class: "badge neutral" }, boss.tier_name ?? "등급 미확인")];
  if (boss.shadow) badges.push(element("span", { class: "badge state shadow" }, "그림자"));
  if (boss.temp_name) badges.push(element("span", { class: "badge state mega" }, boss.temp_name));
  const card = element("article", { class: `card boss-card t-${typeCode(boss.types?.[0])}`, id: `boss-${boss.kind}-${boss.index}` },
    element("header", { class: "boss-head" },
      avatar({ dex: boss.pokemon_id, form: boss.form, temp: boss.temp, types: boss.types, name: boss.species ?? boss.label }, "lg"),
      element("div", { class: "boss-title" }, element("h3", {}, boss.label),
        element("div", { class: "badges" }, ...badges, boss.types ? typeChips(boss.types) : null))));
  if (boss.status !== "ok") {
    card.append(element("p", { class: "warn-text" }, `매핑 실패: ${boss.reason}`),
      element("p", { class: "muted" }, `출처 값: ${boss.source.id} · ${boss.source.form ?? "모습 없음"} · 등급 ${boss.source.level}` +
        (boss.source.name_en ? ` · ${boss.source.name_en}` : "")));
    return card;
  }
  card.append(element("p", { class: "muted" }, `${boss.types.join("/")}` +
    (boss.variant ? ` · 출처 모습 ${boss.variant}` : "") +
    (boss.source.cp_range ? ` · 잡을 때 CP ${boss.source.cp_range[0]}–${boss.source.cp_range[1]}` : "")));
  for (const note of boss.notes) card.append(element("p", { class: "muted" }, note));
  card.append(element("div", { class: "boss-result" }, element("p", { class: "muted" }, "계산 대기 중")));
  card.append(element("p", { class: "muted" }, detailLink(boss)));
  return card;
}

// The top of the page: each current raid boss with its party in order, ready to copy in the game.
function quickRow(boss) {
  const s = results.get(`${boss.kind}-${boss.index}`)?.summary;
  const schedule = typeof scheduleFor === "function" ? scheduleFor(boss) : null;
  const outcome = !s ? "계산 중" : !s.damage ? "쓸 수 있는 개체 없음"
    : s.clear_s ? `혼자 ${Math.round(s.clear_s.avg)}초 처치` : `피해 ${Math.round((s.damage.avg / s.boss_hp) * 100)}% (처치 못 함)`;
  const party = s?.party ?? [];
  const row = element("a", { class: `quick-row t-${typeCode(boss.types?.[0])}`, href: `#boss-${boss.kind}-${boss.index}` },
    avatar({ dex: boss.pokemon_id, form: boss.form, temp: boss.temp, types: boss.types, name: boss.species ?? boss.label }, "md"),
    element("span", { class: "quick-boss" }, element("strong", {}, boss.label),
      element("span", {}, [boss.tier_name, boss.kind === "max" ? "맥스배틀" : null, schedule].filter(Boolean).join(" · "))),
    element("span", { class: `quick-outcome${s?.clear_s ? " clear" : ""}` }, outcome),
    element("span", { class: "quick-party" }, ...party.map(m => element("span", { class: "quick-member", title: `${m.order}. ${m.name} ${m.code ?? ""}` },
      avatar({ dex: m.dex, form: m.form, temp: m.temp, shiny: m.shiny, name: m.name }, "sm"),
      element("span", {}, m.code ? `${m.name.replace(/\s*\(.*\)$/, "")} ${m.code}` : m.name)))));
  return row;
}

function renderQuick() {
  const now = document.querySelector("#quick-now");
  if (!listing?.version) {
    now.replaceChildren(element("p", { class: "muted" }, "저장된 보스 목록이 없습니다. 아래 갱신 버튼으로 받으세요."));
    return;
  }
  const order = ["mega_legendary", "5", "mega", "3", "1"];
  const bosses = [...listing.raids.filter(b => b.status === "ok")
    .sort((a, b) => (a.shadow - b.shadow) || (order.indexOf(a.tier) - order.indexOf(b.tier))),
  ...listing.max_battles.filter(b => b.status === "ok")];
  now.replaceChildren(...bosses.map(quickRow));
  renderNext();
}

// Bosses of raid events that start later, with a link that prefills the raid planner.
function renderNext() {
  const holder = document.querySelector("#quick-next");
  if (typeof calendar === "undefined" || !calendar?.events) { holder.replaceChildren(); return; }
  const upcoming = calendar.events.filter(e => e.status === "upcoming" && e.raid && e.bosses.some(b => b.mapped));
  if (!upcoming.length) { holder.replaceChildren(); return; }
  const items = upcoming.flatMap(e => e.bosses.filter(b => b.mapped).map(b => ({ boss: b, event: e })));
  holder.replaceChildren(element("div", { class: "row-head" }, element("h3", {}, "다음 레이드"),
    element("span", { class: "muted" }, "누르면 그 보스로 파티를 미리 계산합니다 (등급은 짐작값이라 바꿀 수 있음)")),
    element("div", { class: "next-rail" }, ...items.slice(0, 12).map(({ boss, event }) => {
      const tier = boss.temp === "PRIMAL" ? "mega_legendary" : boss.temp ? "mega" : "5";
      const params = new URLSearchParams({ species: boss.species, tier });
      if (boss.temp) params.set("temp", boss.temp);
      if (boss.shadow) params.set("shadow", "1");
      const start = parseLocal(event.start);
      return element("a", { class: "next-card", href: `/raid?${params}` },
        avatar({ ...boss, name: boss.species }, "md"),
        element("span", {}, element("strong", {}, boss.name), element("span", {}, `${shortDate(start)} ${clock(start)}부터`)));
    })));
}

function showRecommendation(boss, result) {
  results.set(`${boss.kind}-${boss.index}`, result);
  const quick = document.querySelector(`.quick-row[href="#boss-${boss.kind}-${boss.index}"]`);
  quick?.replaceWith(quickRow(boss));
  const box = document.querySelector(`#boss-${boss.kind}-${boss.index} .boss-result`);
  const s = result.summary;
  if (!s.damage) {
    box.replaceChildren(element("p", { class: "warn-text" }, `추천에 쓸 수 있는 개체가 없습니다 (제외 ${s.excluded}마리).`));
    return;
  }
  const percent = Math.round((s.damage.avg / s.boss_hp) * 100);
  const moveList = items => items.map(m => `${m.name}(${m.type})`).join(", ");
  const lines = [
    s.clear_s
      ? element("p", {}, element("strong", {}, `혼자 처치 ${range(s.clear_s, "초", 1)}`),
        ` · ${s.defeated_scenarios}/${s.scenarios}가지 기술 조합에서 처치`)
      : element("p", {}, element("strong", {}, `예상 피해 ${range(s.damage)}`), ` · 보스 HP ${s.boss_hp.toLocaleString()}의 ${percent}%` +
        (s.defeated_scenarios ? ` · ${s.defeated_scenarios}/${s.scenarios}가지 기술 조합에서 처치` : "")),
    s.boss_moves ? element("p", { class: "muted" }, "보스 기술 · " + [
      s.boss_moves.fast.length ? `노말어택: ${moveList(s.boss_moves.fast)}` : null,
      `스페셜어택: ${moveList(s.boss_moves.charged)}`].filter(Boolean).join(" · ")) : null,
    s.dodge_comparison && s.dodge_comparison.chosen === "tank"
      ? element("p", { class: "mega-note" }, boss.kind === "raid"
        ? "다 맞으면서 하기 추천: 스페셜어택을 피하면 시간 안에 덜 깎습니다."
        : "다 맞으면서 하기 추천: 노림 공격을 피하면 시간 안에 덜 깎습니다.") : null,
    element("ol", { class: "party-list" }, ...s.party.map(m => element("li", {},
      avatar({ dex: m.dex, form: m.form, temp: m.temp, shiny: m.shiny, name: m.name }, "xs"),
      element("strong", {}, m.name), m.code ? ` ${m.code}` : "",
      element("span", { class: "muted" }, ` · Lv ${m.level} · ${m.fast} / ${m.charged ?? "스페셜어택 안 씀"}` +
        (m.mega ? ` · ${m.mega}${m.mega_evolved ? "" : " (아직 메가진화 안 함)"}` : "") + (m.max_move ? ` · ${m.max_move} · ${m.plan}` : "") +
        (m.damage.max > 0 ? ` · 피해 ${Math.round(m.damage.avg).toLocaleString()}` : " · 나오기 전에 끝남"))))),
  ];
  // An older saved result has the earlier comparison shape; it is shown without the mega lines.
  if (s.mega_comparison?.options) lines.push(...megaLines(s.mega_comparison));
  if (s.shadow) {
    const when = s.shadow.enraged_s == null ? "이 피해로는 격앙하지 않습니다" : `평균 ${s.shadow.enraged_s}초에 격앙`;
    const other = s.shadow.comparison ? ` · 바로 진정하면 ${Math.round(s.shadow.comparison.damage).toLocaleString()}` : "";
    lines.push(element("p", { class: "mega-note" }, `그림자 레이드 · 진정 안 함 · ${when}${other}`));
  }
  if (s.shortfall) lines.push(element("p", { class: "warn-text" }, s.shortfall));
  lines.push(element("p", { class: "muted" }, (result.cached ? "저장된 결과" : "새로 계산") +
    ` · ${s.engine} · ${s.ruleset} · 기준 데이터 ${s.reference}` + (s.search_approximate ? " · 후보를 줄인 근사 탐색" : "")));
  box.replaceChildren(...lines.filter(Boolean));
}

function showGroups() {
  const sections = [];
  for (const [kind, shadow, tier, title] of GROUPS) {
    const bosses = listing[kind].filter(b => b.status === "ok" && b.shadow === shadow && b.tier === tier);
    if (bosses.length) sections.push(element("section", {}, element("h2", {}, title), element("div", { class: "grid" }, ...bosses.map(bossCard))));
  }
  const failed = listing.raids.filter(b => b.status !== "ok");
  if (failed.length) sections.push(element("section", {}, element("h2", {}, `매핑하지 못한 레이드 보스 ${failed.length}개`),
    element("div", { class: "grid" }, ...failed.map(bossCard))));
  sections.push(element("section", {}, element("h2", {}, "맥스배틀"),
    listing.max_battles.length ? element("div", { class: "grid" }, ...listing.max_battles.map(bossCard))
      : element("p", { class: "muted" }, listing.version ? "출처의 현재 맥스배틀 목록이 비어 있습니다. 맥스배틀 추천 화면에서 보스를 직접 고를 수 있습니다." : "")));
  groups.replaceChildren(...sections);
}

function readOptions() {
  return { weather: form.elements.weather.value, trainers: form.elements.trainers.value.trim() || "1",
           mega: form.elements.mega.value };
}

async function recommendAll() {
  const mine = ++run;
  const options = readOptions();
  const bosses = [...listing.raids, ...listing.max_battles].filter(b => b.status === "ok");
  let done = 0;
  for (const boss of bosses) {
    if (mine !== run) return;  // a newer run started
    const box = document.querySelector(`#boss-${boss.kind}-${boss.index} .boss-result`);
    box.replaceChildren(element("p", { class: "muted" }, "계산하고 있습니다."));
    status.textContent = `추천 계산 중 ${done + 1}/${bosses.length}`;
    try {
      const result = await jsonResponse(await fetch("/api/raids/current/recommend", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: listing.version, kind: boss.kind, index: boss.index, ...options }),
      }));
      if (mine === run) showRecommendation(boss, result);
    } catch (error) {
      if (mine === run) box.replaceChildren(element("p", { class: "warn-text" }, error.message));
      if (error.status === 409) { status.textContent = error.message; return; }
    }
    done += 1;
  }
  if (mine === run) status.textContent = bosses.length ? `보스 ${bosses.length}개의 추천을 붙였습니다.` : "";
}

async function load(url, init) {
  listing = await jsonResponse(await fetch(url, init));
  results.clear();
  showSource();
  showGroups();
  renderQuick();
  markBossSchedules([...listing.raids, ...listing.max_battles]);
  recommendAll();
}

refreshButton.addEventListener("click", async () => {
  refreshButton.disabled = true;
  status.textContent = "보스 목록과 이벤트 달력을 내려받고 있습니다.";
  try {
    await load("/api/raids/current/refresh", { method: "POST" });
    await loadCalendar();
    markBossSchedules([...listing.raids, ...listing.max_battles]);
    renderQuick();
    if (listing.events_error) status.textContent = listing.events_error;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    refreshButton.disabled = false;
  }
});
form.addEventListener("submit", event => {
  event.preventDefault();
  if (listing) recommendAll();
});

spriteDownloader(document.querySelector("#sprite-tools"));

(async () => {
  try {
    const options = await jsonResponse(await fetch("/api/raid/options"));
    form.elements.weather.replaceChildren(...options.weather.map(w => element("option", { value: w.id }, w.name)));
    await loadCalendar();
    await load("/api/raids/current");
  } catch (error) {
    status.textContent = error.message;
  }
})();

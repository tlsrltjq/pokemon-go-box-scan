const form = document.querySelector("#max-form");
const status = document.querySelector("#status");
const section = document.querySelector("#result");
const tierDefaults = {};

function fill(select, items, keepFirst = false) {
  const first = keepFirst ? select.options[0] : null;
  select.replaceChildren(...(first ? [first] : []), ...items.map(item => element("option", { value: item.id }, item.name)));
}

function range(summary, unit = "", digits = 0) {
  const f = value => Number(value).toFixed(digits);
  return summary.min === summary.max ? `${f(summary.avg)}${unit}` : `${f(summary.avg)}${unit} (${f(summary.min)}–${f(summary.max)})`;
}

async function loadOptions() {
  const options = await jsonResponse(await fetch("/api/max/options"));
  document.querySelector("#species-list").replaceChildren(...options.species.map(name => element("option", { value: name })));
  fill(form.elements.tier, options.tiers.map(t => ({ id: t.id, name: `${t.name} · HP ${t.hp.toLocaleString()}` })));
  options.tiers.forEach(t => { tierDefaults[t.id] = t.time_limit; });
  form.elements.tier.value = "1";
  form.elements.time_limit_s.value = tierDefaults["1"];
  fill(form.elements.weather, options.weather);
}

async function loadBoss() {
  const species = form.elements.species.value.trim();
  const formField = document.querySelector("#form-field");
  if (!species) return;
  try {
    const { boss } = await jsonResponse(await fetch(`/api/max/options?species=${encodeURIComponent(species)}`));
    const moves = document.querySelector("#boss-moves");
    moves.textContent = `보스 스페셜어택 (들어가기 전에는 모름): ${boss.charged.map(m => m.type ? `${m.name}(${m.type})` : m.name).join(", ")}`;
    moves.hidden = !boss.charged.length;
    fill(form.elements.form, boss.forms);
    formField.hidden = boss.forms.length < 2;
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
}

function maxMoves(member) {
  const attack = member.max_attack;
  const parts = [`${attack.name} Lv.${attack.level} (위력 ${attack.power}, ${attack.type})`];
  if (member.guard) parts.push(`다이월 Lv.${member.guard}`);
  if (member.spirit) parts.push(`다이리커버리 Lv.${member.spirit}`);
  return parts.join(" · ");
}

function memberCard(member) {
  const state = member.max_state === "gigantamax" ? "거다이맥스" : "다이맥스";
  return element("article", { class: "card" },
    element("header", {},
      element("h3", {}, `${member.order}. ${member.name}`),
      element("span", { class: "badge neutral" }, member.code ?? "")),
    element("p", { class: "muted" }, `${state} · 레벨 ${member.level} · IV ${formatIv(member.iv)} · ${member.types.join("/")}` +
      (member.shadow ? " · 그림자 보정 공격 ×1.2·방어 ×0.833" : "")),
    element("div", { class: "move" }, element("span", { class: "move-role" }, "노말어택"), element("strong", {}, member.fast)),
    element("div", { class: "move" }, element("span", { class: "move-role" }, "스페셜어택"),
      element("strong", {}, member.charged ?? "쓰지 않음")),
    element("div", { class: "move" }, element("span", { class: "move-role" }, "맥스기술"), element("strong", {}, maxMoves(member))),
    element("p", { class: "muted" }, `다이맥스할 때: ${member.plan}`),
    element("dl", {},
      element("div", {}, element("dt", {}, "예상 피해"), element("dd", {}, element("strong", {}, range(member.damage)))),
      element("div", {}, element("dt", {}, "그중 맥스 기술"), element("dd", {}, element("strong", {}, range(member.max_damage)))),
      element("div", {}, element("dt", {}, "버틴 시간"), element("dd", {}, element("strong", {}, range(member.alive_s, "초", 1)),
        element("span", {}, member.fainted_scenarios ? `보스 기술 ${member.fainted_scenarios}가지에서 기절` : "기절하지 않음")))),
  );
}

const DODGE_NAMES = { dodge: "노림 공격은 피하기", tank: "다 맞으면서 하기" };

function outcomeText(r) {
  return r.clear_s ? `평균 ${r.clear_s.avg.toFixed(1)}초에 처치` : `평균 ${Math.round(r.damage.avg).toLocaleString()} 피해, 처치 못 함`;
}

function dodgeLine(result) {
  const c = result.dodge_comparison;
  if (!c) return null;
  const mine = result.result;
  if (c.chosen === "tank") {
    return element("p", { class: "mega-note" },
      `다 맞으면서 하기를 추천합니다. 노림 공격을 피하면 ${outcomeText(c.other)}입니다.`);
  }
  if (mine.clear_s && c.other.clear_s && mine.clear_s.avg - c.other.clear_s.avg > 0.5) {
    return element("p", { class: "muted" },
      `다 맞으면서 하면 평균 ${(mine.clear_s.avg - c.other.clear_s.avg).toFixed(1)}초 빨라지지만 기절이 늘 수 있어 피하기를 추천합니다.`);
  }
  return null;
}

function showResult(result) {
  section.hidden = false;
  const boss = result.boss;
  const name = `${boss.species}${boss.form_name ? ` (${boss.form_name})` : ""}`;
  const summary = document.querySelector("#summary");
  if (!result.result) {
    summary.replaceChildren(element("p", {},
      `추천에 쓸 수 있는 다이맥스·거다이맥스 개체가 없습니다. 박스에서 개체의 다이맥스 상태를 저장했는지 확인하세요` +
      (result.not_max_count ? ` (다이맥스 상태가 아닌 개체 ${result.not_max_count}마리).` : ".")));
  } else {
    const r = result.result;
    summary.replaceChildren(...[
      element("h3", {}, `${name} · ${boss.tier_name} · ${result.weather_name}`),
      element("p", {}, `보스 ${boss.types.join("/")} · HP ${boss.hp.toLocaleString()} · 제한 시간 ${result.time_limit_s}초 · ` +
        (result.trainers === 1 ? "혼자" : `${result.trainers}명`)),
      element("dl", {},
        element("div", {}, element("dt", {}, "처치 시간"), element("dd", {},
          element("strong", {}, r.clear_s ? range(r.clear_s, "초", 1) : "처치 못 함"),
          element("span", {}, `보스 기술 ${result.scenarios.length}가지 중 ${r.defeated_scenarios}가지에서 처치`))),
        element("div", {}, element("dt", {}, "예상 누적 피해"), element("dd", {}, element("strong", {}, range(r.damage)),
          element("span", {}, `보스 HP의 ${Math.round((r.damage.avg / r.boss_hp) * 100)}%`))),
        element("div", {}, element("dt", {}, "다이맥스 횟수 · 기절"), element("dd", {},
          element("strong", {}, `${range(r.max_phases, "", 1)} · ${range(r.faints, "", 1)}`)))),
      element("p", { class: "muted" }, `보스 스페셜어택 ${result.scenarios.length}가지 평균(괄호는 가장 낮은~높은 결과). ` +
        `${DODGE_NAMES[result.dodge ? "dodge" : "tank"]} 기준입니다.`),
      dodgeLine(result),
      result.shortfall ? element("p", { class: "warn-text" }, result.shortfall) : null,
    ].filter(Boolean));
  }
  document.querySelector("#party").replaceChildren(...result.party.map(memberCard));
  document.querySelector("#scenarios").replaceChildren(
    element("thead", {}, element("tr", {}, ...["보스 스페셜어택", "예상 피해", "전투 시간", "다이맥스", "기절"].map(t => element("th", { scope: "col" }, t)))),
    element("tbody", {}, ...result.by_scenario.map(s => element("tr", {},
      element("td", {}, s.charged), element("td", {}, `${s.damage.toLocaleString()}${s.defeated ? " (처치)" : ""}`),
      element("td", {}, `${s.battle_s}초`), element("td", {}, `${s.max_phases}번`), element("td", {}, `${s.faints}마리`)))));
  const excluded = document.querySelector("#excluded");
  excluded.replaceChildren(...(result.excluded.length ? [
    element("h3", {}, `제외한 개체 ${result.excluded.length}마리`),
    element("ul", { class: "assumptions" }, ...result.excluded.map(e => element("li", {}, `${e.code ?? ""} ${e.name}: ${e.reason}`)))] : []));
  document.querySelector("#assumptions").replaceChildren(...result.assumptions.map(a => element("li", {}, a)));
  const search = result.search;
  document.querySelector("#versions").textContent =
    `후보 ${search.candidates}마리 중 ${search.considered}마리로 ${search.parties}개 출전 순서를 비교했습니다` +
    (search.approximate ? " (후보를 줄여 탐색한 근사 결과)" : "") +
    `. 계산 ${result.engine} · 규칙 ${result.ruleset} · 기준 데이터 ${result.reference}`;
}

form.elements.species.addEventListener("change", loadBoss);
form.elements.tier.addEventListener("change", () => {
  form.elements.time_limit_s.value = tierDefaults[form.elements.tier.value] ?? form.elements.time_limit_s.value;
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  status.textContent = "계산하고 있습니다.";
  try {
    const result = await jsonResponse(await fetch("/api/max/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        species: form.elements.species.value,
        tier: form.elements.tier.value,
        weather: form.elements.weather.value,
        form: document.querySelector("#form-field").hidden ? null : form.elements.form.value,
        time_limit_s: form.elements.time_limit_s.value,
        trainers: form.elements.trainers.value,
        dodge: form.elements.dodge.value,
        orbs: form.elements.orbs.checked,
      }),
    }));
    status.textContent = "";
    showResult(result);
  } catch (error) {
    status.textContent = error.message;
    section.hidden = true;
  } finally {
    button.disabled = false;
  }
});

loadOptions().catch(error => { status.textContent = error.message; });

const form = document.querySelector("#raid-form");
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
  const options = await jsonResponse(await fetch("/api/raid/options"));
  document.querySelector("#species-list").replaceChildren(...options.species.map(name => element("option", { value: name })));
  fill(form.elements.tier, options.tiers.map(t => ({ id: t.id, name: `${t.name} · HP ${t.hp.toLocaleString()}` })));
  options.tiers.forEach(t => { tierDefaults[t.id] = t.time_limit; });
  form.elements.tier.value = "5";
  form.elements.time_limit_s.value = tierDefaults["5"];
  fill(form.elements.weather, options.weather);
}

async function loadBoss() {
  const species = form.elements.species.value.trim();
  const formField = document.querySelector("#form-field");
  const tempField = document.querySelector("#temp-field");
  if (!species) return;
  try {
    const { boss } = await jsonResponse(await fetch(`/api/raid/options?species=${encodeURIComponent(species)}`));
    fill(form.elements.boss_fast, boss.fast, true);
    fill(form.elements.boss_charged, boss.charged, true);
    fill(form.elements.form, boss.forms);
    formField.hidden = boss.forms.length < 2;
    fill(form.elements.temp, [{ id: "", name: "하지 않음" }, ...boss.temp]);
    tempField.hidden = !boss.temp.length;
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
}

function memberCard(member) {
  return element("article", { class: "card" },
    element("header", {},
      element("h3", {}, `${member.order}. ${member.name}`),
      element("span", { class: "badge neutral" }, member.code ?? "")),
    element("p", { class: "muted" }, `레벨 ${member.level} · IV ${formatIv(member.iv)} · ${member.types.join("/")}` +
      (member.shadow ? " · 그림자 보정 공격 ×1.2·방어 ×0.833" : "")),
    member.mega ? element("p", { class: "mega-note" },
      `${member.mega.name} 상태로 출전 · 싸우는 동안 ${member.mega.boost_types.join("·")} 공격 ×${member.mega.same_type}, ` +
      `다른 타입 ×${member.mega.different_type}` + (member.mega.plus_move ? ` · ${member.mega.plus_move} 사용 가능` : "")) : null,
    element("div", { class: "move" }, element("span", { class: "move-role" }, "노말어택"), element("strong", {}, member.fast)),
    element("div", { class: "move" }, element("span", { class: "move-role" }, "스페셜어택"),
      element("strong", {}, member.charged), element("span", { class: "move-power" }, member.charged_type ?? "")),
    member.charged_note ? element("p", { class: "muted" }, member.charged_note) : null,
    element("dl", {},
      element("div", {}, element("dt", {}, "예상 피해"), element("dd", {}, element("strong", {}, range(member.damage)))),
      element("div", {}, element("dt", {}, "버틴 시간"), element("dd", {}, element("strong", {}, range(member.alive_s, "초", 1)))),
      element("div", {}, element("dt", {}, "이론 DPS"), element("dd", {}, element("strong", {}, String(member.theoretical_dps))))),
  );
}

function shadowLine(shadow) {
  if (!shadow) return null;
  const calm = shadow.calm_after_ms == null ? "진정 안 함" : `격앙 ${shadow.calm_after_ms / 1000}초 뒤 진정`;
  const when = shadow.enraged_s == null ? "이 피해로는 격앙 시점(남은 HP " + Math.round(shadow.enrage_at * 100) + "%)까지 가지 않습니다"
    : `평균 ${shadow.enraged_s}초에 격앙(${shadow.enraged_runs}/${shadow.runs}가지 조합)`;
  const other = shadow.comparison
    ? ` 같은 파티로 ${shadow.comparison.calm_after_s == null ? "진정하지 않으면" : "격앙하자마자 진정하면"} ${Math.round(shadow.comparison.damage).toLocaleString()}.`
    : "";
  return element("p", { class: "mega-note" }, `그림자 레이드 · ${calm} · ${when}.${other}`);
}

function megaLine(comparison) {
  if (!comparison) return null;
  if (!comparison.candidates) return element("p", { class: "muted" }, "박스에 메가진화·원시회귀할 수 있는 개체가 없어 메가진화 없이 계산했습니다.");
  if (!comparison.member) return element("p", { class: "muted" }, "메가진화 개체를 넣어도 피해가 늘지 않아 메가진화 없이 추천합니다.");
  const gain = comparison.with_mega - comparison.without_mega;
  return element("p", {}, `${comparison.member}을(를) 넣으면 메가진화 없는 추천(${Math.round(comparison.without_mega).toLocaleString()})보다 ` +
    `평균 ${Math.round(gain).toLocaleString()} 더 줍니다.`);
}

function showResult(result) {
  section.hidden = false;
  const boss = result.boss;
  const name = `${boss.shadow ? "그림자 " : ""}${boss.species}${boss.form_name ? ` (${boss.form_name})` : ""}${boss.temp_name ? ` · ${boss.temp_name}` : ""}`;
  const summary = document.querySelector("#summary");
  if (!result.result) {
    summary.replaceChildren(element("p", {}, "추천에 쓸 수 있는 개체가 없습니다. 아래 제외 사유를 확인하세요."));
  } else {
    const r = result.result;
    summary.replaceChildren(...[
      element("h3", {}, `${name} · ${boss.tier_name} · ${result.weather_name}`),
      element("p", {}, `보스 ${boss.types.join("/")} · HP ${boss.hp.toLocaleString()} · 제한 시간 ${result.time_limit_s}초` +
        (result.rejoin_delay_s == null ? " · 재입장 없음" : ` · 재입장 지연 ${result.rejoin_delay_s}초`) +
        (result.trainers > 1 ? ` · ${result.trainers}명` : " · 혼자")),
      element("dl", {},
        element("div", {}, element("dt", {}, "예상 누적 피해"), element("dd", {}, element("strong", {}, range(r.damage)),
          element("span", {}, `보스 HP의 ${Math.round((r.damage.avg / r.boss_hp) * 100)}%`))),
        element("div", {}, element("dt", {}, "유효 전투 시간"), element("dd", {}, element("strong", {}, range(r.battle_s, "초", 1)))),
        element("div", {}, element("dt", {}, "기절 · 재입장"), element("dd", {}, element("strong", {}, `${range(r.faints)} · ${range(r.rejoins)}`)))),
      element("p", { class: "muted" }, `보스 기술 조합 ${result.scenarios.length}가지 평균(괄호는 가장 낮은~높은 결과). ` +
        (r.defeated_scenarios ? `${r.defeated_scenarios}가지 조합에서 혼자 보스를 쓰러뜨립니다. ` : "") +
        `평균 DPS ${r.dps.avg}.`),
      megaLine(result.mega_comparison),
      shadowLine(result.shadow),
      result.shortfall ? element("p", { class: "warn-text" }, result.shortfall) : null,
    ].filter(Boolean));
  }
  document.querySelector("#party").replaceChildren(...result.party.map(memberCard));
  const weather = document.querySelector("#weather-compare");
  weather.replaceChildren(...(result.weather_comparison ? [
    element("h3", {}, "날씨별 비교"),
    element("table", { class: "compare" },
      element("thead", {}, element("tr", {}, ...["날씨", "예상 피해", "추천 파티"].map(t => element("th", { scope: "col" }, t)))),
      element("tbody", {}, ...result.weather_comparison.map(row => element("tr",
        { class: row.weather === result.weather ? "changed" : null },
        element("td", {}, row.name), element("td", {}, range(row.damage)), element("td", {}, row.party.join(" → "))))))] : []));
  document.querySelector("#scenarios").replaceChildren(
    element("thead", {}, element("tr", {}, ...["보스 노말어택", "보스 스페셜어택", "예상 피해", "전투 시간"].map(t => element("th", { scope: "col" }, t)))),
    element("tbody", {}, ...result.by_scenario.map(s => element("tr", {},
      element("td", {}, s.fast), element("td", {}, s.charged),
      element("td", {}, `${s.damage.toLocaleString()}${s.defeated ? " (처치)" : ""}`), element("td", {}, `${s.battle_s}초`)))));
  const excluded = document.querySelector("#excluded");
  excluded.replaceChildren(...(result.excluded.length ? [
    element("h3", {}, `제외한 개체 ${result.excluded.length}마리`),
    element("ul", { class: "assumptions" }, ...result.excluded.map(e => element("li", {}, `${e.code ?? ""} ${e.name}: ${e.reason}`)))] : []));
  document.querySelector("#assumptions").replaceChildren(...result.assumptions.map(a => element("li", {}, a)));
  const search = result.search;
  document.querySelector("#versions").textContent =
    `후보 ${search.candidates}마리 중 ${search.considered}마리로 ${search.combinations}개 파티를 비교하고 ${search.fully_simulated ?? 0}개를 전체 계산했습니다` +
    (search.approximate ? " (후보를 줄여 탐색한 근사 결과)" : "") +
    `. 계산 ${result.engine} · 규칙 ${result.ruleset} · 기준 데이터 ${result.reference}`;
}

form.elements.species.addEventListener("change", loadBoss);
form.elements.shadow.addEventListener("change", () => {
  document.querySelector("#shadow-fields").hidden = !form.elements.shadow.checked;
});
form.elements.tier.addEventListener("change", () => {
  form.elements.time_limit_s.value = tierDefaults[form.elements.tier.value] ?? form.elements.time_limit_s.value;
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  status.textContent = "계산하고 있습니다.";
  try {
    const result = await jsonResponse(await fetch("/api/raid/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        species: form.elements.species.value,
        tier: form.elements.tier.value,
        weather: form.elements.weather.value,
        form: document.querySelector("#form-field").hidden ? null : form.elements.form.value,
        temp: document.querySelector("#temp-field").hidden ? null : form.elements.temp.value || null,
        time_limit_s: form.elements.time_limit_s.value,
        rejoin_delay_s: form.elements.rejoin.checked ? form.elements.rejoin_delay_s.value : null,
        boss_fast: form.elements.boss_fast.value || null,
        boss_charged: form.elements.boss_charged.value || null,
        compare_weather: form.elements.compare_weather.checked,
        include_mega: form.elements.include_mega.checked,
        trainers: form.elements.trainers.value,
        shadow: form.elements.shadow.checked ? {
          enrage_at: Number(form.elements.enrage_at.value) / 100,
          attack: form.elements.enraged_attack.value,
          defense: form.elements.enraged_defense.value,
          calm_after_s: form.elements.calm_after_s.value || null,
        } : null,
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

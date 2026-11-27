const form = document.querySelector("#raid-form");
const status = document.querySelector("#status");
const section = document.querySelector("#result");
const tierDefaults = {};

function fill(select, items, keepFirst = false) {
  const first = keepFirst ? select.options[0] : null;
  select.replaceChildren(...(first ? [first] : []), ...items.map(item => element("option", { value: item.id }, item.name)));
}

function range(summary, unit = "", digits = 0) {
  const f = value => Number(value).toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
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
    const moves = document.querySelector("#boss-moves");
    moves.textContent = `보스가 쓸 수 있는 기술 · 노말어택: ${boss.fast.map(m => m.name).join(", ") || "없음"}` +
      ` · 스페셜어택: ${boss.charged.map(m => m.name).join(", ") || "없음"}`;
    moves.hidden = false;
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
  return element("article", { class: `card member-card t-${typeCode(member.types?.[0])}` },
    element("header", { class: "member-head" },
      avatar({ dex: member.dex, form: member.form, temp: member.mega?.kind, shiny: member.shiny, types: member.types, name: member.name }, "md"),
      element("h3", {}, element("span", { class: "order" }, String(member.order)), member.name),
      element("span", { class: "badge neutral" }, member.code ?? "")),
    element("p", { class: "muted" }, `레벨 ${member.level} · IV ${formatIv(member.iv)} · ${member.types.join("/")}` +
      (member.shadow ? " · 그림자 보정 공격 ×1.2·방어 ×0.833" : "")),
    member.mega ? element("p", { class: "mega-note" },
      `${member.mega.name} 상태로 출전 (${!member.mega.evolved ? "아직 메가진화 안 한 개체"
        : member.mega.level ? `메가 Lv.${member.mega.level}` : "메가진화해 본 개체"}) · ` +
      `싸우는 동안 ${member.mega.boost_types.join("·")} 공격 ×${member.mega.same_type}, 다른 타입 ×${member.mega.different_type}` +
      (member.mega.plus_move ? ` · ${member.mega.plus_move} 사용${member.mega.plus_confirmed ? "" : " (가정)"}` : "")) : null,
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

function bossMovesLine(moves) {
  if (!moves) return null;
  const list = items => items.map(m => `${m.name}(${m.type})`).join(", ") || "없음";
  return element("p", {}, `보스가 쓸 수 있는 기술 · 노말어택: ${list(moves.fast)} · 스페셜어택: ${list(moves.charged)}. ` +
    "들어가기 전에는 어떤 기술인지 모르므로 모든 조합을 계산했습니다.");
}

const DODGE_NAMES = { dodge: "스페셜어택은 피하기", tank: "다 맞으면서 하기" };

function outcomeText(o) {
  return o.clear_s ? `평균 ${o.clear_s.avg}초에 처치` : `피해 ${Math.round(o.damage.avg).toLocaleString()} (처치 못 함)`;
}

function dodgeLine(result) {
  const comparison = result.dodge_comparison;
  if (!comparison) return element("p", { class: "muted" }, `회피: ${DODGE_NAMES[result.dodge ? "dodge" : "tank"]}.`);
  const other = comparison.other;
  if (comparison.chosen === "tank") {
    return element("p", { class: "mega-note" }, "다 맞으면서 하기를 추천합니다. 스페셜어택을 피하면 시간 안에 덜 깎습니다" +
      ` (피하면 ${outcomeText(other)}, 그때 파티 ${other.party.join(" → ")}).`);
  }
  const r = result.result;
  if (r.clear_s && other.clear_s && other.defeated_scenarios === r.defeated_scenarios) {
    const saved = r.clear_s.avg - other.clear_s.avg;
    return element("p", { class: "muted" }, "스페셜어택을 피하는 기준입니다. " +
      (saved > 0.5 ? `다 맞으면서 하면 평균 ${saved.toFixed(1)}초 빨라집니다 (파티 ${other.party.join(" → ")}).`
        : "다 맞으면서 해도 처치 시간이 거의 같습니다."));
  }
  return element("p", { class: "muted" }, `스페셜어택을 피하는 기준입니다. 다 맞으면서 하면 ${outcomeText(other)}.`);
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
      element("div", { class: "result-head" },
        avatar({ dex: boss.dex, form: boss.form, temp: boss.temp, types: boss.types, name: boss.species }, "lg"),
        element("div", {}, element("h3", {}, name), element("p", { class: "muted" }, `${boss.tier_name} · ${result.weather_name}`),
          boss.types ? typeChips(boss.types) : null)),
      element("p", {}, `보스 ${boss.types.join("/")} · HP ${boss.hp.toLocaleString()} · 제한 시간 ${result.time_limit_s}초` +
        (result.rejoin_delay_s == null ? " · 재입장 없음" : ` · 재입장 지연 ${result.rejoin_delay_s}초`) +
        (result.trainers > 1 ? ` · ${result.trainers}명` : " · 혼자")),
      element("dl", {},
        element("div", {}, element("dt", {}, "처치 시간"), element("dd", {}, element("strong", {},
          r.clear_s ? range(r.clear_s, "초", 1) : "처치 못 함"),
          element("span", {}, `보스 기술 ${result.scenarios.length}가지 조합 중 ${r.defeated_scenarios}가지에서 처치`))),
        element("div", {}, element("dt", {}, "예상 누적 피해"), element("dd", {}, element("strong", {}, range(r.damage)),
          element("span", {}, `보스 HP의 ${Math.round((r.damage.avg / r.boss_hp) * 100)}%`))),
        element("div", {}, element("dt", {}, "기절 · 재입장"), element("dd", {}, element("strong", {}, `${range(r.faints)} · ${range(r.rejoins)}`)))),
      element("p", { class: "muted" }, `보스 기술 조합 평균(괄호는 가장 낮은~높은 결과). 유효 전투 시간 ${range(r.battle_s, "초", 1)} · 평균 DPS ${r.dps.avg}.`),
      bossMovesLine(boss.moves),
      dodgeLine(result),
      ...megaLines(result.mega_comparison),
      shadowLine(result.shadow),
      result.shortfall ? element("p", { class: "warn-text" }, result.shortfall) : null,
    ].filter(Boolean));
  }
  document.querySelector("#party").replaceChildren(...result.party.map(memberCard));
  const weather = document.querySelector("#weather-compare");
  weather.replaceChildren(...(result.weather_comparison ? [
    element("h3", {}, "날씨별 비교"),
    element("table", { class: "compare" },
      element("thead", {}, element("tr", {}, ...["날씨", "처치 시간", "예상 피해", "추천 파티"].map(t => element("th", { scope: "col" }, t)))),
      element("tbody", {}, ...result.weather_comparison.map(row => element("tr",
        { class: row.weather === result.weather ? "changed" : null },
        element("td", {}, row.name), element("td", {}, row.clear_s ? range(row.clear_s, "초", 1) : "처치 못 함"),
        element("td", {}, range(row.damage)), element("td", {}, row.party.join(" → "))))))] : []));
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
        dodge: form.elements.dodge.value,
        compare_weather: form.elements.compare_weather.checked,
        mega: form.elements.mega.value,
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

// Opened from 현재 레이드 with the boss filled in: /raid?species=…&tier=…&form=…&temp=…&shadow=1
async function prefill() {
  const params = new URLSearchParams(location.search);
  if (!params.get("species")) return;
  form.elements.species.value = params.get("species");
  if (params.get("tier") && tierDefaults[params.get("tier")]) {
    form.elements.tier.value = params.get("tier");
    form.elements.time_limit_s.value = tierDefaults[params.get("tier")];
  }
  if (params.get("weather")) form.elements.weather.value = params.get("weather");
  if (params.get("trainers")) form.elements.trainers.value = params.get("trainers");
  form.elements.mega.value = MEGA_MODE_NAMES[params.get("mega")] ? params.get("mega") : "off";
  form.elements.shadow.checked = params.get("shadow") === "1";
  document.querySelector("#shadow-fields").hidden = !form.elements.shadow.checked;
  await loadBoss();
  if (params.get("form")) form.elements.form.value = params.get("form");
  if (params.get("temp")) form.elements.temp.value = params.get("temp");
  form.requestSubmit();
}

loadOptions().then(prefill).catch(error => { status.textContent = error.message; });

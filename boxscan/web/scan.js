const form = document.querySelector("#upload");
const input = document.querySelector("#video");
const button = form.querySelector("button");
const status = document.querySelector("#status");
const framesSection = document.querySelector("#frames-section");
const framesStatus = document.querySelector("#frames-status");
const gallery = document.querySelector("#frames");
const analysisSection = document.querySelector("#analysis-section");
const analysisStatus = document.querySelector("#analysis-status");
const cards = document.querySelector("#individuals");
const STATS = IV_KEYS;
let currentScan = null;

function showFrames(frames) {
  gallery.replaceChildren(...frames.map((url, index) => element("figure", {},
    element("a", { href: url },
      element("img", { src: url, loading: "lazy", alt: `추출 프레임 ${index + 1}` })),
    element("figcaption", {}, `프레임 ${index + 1}`),
  )));
}

function reading(label, item, unit = "") {
  const value = item ? `${item.value}${unit}` : "읽지 못함";
  const votes = item ? `${item.observations}장 중 ${item.votes}장 일치` : "원본 프레임 확인 필요";
  return element("div", { class: "reading" },
    element("dt", {}, label),
    element("dd", {}, element("strong", {}, value), element("span", {}, votes)),
  );
}

function ivBlock(appraisals) {
  const read = appraisals.filter(item => item.iv);
  const last = read.at(-1);
  if (!last) {
    const reason = appraisals.length
      ? "막대가 멈춘 구간이 짧아 판정하지 않았습니다. 조사 화면을 1초 이상 보여 주세요."
      : "조사 화면을 찾지 못했습니다.";
    return element("div", { class: "iv empty" }, element("h4", {}, "IV"), element("p", {}, reason));
  }
  const total = STATS.reduce((sum, [key]) => sum + last.iv[key], 0);
  const rows = STATS.map(([key, label]) => {
    const value = last.iv[key];
    const bar = element("span", { class: "bar", role: "img", "aria-label": `${label} ${value}/15` });
    bar.style.setProperty("--fill", `${(value / 15) * 100}%`);
    if (value === 15) bar.classList.add("full");
    return element("div", { class: "iv-row" },
      element("span", { class: "iv-label" }, label), bar, element("strong", {}, String(value)));
  });
  const note = `${last.stable_seconds.toFixed(1)}초 동안 같은 값` +
    (read.length > 1 ? ` · 조사 화면 ${read.length}회 중 마지막` : "");
  return element("div", { class: "iv" },
    element("h4", {}, `IV ${total}/45 (${Math.round((total / 45) * 100)}%)`),
    ...rows,
    element("p", { class: "muted" }, note),
  );
}

const MISSING = { species: "종", iv: "IV", cp: "CP" };

function levelBlock(level) {
  if (!level) return null;
  const levels = level.levels.map(value => String(value));
  const max = level.max_level_checked;
  const views = {
    RESOLVED_BY_CONSTRAINTS: [`레벨 ${levels[0]}`,
      `CP·${level.used_max_hp ? "최대 HP·" : ""}IV로 하나로 정해졌습니다.`],
    AMBIGUOUS: [`레벨 ${levels.join(" 또는 ")}`,
      "같은 값이 나오는 레벨이 여러 개입니다. 다음 강화 비용으로 좁힐 예정입니다."],
    CONFLICT: ["맞는 레벨 없음",
      `레벨 ${max} 이하에서 이 CP·HP·IV 조합이 나오지 않습니다. 판독 값을 원본과 비교하세요. ` +
      `메가진화·베스트 파트너·다른 모습이거나 레벨 ${max}보다 높을 수도 있습니다.`],
    MISSING_INPUT: ["레벨 계산 안 함",
      `${(level.missing ?? []).map(key => MISSING[key] ?? key).join(", ")} 값이 없습니다.`],
    NO_REFERENCE: ["레벨 계산 안 함",
      "기준 데이터가 없습니다. Mac에서 reference update 명령을 실행하세요."],
    UNKNOWN_SPECIES: ["레벨 계산 안 함", "기준 데이터에서 이 종 이름을 찾지 못했습니다."],
  };
  const [title, detail] = views[level.status] ?? ["레벨", level.status];
  const notes = [detail];
  if (level.form && level.form !== "Normal") notes.push(`${level.form_name ?? level.form} 종족값 기준입니다.`);
  const others = Object.entries(level.other_forms ?? {});
  if (others.length) {
    notes.push("다른 모습으로도 맞습니다: " +
      others.map(([form, found]) => `${form} 레벨 ${found.join("/")}`).join(", "));
  }
  if (level.temp_evolution && level.regular?.length === 1) {
    const regular = level.regular[0];
    notes.push(`메가진화·원시회귀 중의 화면 CP로 계산했습니다. 기본 모습 CP ${regular.cp}, 최대 HP ${regular.max_hp} (계산값).`);
  }
  const warn = ["CONFLICT", "AMBIGUOUS"].includes(level.status);
  return element("div", { class: warn ? "level warn" : "level" },
    element("h4", {}, title),
    element("p", {}, notes.join(" ")),
  );
}

function moveRow(label, move) {
  const boost = move.boost ? ` +${move.boost}` : "";
  const notes = [`${move.observations}장 중 ${move.votes}장`];
  if (move.ocr_text) notes.push(`화면 글자 "${move.ocr_text}"를 기술 목록에서 가장 비슷한 이름으로 고침`);
  if (move.match === "none") notes.push("기술 사전에서 찾지 못해 화면 글자 그대로입니다");
  if (move.reference_power != null && move.power != null && move.reference_power !== move.power) {
    notes.push(`기준 데이터 위력 ${move.reference_power}와 다름`);
  }
  const name = element("strong", {}, move.name ?? move.value);
  if (move.marker) {
    name.append(element("span", { class: "marker", title: move.reason }, move.marker));
  }
  return element("div", { class: "move" },
    element("span", { class: "move-role" }, label),
    name,
    element("span", { class: "move-power" }, move.power === null ? "" : `위력 ${move.power}${boost}`),
    move.marker ? element("span", { class: "move-reason" }, `* ${move.reason}`) : null,
    move.boost_kind === "mega" ? element("span", { class: "move-reason neutral" }, `+${move.boost}은 메가진화 보너스`) : null,
    element("span", { class: "move-votes" }, notes.join(" · ")),
  );
}

function megaPlusRow(move) {
  const boost = move.boost ? ` +${move.boost}` : "";
  const notes = [`${move.observations}장 중 ${move.votes}장`];
  if (move.boost) notes.push(`+${move.boost}은 메가진화 보너스(메가 레벨)`);
  if (move.reference_power != null && move.power != null && move.reference_power !== move.power) {
    notes.push(`기준 데이터 위력 ${move.reference_power}와 다름`);
  }
  return element("div", { class: "move" },
    element("span", { class: "move-role" }, "메가진화 추가"),
    element("strong", {}, move.name ?? `${move.value}+`),
    element("span", { class: "move-power" }, move.power == null ? "" : `위력 ${move.power}${boost}`),
    element("span", { class: "move-reason neutral" }, move.reason ?? "메가진화 중에만 쓰는 추가 스페셜어택"),
    element("span", { class: "move-votes" }, notes.join(" · ")),
  );
}

function movesBlock(moves) {
  if (!moves || (!moves.fast && !moves.charged.length)) {
    return element("div", { class: "moves empty" }, element("h4", {}, "기술"),
      element("p", {}, "기술 화면을 찾지 못했습니다. 기술 목록이 보이게 1초 정도 멈춰 주세요."));
  }
  const rows = [];
  if (moves.fast) rows.push(moveRow("노말어택", moves.fast));
  else rows.push(element("p", { class: "muted" }, "노말어택을 읽지 못했습니다."));
  moves.charged.forEach((move, i) => rows.push(moveRow(moves.charged.length > 1 ? `스페셜어택 ${i + 1}` : "스페셜어택", move)));
  if (moves.mega_plus) rows.push(megaPlusRow(moves.mega_plus));
  const boosted = [moves.fast, ...moves.charged].some(move => move?.boost && move.boost_kind !== "mega");
  return element("div", { class: "moves" },
    element("h4", {}, "기술"),
    ...rows,
    element("p", { class: "muted" },
      (({
        species: "이 포켓몬의 기술 목록과 대조했습니다.",
        all: "종을 몰라 전체 기술 사전과만 대조했습니다. * 표시는 종을 알아야 판단할 수 있습니다.",
        unavailable: "기술 사전이 없어 화면 글자 그대로입니다. reference update를 실행하세요.",
      })[moves.dictionary] ?? "") +
      (boosted ? " +숫자는 녹화 당시 날씨 보너스입니다." : "")),
  );
}

function candidateValues(individual) {
  const moves = individual.moves ?? {};
  const name = move => (move ? move.name ?? move.value : "");
  return {
    species: individual.species?.value ?? "",
    cp: individual.cp?.value ?? "",
    max_hp: individual.max_hp?.value ?? "",
    iv: individual.appraisals.filter(item => item.iv).at(-1)?.iv ?? null,
    fast_move: name(moves.fast),
    charged_moves: (moves.charged ?? []).map(name),
    lucky: individual.lucky?.value === true,
    background: individual.background?.value === true,
    shadow: individual.shadow?.value === true,
    shadow_evidence: individual.shadow?.evidence ?? [],
    max_state: individual.max_state?.value ?? "",
    temp_evolution: individual.state?.temp_evolution ?? null,
    max_moves: individual.max_moves ?? [],
    mega_move: individual.moves?.mega_plus ? (individual.moves.mega_plus.name ?? `${individual.moves.mega_plus.value}+`) : null,
  };
}

const MAX_STATE_NAMES = { dynamax: "다이맥스", gigantamax: "거다이맥스" };

function stateBadges(individual) {
  const state = individual.state ?? {};
  const badges = [];
  const max = individual.max_state?.value;
  if (max) badges.push([MAX_STATE_NAMES[max], "max"]);
  if (state.mega_active) {
    const name = state.screen_name && state.screen_name !== individual.species?.value ? ` (${state.screen_name})` : "";
    badges.push([`${state.temp_name ?? "메가진화"} 중${name}`, "mega"]);
  }
  if (individual.background?.value) badges.push(["배경 ◆", "plain"]);
  if (individual.shadow?.value) badges.push(["그림자 (확인 필요)", "shadow"]);
  if (state.fusion?.length) badges.push([state.fusion.some(f => f.type === "FUSE") ? "합체 가능" : "모습 변화 가능", "plain"]);
  if (!badges.length) return null;
  return element("div", { class: "badges" }, ...badges.map(([text, kind]) => element("span", { class: `badge state ${kind}` }, text)));
}

function fusionNote(state) {
  const options = (state?.fusion ?? []).filter(f => f.component);
  if (!options.length) return null;
  return element("p", { class: "muted" }, "합체: " +
    options.map(f => `${withParticle(f.component)} 합체하면 ${f.target_name ?? f.target}`).join(", "));
}

function maxMovesBlock(moves) {
  if (!moves?.length) return null;
  return element("div", { class: "moves" },
    element("h4", {}, "맥스기술"),
    ...moves.map(move => element("div", { class: "move" },
      element("span", { class: "move-role" }, ""),
      element("strong", {}, move.name),
      element("span", { class: "move-power" }, move.level ? `레벨 ${move.level}` : move.locked ? "잠김" : "-"))),
  );
}

function readForm(formNode) {
  const get = name => formNode.elements[name].value.trim();
  const iv = STATS.map(([key]) => get(`iv_${key}`));
  return {
    species: get("species") || null,
    cp: get("cp") || null,
    max_hp: get("max_hp") || null,
    iv: iv.every(value => value === "") ? null : { attack: iv[0], defense: iv[1], stamina: iv[2] },
    fast_move: get("fast_move") || null,
    charged_moves: [get("charged_1"), get("charged_2")].filter(Boolean),
    shiny: formNode.elements.shiny.checked,
    lucky: formNode.elements.lucky.checked,
    background: formNode.elements.background.checked,
    shadow: formNode.elements.shadow.checked,
    max_state: formNode.elements.max_state.value || null,
    temp_evolution: formNode.dataset.temp || null,
    max_moves: JSON.parse(formNode.dataset.maxMoves || "[]"),
    mega_move: formNode.dataset.megaMove || null,
  };
}

function previewValues(values, individual) {
  const number = value => (value === null || value === "" ? null : Number(value));
  const known = [individual.moves?.fast, ...(individual.moves?.charged ?? [])].filter(Boolean);
  const move = name => {
    if (!name) return null;
    const match = known.find(item => (item.name ?? item.value) === name);
    return { name, marker: match?.marker ?? "" };
  };
  const level = individual.level;
  return {
    species: values.species,
    shiny: values.shiny,
    lucky: values.lucky,
    background: values.background,
    shadow: values.shadow,
    max_state: values.max_state,
    cp: number(values.cp),
    max_hp: number(values.max_hp),
    iv: values.iv && Object.fromEntries(STATS.map(([key]) => [key, number(values.iv[key])])),
    level: level && {
      value: level.status === "RESOLVED_BY_CONSTRAINTS" ? level.levels[0] : null,
      candidates: level.levels,
      status: level.status,
    },
    moves: { fast: move(values.fast_move), charged: values.charged_moves.map(move) },
  };
}

function savedNote(approval, duplicate = false) {
  return element("div", { class: "save saved" },
    element("h4", {}, approval.deleted ? `${approval.code} · 박스에서 제거됨` : `${approval.code}(으)로 저장됨`),
    element("p", {},
      duplicate ? "이미 저장한 후보입니다. " : "",
      element("a", { href: `/box#${approval.pokemon_id}` }, "내 박스에서 보기")));
}

function saveBlock(individual, index) {
  if (individual.approval) return savedNote(individual.approval);
  const values = candidateValues(individual);
  const iv = values.iv ?? {};
  const numeric = { inputmode: "numeric" };
  const target = element("select", { name: "target" }, element("option", { value: "" }, "새 개체로 저장"));
  const compare = element("div", { class: "compare-area" });
  const message = element("div", { class: "save-message", role: "status" });
  const submit = element("button", { type: "submit" }, "박스에 저장");
  const formNode = element("form", { class: "save-form" },
    element("div", { class: "fields" },
      field("종", "species", values.species, { maxlength: "30" }),
      field(values.temp_evolution ? "CP (메가진화 중)" : "CP", "cp", values.cp, numeric),
      field(values.temp_evolution ? "최대 HP (메가진화 중)" : "최대 HP", "max_hp", values.max_hp, numeric),
      field("IV 공격", "iv_attack", iv.attack, numeric),
      field("IV 방어", "iv_defense", iv.defense, numeric),
      field("IV HP", "iv_stamina", iv.stamina, numeric),
      field("노말어택", "fast_move", values.fast_move, { maxlength: "30" }),
      field("스페셜어택 1", "charged_1", values.charged_moves[0], { maxlength: "30" }),
      field("스페셜어택 2", "charged_2", values.charged_moves[1], { maxlength: "30" }),
    ),
    element("div", { class: "checks" },
      checkbox("이로치 *", "shiny", false),
      checkbox("반짝반짝 포켓몬 ♣", "lucky", values.lucky),
      checkbox("배경 ◆", "background", values.background),
      checkbox("그림자", "shadow", values.shadow)),
    values.shadow_evidence.length ? element("p", { class: "muted" },
      `그림자로 보이는 단서: ${values.shadow_evidence.join(", ")}. 실제 그림자 개체 영상으로는 아직 확인하지 않은 판독이라 직접 확인하세요.`) : null,
    element("label", { class: "field wide" }, element("span", {}, "다이맥스"),
      element("select", { name: "max_state" },
        element("option", { value: "" }, "해당 없음"),
        element("option", { value: "dynamax", selected: values.max_state === "dynamax" ? "" : null }, "다이맥스"),
        element("option", { value: "gigantamax", selected: values.max_state === "gigantamax" ? "" : null }, "거다이맥스"))),
    element("p", { class: "muted" }, "반짝반짝 포켓몬·배경·다이맥스는 화면 글자로 미리 채웁니다. 이로치는 직접 체크하면 이름 뒤에 *가 붙습니다." +
      (values.mega_move ? ` 메가진화 추가 스페셜어택 ${values.mega_move}도 함께 저장합니다.` : "") +
      (values.temp_evolution ? " 메가진화 중이라 기본 모습 CP·최대 HP는 레벨로 계산해 저장하고, 화면 값은 메가진화 기록으로 따로 남깁니다." : "")),
    element("label", { class: "field wide" }, element("span", {}, "저장 방식"), target),
    compare, message, submit);
  if (values.temp_evolution) formNode.dataset.temp = values.temp_evolution;
  formNode.dataset.maxMoves = JSON.stringify(values.max_moves);
  if (values.mega_move) formNode.dataset.megaMove = values.mega_move;
  const existing = new Map();
  let acknowledge = false;

  const syncFlags = () => {
    // Updating keeps the saved shiny/lucky/background unless the user changes them.
    const entry = existing.get(target.value);
    formNode.elements.shiny.checked = entry?.shiny === true;
    formNode.elements.background.checked = entry?.background === true || values.background;
    formNode.elements.shadow.checked = entry?.shadow === true || values.shadow;
    formNode.elements.lucky.checked = entry?.lucky === true || values.lucky;
    formNode.elements.max_state.value = values.max_state || entry?.max_state || "";
  };

  const refresh = () => {
    const entry = existing.get(target.value);
    acknowledge = false;
    message.replaceChildren();
    compare.replaceChildren();
    if (entry) {
      compare.append(comparisonTable(entry, previewValues(readForm(formNode), individual)),
        element("p", { class: "muted" }, "비운 칸은 기존 값을 그대로 둡니다. 레벨과 * 표시는 저장할 때 다시 계산합니다."));
    }
    submit.textContent = entry ? `${entry.code} 갱신` : "박스에 저장";
  };

  if (values.species) {
    fetch(`/api/pokemon?species=${encodeURIComponent(values.species)}`).then(jsonResponse).then(({ pokemon }) => {
      for (const entry of pokemon) {
        existing.set(entry.id, entry);
        const same = values.iv && entry.iv && STATS.every(([key]) => entry.iv[key] === values.iv[key]);
        target.append(element("option", { value: entry.id },
          `${entry.code} 갱신 · CP ${entry.cp ?? "-"} · IV ${formatIv(entry.iv)}${same ? " · IV 일치" : ""}`));
      }
    }).catch(() => {});
  }
  target.addEventListener("change", () => { syncFlags(); refresh(); });
  formNode.addEventListener("input", event => { if (event.target !== target) refresh(); });
  formNode.addEventListener("submit", async event => {
    event.preventDefault();
    submit.disabled = true;
    message.replaceChildren();
    const entry = existing.get(target.value);
    try {
      const saved = await jsonResponse(await fetch(
        `/api/scans/${encodeURIComponent(currentScan)}/individuals/${index}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            values: readForm(formNode),
            target: entry?.id ?? null,
            expected_revision: entry?.revision ?? null,
            acknowledge,
          }),
        }));
      formNode.replaceWith(savedNote({ pokemon_id: saved.id, code: saved.code }, saved.duplicate));
    } catch (error) {
      if (error.payload?.warnings) {
        acknowledge = true;
        message.append(
          element("ul", { class: "warnings" }, ...error.payload.warnings.map(text => element("li", {}, text))),
          element("p", {}, "같은 개체가 맞으면 한 번 더 누르세요."));
        submit.textContent = "확인했고 저장";
      } else {
        message.textContent = error.message;
      }
    } finally {
      submit.disabled = false;
    }
  });
  return element("details", { class: "save", open: "" }, element("summary", {}, "박스에 저장"), formNode);
}

function card(individual, index) {
  const species = individual.species?.value ?? "종 미확인";
  const lucky = individual.lucky?.value === true;
  const appraisal = individual.appraisals.filter(item => item.evidence).at(-1);
  const [first, last] = individual.frames;
  const [start, end] = individual.seconds;
  return element("article", { class: "card" },
    element("header", {},
      element("h3", {}, `${index + 1}. ${species}${lucky ? "♣" : ""}`),
      element("span", { class: "badge" }, "자동 판독 후보"),
    ),
    stateBadges(individual),
    element("div", { class: "evidence" },
      element("a", { href: individual.detail_frame },
        element("img", { src: individual.detail_frame, loading: "lazy", alt: `${species} 상세 화면` })),
      appraisal ? element("a", { href: appraisal.evidence },
        element("img", { src: appraisal.evidence, loading: "lazy", alt: `${species} IV 판정 프레임` })) : null,
    ),
    element("dl", {},
      reading("CP", individual.cp),
      reading("최대 HP", individual.max_hp),
      reading("종(조사 문구)", individual.species),
    ),
    lucky ? element("p", { class: "muted" },
      `♣ 반짝반짝 포켓몬 (${individual.lucky.observations}장 중 ${individual.lucky.votes}장에서 확인)`) : null,
    ivBlock(individual.appraisals),
    levelBlock(individual.level),
    movesBlock(individual.moves),
    maxMovesBlock(individual.max_moves),
    fusionNote(individual.state),
    element("p", { class: "muted" }, `프레임 ${first}–${last} · ${start}–${end}초`),
    saveBlock(individual, index),
  );
}

function showAnalysis(analysis) {
  analysisSection.hidden = false;
  const messages = {
    none: "이 스캔은 아직 분석하지 않았습니다.",
    queued: "분석을 기다리는 중입니다.",
    running: "Mac에서 글자와 IV 막대를 읽고 있습니다.",
    failed: analysis.error,
    skipped: analysis.error,
  };
  if (analysis.status !== "done") {
    analysisStatus.textContent = messages[analysis.status] ?? "";
    cards.replaceChildren();
    return;
  }
  const count = analysis.individuals.length;
  analysisStatus.textContent = count
    ? `${count}개체를 찾았습니다. 자동 판독 값이므로 원본 프레임과 비교한 뒤 사용하세요.` +
      (analysis.unassigned ? ` 개체에 연결하지 못한 조사 화면 ${analysis.unassigned}개가 있습니다.` : "")
    : "읽은 값이 없습니다. 상세 화면과 조사 화면이 선명하게 찍혔는지 확인하세요.";
  cards.replaceChildren(...analysis.individuals.map(card));
}

async function monitor(id) {
  while (true) {
    const scan = await jsonResponse(await fetch(`/api/scans/${encodeURIComponent(id)}`));
    if (scan.status === "failed") {
      status.textContent = scan.error;
      return;
    }
    if (scan.status === "ready") {
      currentScan = scan.id;
      status.textContent = scan.filename;
      framesSection.hidden = false;
      framesStatus.textContent = `1초마다 추출한 ${scan.frames.length}장`;
      if (gallery.childElementCount !== scan.frames.length) showFrames(scan.frames);
      showAnalysis(scan.analysis);
      if (!["queued", "running"].includes(scan.analysis.status)) return;
    } else {
      status.textContent = scan.status === "queued"
        ? "영상 저장 완료. 워커가 작업을 시작할 때까지 기다리는 중입니다."
        : "프레임을 추출하고 있습니다.";
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

async function run(action) {
  button.disabled = true;
  try {
    await action();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    const file = input.files[0];
    if (!file) return;
    gallery.replaceChildren();
    cards.replaceChildren();
    framesSection.hidden = true;
    analysisSection.hidden = true;
    status.textContent = "영상을 Mac에 저장하고 있습니다.";
    const params = new URLSearchParams({ filename: file.name });
    const scan = await jsonResponse(await fetch(`/api/scans?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    }));
    history.replaceState(null, "", `/?scan=${encodeURIComponent(scan.id)}`);
    await monitor(scan.id);
  });
});

const previous = new URLSearchParams(location.search).get("scan");
if (previous) run(() => monitor(previous));

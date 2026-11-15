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
const STATS = [["attack", "공격"], ["defense", "방어"], ["stamina", "HP"]];

async function jsonResponse(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : "요청을 처리하지 못했습니다.");
  }
  return payload;
}

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "class") node.className = value;
    else node.setAttribute(name, value);
  }
  node.append(...children.filter(child => child !== null && child !== undefined));
  return node;
}

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
      `메가진화·베스트 파트너·특수 폼이거나 레벨 ${max}보다 높을 수도 있습니다.`],
    MISSING_INPUT: ["레벨 계산 안 함",
      `${(level.missing ?? []).map(key => MISSING[key] ?? key).join(", ")} 값이 없습니다.`],
    NO_REFERENCE: ["레벨 계산 안 함",
      "기준 데이터가 없습니다. Mac에서 reference update 명령을 실행하세요."],
    UNKNOWN_SPECIES: ["레벨 계산 안 함", "기준 데이터에서 이 종 이름을 찾지 못했습니다."],
  };
  const [title, detail] = views[level.status] ?? ["레벨", level.status];
  const notes = [detail];
  if (level.form && level.form !== "Normal") notes.push(`${level.form} 폼 종족값 기준입니다.`);
  const others = Object.entries(level.other_forms ?? {});
  if (others.length) {
    notes.push("다른 폼으로도 맞습니다: " +
      others.map(([form, found]) => `${form} 레벨 ${found.join("/")}`).join(", "));
  }
  const warn = ["CONFLICT", "AMBIGUOUS"].includes(level.status);
  return element("div", { class: warn ? "level warn" : "level" },
    element("h4", {}, title),
    element("p", {}, notes.join(" ")),
  );
}

function moveRow(label, move) {
  const boost = move.weather_boost ? ` +${move.weather_boost}` : "";
  return element("div", { class: "move" },
    element("span", { class: "move-role" }, label),
    element("strong", {}, move.value),
    element("span", { class: "move-power" }, move.power === null ? "" : `위력 ${move.power}${boost}`),
    element("span", { class: "move-votes" }, `${move.observations}장 중 ${move.votes}장`),
  );
}

function movesBlock(moves) {
  if (!moves || (!moves.fast && !moves.charged.length)) {
    return element("div", { class: "moves empty" }, element("h4", {}, "기술"),
      element("p", {}, "기술 화면을 찾지 못했습니다. 기술 목록이 보이게 1초 정도 멈춰 주세요."));
  }
  const rows = [];
  if (moves.fast) rows.push(moveRow("노말", moves.fast));
  else rows.push(element("p", { class: "muted" }, "노말 기술을 읽지 못했습니다."));
  moves.charged.forEach((move, i) => rows.push(moveRow(moves.charged.length > 1 ? `스페셜 ${i + 1}` : "스페셜", move)));
  const boosted = [moves.fast, ...moves.charged].some(move => move?.weather_boost);
  return element("div", { class: "moves" },
    element("h4", {}, "기술"),
    ...rows,
    element("p", { class: "muted" },
      "화면 글자 그대로입니다. 기술 사전 대조와 레거시·이벤트 기술 * 표시는 다음 단계입니다." +
      (boosted ? " +숫자는 녹화 당시 날씨 보너스입니다." : "")),
  );
}

function card(individual, index) {
  const species = individual.species?.value ?? "종 미확인";
  const appraisal = individual.appraisals.filter(item => item.evidence).at(-1);
  const [first, last] = individual.frames;
  const [start, end] = individual.seconds;
  return element("article", { class: "card" },
    element("header", {},
      element("h3", {}, `${index + 1}. ${species}`),
      element("span", { class: "badge" }, "자동 판독 후보"),
    ),
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
    ivBlock(individual.appraisals),
    levelBlock(individual.level),
    movesBlock(individual.moves),
    element("p", { class: "muted" }, `프레임 ${first}–${last} · ${start}–${end}초`),
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

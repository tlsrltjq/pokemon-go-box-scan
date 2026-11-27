// Event calendar on the 레이드·이벤트 page: live events as swipeable cards with how much time is
// left, a strip of the next 14 days, and the chosen day's timeline.
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
const CALENDAR_DAYS = 14;
const LONG_MS = 26 * 3_600_000;
let calendar = null;
let selectedDay = null;

const parseLocal = text => (text ? new Date(text) : null);  // "2026-12-01T22:00" is local time
const dayKey = date => `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
const shortDate = date => `${date.getMonth() + 1}/${date.getDate()}(${WEEKDAYS[date.getDay()]})`;
const clock = date => `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;

function eventCategory(event) {
  if (event.raid) return "raid";
  if (["pokemon-spotlight-hour", "community-day"].includes(event.type)) return "catch";
  if (["season", "go-pass", "go-battle-league"].includes(event.type)) return "season";
  return "event";
}

// A Korean headline from the details when there are any; the published English name stays below it.
function eventTitle(event) {
  const names = event.bosses.map(b => b.name);
  if (names.length) return `${names.slice(0, 3).join(", ")}${names.length > 3 ? ` 외 ${names.length - 3}` : ""}`;
  if (event.spotlight) return event.spotlight.name;
  if (event.spawns.length) return event.spawns.slice(0, 3).join(", ");
  return event.name;
}

function eventPokemon(event) {
  return [...event.bosses, ...(event.spotlight ? [event.spotlight] : [])].filter(p => p.dex).slice(0, 3);
}

function remaining(end, now) {
  const hours = (end - now) / 3_600_000;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}분 남음`;
  if (hours < 24) return `${Math.round(hours)}시간 남음`;
  return `${Math.floor(hours / 24)}일 남음`;
}

function spriteStack(event) {
  const list = eventPokemon(event);
  if (!list.length) return element("span", { class: `event-mark ${eventCategory(event)}` }, event.type_name.slice(0, 1));
  return element("span", { class: "sprite-stack" }, ...list.map(p => avatar({ ...p, name: p.species ?? p.name }, "sm")));
}

function linked(event, attrs, ...children) {
  return event.link ? element("a", { ...attrs, href: event.link, target: "_blank", rel: "noopener", title: event.name }, ...children)
    : element("div", { ...attrs, title: event.name }, ...children);
}

function liveCard(event, now) {
  const start = parseLocal(event.start);
  const end = parseLocal(event.end);
  const share = start && end ? Math.min(1, Math.max(0, (now - start) / (end - start))) : null;
  return linked(event, { class: `live-card ${eventCategory(event)}` },
    element("div", { class: "live-top" }, spriteStack(event), element("span", { class: "event-kind" }, event.type_name)),
    element("strong", { class: "live-title" }, eventTitle(event)),
    element("span", { class: "live-name" }, event.name),
    share === null ? null : element("span", { class: "progress", style: `--done:${Math.round(share * 100)}%` }),
    element("span", { class: "live-when" }, end ? `${remaining(end, now)} · ${shortDate(end)} ${clock(end)}까지` : "진행 중"));
}

function dayEvents(day) {
  const startOfDay = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  const endOfDay = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
  return calendar.events.filter(event => {
    const start = parseLocal(event.start);
    const end = parseLocal(event.end);
    if (!start || event.status === "ended") return false;
    const long = end && end - start > LONG_MS;
    return long ? start >= startOfDay && start < endOfDay : start < endOfDay && (end ?? start) >= startOfDay;
  }).sort((a, b) => (a.start < b.start ? -1 : 1));
}

function dayPill(day, today, events) {
  const key = dayKey(day);
  const categories = [...new Set(events.map(eventCategory))].slice(0, 3);
  const pill = element("button", { type: "button", class: `day-pill${key === selectedDay ? " selected" : ""}${key === dayKey(today) ? " today" : ""}`,
    "aria-pressed": String(key === selectedDay), "aria-label": `${day.getMonth() + 1}월 ${day.getDate()}일 일정 ${events.length}개` },
    element("span", { class: "pill-weekday" }, key === dayKey(today) ? "오늘" : WEEKDAYS[day.getDay()]),
    element("span", { class: "pill-date" }, String(day.getDate())),
    element("span", { class: "pill-dots" }, ...categories.map(c => element("i", { class: c }))));
  pill.addEventListener("click", () => { selectedDay = key; showCalendar(); });
  return pill;
}

function timelineRow(event) {
  const start = parseLocal(event.start);
  const end = parseLocal(event.end);
  const long = end && end - start > LONG_MS;
  const time = long ? ["시작", clock(start)] : [clock(start), end ? clock(end) : ""];
  return element("li", { class: "timeline-row" },
    element("span", { class: "timeline-time" }, element("strong", {}, time[0]), element("span", {}, time[1])),
    linked(event, { class: `timeline-card ${eventCategory(event)}` }, spriteStack(event),
      element("span", { class: "timeline-text" },
        element("span", { class: "event-kind" }, event.type_name + (long && end ? ` · ${shortDate(end)}까지` : "")),
        element("strong", {}, eventTitle(event)),
        element("span", { class: "live-name" }, event.name + (event.spotlight?.bonus ? ` · ${event.spotlight.bonus}` : "")))));
}

function showCalendar() {
  const source = document.querySelector("#calendar-source");
  const nowBox = document.querySelector("#calendar-now");
  const strip = document.querySelector("#calendar-strip");
  const dayBox = document.querySelector("#calendar-day");
  const later = document.querySelector("#calendar-later");
  if (!calendar?.version) {
    source.textContent = "저장된 이벤트 달력이 없습니다. 위의 갱신 버튼을 누르면 보스 목록과 함께 받습니다.";
    [nowBox, strip, dayBox, later].forEach(node => node.replaceChildren());
    return;
  }
  const now = new Date();
  source.replaceChildren("출처 ", element("a", { href: calendar.source_home, target: "_blank", rel: "noopener" }, calendar.credit),
    ` · ${new Date(calendar.fetched_at).toLocaleDateString("ko-KR")} 받음`);

  const live = calendar.events.filter(e => e.status === "live")
    .sort((a, b) => (b.raid - a.raid) || ((a.end ?? "9") < (b.end ?? "9") ? -1 : 1));
  nowBox.replaceChildren(element("div", { class: "row-head" }, element("h3", {}, "지금 진행 중"), element("span", { class: "count" }, String(live.length))),
    live.length ? element("div", { class: "live-rail" }, ...live.map(e => liveCard(e, now)))
      : element("p", { class: "muted" }, "지금 진행 중인 이벤트가 없습니다."));

  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Array.from({ length: CALENDAR_DAYS }, (_, i) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + i));
  const perDay = new Map(days.map(d => [dayKey(d), dayEvents(d)]));
  if (!selectedDay || !perDay.has(selectedDay)) {
    selectedDay = dayKey(days.find(d => perDay.get(dayKey(d)).length) ?? today);
  }
  strip.replaceChildren(element("div", { class: "row-head" }, element("h3", {}, "다가오는 일정"),
    element("span", { class: "muted" }, `${days[0].getMonth() + 1}/${days[0].getDate()} – ${days.at(-1).getMonth() + 1}/${days.at(-1).getDate()}`)),
    element("div", { class: "day-strip", role: "group", "aria-label": "날짜 고르기" }, ...days.map(d => dayPill(d, today, perDay.get(dayKey(d))))));
  const stripRow = strip.querySelector(".day-strip");
  const pill = stripRow.querySelector(".selected");
  if (pill) stripRow.scrollLeft = pill.offsetLeft - stripRow.offsetLeft - (stripRow.clientWidth - pill.offsetWidth) / 2;  // keep the chosen day in view
  const chosen = days.find(d => dayKey(d) === selectedDay);
  const items = perDay.get(selectedDay);
  dayBox.replaceChildren(element("p", { class: "day-title" }, `${chosen.getMonth() + 1}월 ${chosen.getDate()}일 ${WEEKDAYS[chosen.getDay()]}요일`),
    items.length ? element("ol", { class: "timeline" }, ...items.map(timelineRow))
      : element("p", { class: "muted" }, "이날 새로 시작하는 일정은 없습니다. 이어지는 이벤트는 위 진행 중 카드에 있습니다."));

  const lastDay = new Date(today.getFullYear(), today.getMonth(), today.getDate() + CALENDAR_DAYS);
  const after = calendar.events.filter(e => e.status === "upcoming" && parseLocal(e.start) >= lastDay);
  later.replaceChildren(...(after.length ? [element("details", { class: "cal-later" },
    element("summary", {}, `2주 뒤 일정 ${after.length}개`),
    element("ol", { class: "timeline" }, ...after.map(e => {
      const row = timelineRow(e);
      row.querySelector(".timeline-time strong").textContent = shortDate(parseLocal(e.start));
      return row;
    })))] : []));
}

// The raid-rotation event a boss card belongs to: "12/1(화) 22:00까지 · 4일 남음".
function scheduleFor(boss) {
  if (!calendar?.events || boss.status !== "ok") return null;
  const match = calendar.events.find(event => event.status !== "ended" && event.raid && event.bosses.some(b =>
    b.species === boss.species && (b.temp ?? null) === (boss.temp ?? null) && b.shadow === Boolean(boss.shadow)));
  if (!match) return null;
  const end = parseLocal(match.end);
  const start = parseLocal(match.start);
  return match.status === "upcoming" && start ? `${shortDate(start)} ${clock(start)}부터 (${match.type_name})`
    : end ? `${remaining(end, new Date())} · ${shortDate(end)} ${clock(end)}까지` : null;
}

function markBossSchedules(bosses) {
  for (const boss of bosses) {
    const card = document.querySelector(`#boss-${boss.kind}-${boss.index}`);
    const text = card && scheduleFor(boss);
    card?.querySelector(".boss-schedule")?.remove();
    if (text) card.querySelector(".boss-head")?.after(element("p", { class: "boss-schedule" }, text));
  }
}

async function loadCalendar() {
  try {
    calendar = await jsonResponse(await fetch("/api/events"));
  } catch (error) {
    calendar = null;
    document.querySelector("#calendar-source").textContent = error.message;
    return;
  }
  showCalendar();
}

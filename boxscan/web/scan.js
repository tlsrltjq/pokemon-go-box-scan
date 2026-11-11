const form = document.querySelector("#upload");
const input = document.querySelector("#video");
const button = form.querySelector("button");
const status = document.querySelector("#status");
const gallery = document.querySelector("#frames");

async function jsonResponse(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : "요청을 처리하지 못했습니다.");
  }
  return payload;
}

function showFrames(frames) {
  gallery.replaceChildren(...frames.map((url, index) => {
    const figure = document.createElement("figure");
    const link = document.createElement("a");
    link.href = url;
    const image = document.createElement("img");
    image.src = url;
    image.loading = "lazy";
    image.alt = `추출 프레임 ${index + 1}`;
    const caption = document.createElement("figcaption");
    caption.textContent = `프레임 ${index + 1}`;
    link.append(image);
    figure.append(link, caption);
    return figure;
  }));
}

async function monitor(id) {
  while (true) {
    const scan = await jsonResponse(await fetch(`/api/scans/${encodeURIComponent(id)}`));
    if (scan.status === "ready") {
      showFrames(scan.frames);
      status.textContent = `${scan.filename} · 프레임 ${scan.frames.length}장`;
      return;
    }
    if (scan.status === "failed") {
      status.textContent = scan.error;
      return;
    }
    status.textContent = scan.status === "queued"
      ? "영상 저장 완료. 워커가 작업을 시작할 때까지 기다리는 중입니다."
      : "프레임을 추출하고 있습니다.";
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

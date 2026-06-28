const videoInput = document.getElementById("video-input");
const player = document.getElementById("player");
const curTime = document.getElementById("cur-time");
const setIn = document.getElementById("set-in");
const setOut = document.getElementById("set-out");
const inLabel = document.getElementById("in-label");
const outLabel = document.getElementById("out-label");
const durLabel = document.getElementById("dur-label");
const rounds = document.getElementById("rounds");
const roundsLabel = document.getElementById("rounds-label");
const engineSel = document.getElementById("engine");
const engineHint = document.getElementById("engine-hint");
const voiceRow = document.getElementById("voice-row");
const voice = document.getElementById("voice");
const keepBg = document.getElementById("keep-bg");
const cfgStatus = document.getElementById("cfg-status");
const processBtn = document.getElementById("process-btn");
const taskStatus = document.getElementById("task-status");
const review = document.getElementById("review");
const segList = document.getElementById("seg-list");
const confirmBtn = document.getElementById("confirm-btn");
const historyList = document.getElementById("history");

let inPoint = 0;
let outPoint = 0;
let currentTaskId = null;

const STAGE_LABELS = {
  queued: "排队中...",
  trimming: "裁剪片段...",
  extracting_audio: "提取音频...",
  transcribing: "语音识别中",
  awaiting_review: "等待校对",
  degrading: "鬼畜翻译中",
  synthesizing: "合成配音中",
  mixing: "混音中",
  burning: "烧录字幕中",
  done: "完成",
  error: "出错",
};

videoInput.addEventListener("change", () => {
  const file = videoInput.files[0];
  if (!file) return;
  player.src = URL.createObjectURL(file);
  inPoint = 0;
  outPoint = 0;
  updateClipLabels();
  processBtn.disabled = false;
});

player.addEventListener("loadedmetadata", () => {
  outPoint = player.duration || 0;
  updateClipLabels();
});

player.addEventListener("timeupdate", () => {
  curTime.textContent = player.currentTime.toFixed(2);
});

setIn.addEventListener("click", () => {
  inPoint = player.currentTime;
  if (outPoint <= inPoint) outPoint = player.duration || inPoint;
  updateClipLabels();
});

setOut.addEventListener("click", () => {
  outPoint = player.currentTime;
  if (outPoint <= inPoint) inPoint = 0;
  updateClipLabels();
});

function updateClipLabels() {
  inLabel.textContent = inPoint.toFixed(2);
  outLabel.textContent = outPoint.toFixed(2);
  durLabel.textContent = Math.max(outPoint - inPoint, 0).toFixed(2);
}

rounds.addEventListener("input", () => {
  roundsLabel.textContent = rounds.value;
});

// ---- 配音引擎：按可用性点亮，克隆引擎隐藏预设音色 ----
let engines = [];

async function loadEngines() {
  const res = await fetch("/api/dub/engines");
  const data = await res.json();
  engines = data.engines || [];
  const prev = engineSel.value;
  engineSel.innerHTML = "";
  engines.forEach((e) => {
    const opt = document.createElement("option");
    opt.value = e.id;
    opt.textContent = e.available ? e.name : `${e.name}（不可用）`;
    opt.disabled = !e.available;
    engineSel.appendChild(opt);
  });
  // 尽量保留原选择，否则选第一个可用的
  const stillOk = engines.find((e) => e.id === prev && e.available);
  engineSel.value = stillOk ? prev : (engines.find((e) => e.available) || {}).id || "edge";
  updateEngineHint();
}

function updateEngineHint() {
  const e = engines.find((x) => x.id === engineSel.value);
  if (!e) return;
  if (e.supports_clone) {
    voiceRow.style.display = "none";
    engineHint.textContent = "🎙 复刻原片中每句说话人的音色";
  } else {
    voiceRow.style.display = "";
    engineHint.textContent = "预设标准音色，不复刻原声";
  }
}

engineSel.addEventListener("change", updateEngineHint);

// 保存云端 Key 后重新拉取可用性
document.querySelectorAll(".save-cfg").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const provider = btn.dataset.provider;
    const inputs = document.querySelectorAll(`input[data-provider="${provider}"]`);
    const fields = {};
    inputs.forEach((inp) => { fields[inp.dataset.field] = inp.value.trim(); });
    cfgStatus.textContent = "保存中...";
    const res = await fetch("/api/dub/voice-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, fields }),
    });
    const data = await res.json();
    if (data.error) {
      cfgStatus.innerHTML = `<span class="error">${data.error}</span>`;
      return;
    }
    cfgStatus.textContent = `已保存 ${provider} 配置`;
    inputs.forEach((inp) => { inp.value = ""; });
    await loadEngines();
  });
});

loadEngines();

processBtn.addEventListener("click", async () => {
  if (!videoInput.files[0]) return;
  if (outPoint - inPoint < 0.5) {
    taskStatus.innerHTML = `<span class="error">请先用「设为入点/出点」截取一段（至少 0.5 秒）</span>`;
    return;
  }

  processBtn.disabled = true;
  review.style.display = "none";
  taskStatus.textContent = "正在上传...";

  const formData = new FormData();
  formData.append("video", videoInput.files[0]);
  formData.append("start", inPoint.toFixed(3));
  formData.append("end", outPoint.toFixed(3));
  formData.append("rounds", rounds.value);
  formData.append("engine", engineSel.value);
  formData.append("voice", voice.value);
  formData.append("keep_bg", keepBg.checked ? "true" : "false");

  const res = await fetch("/api/dub/process", { method: "POST", body: formData });
  const data = await res.json();
  if (data.error) {
    taskStatus.innerHTML = `<span class="error">${data.error}</span>`;
    processBtn.disabled = false;
    return;
  }
  currentTaskId = data.task_id;
  pollStatus();
});

function pollStatus() {
  const timer = setInterval(async () => {
    const res = await fetch("/api/dub/status/" + currentTaskId);
    const task = await res.json();
    renderTaskStatus(task);

    if (task.stage === "awaiting_review") {
      clearInterval(timer);
      showReview(task.segments || []);
      return;
    }
    if (task.stage === "done" || task.stage === "error") {
      clearInterval(timer);
      processBtn.disabled = false;
      if (task.stage === "done") addHistoryEntry(task);
    }
  }, 1000);
}

function renderTaskStatus(task) {
  const label = STAGE_LABELS[task.stage] || task.stage;
  if (task.stage === "error") {
    taskStatus.innerHTML = `<span class="error">${task.error}</span>`;
    return;
  }
  const percent = typeof task.percent === "number" ? ` ${task.percent.toFixed(1)}%` : "";
  taskStatus.textContent = `${label}${percent}`;
}

function showReview(segments) {
  segList.innerHTML = "";
  segments.forEach((seg) => {
    const row = document.createElement("div");
    row.className = "seg";
    const time = document.createElement("div");
    time.className = "time";
    time.textContent = `${seg.start.toFixed(1)}–${seg.end.toFixed(1)}s`;
    const ta = document.createElement("textarea");
    ta.value = seg.text;
    ta.dataset.start = seg.start;
    ta.dataset.end = seg.end;
    row.appendChild(time);
    row.appendChild(ta);
    segList.appendChild(row);
  });
  review.style.display = "block";
}

confirmBtn.addEventListener("click", async () => {
  const segments = Array.from(segList.querySelectorAll("textarea")).map((ta) => ({
    start: parseFloat(ta.dataset.start),
    end: parseFloat(ta.dataset.end),
    text: ta.value,
  }));

  confirmBtn.disabled = true;
  const res = await fetch("/api/dub/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: currentTaskId, segments }),
  });
  const data = await res.json();
  if (data.error) {
    taskStatus.innerHTML = `<span class="error">${data.error}</span>`;
    confirmBtn.disabled = false;
    return;
  }
  review.style.display = "none";
  confirmBtn.disabled = false;
  pollStatus();
});

function addHistoryEntry(task) {
  const li = document.createElement("li");
  const filename = task.output_path.split(/[\\/]/).pop();
  li.innerHTML = `鬼畜配音完成 - <a href="/dubbed_videos/${encodeURIComponent(filename)}" target="_blank">播放/下载</a>
    &nbsp;<a href="#" class="open-loc">打开文件位置</a>`;
  li.querySelector(".open-loc").addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch("/api/open-location", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: task.output_path }),
    });
  });
  historyList.prepend(li);
}

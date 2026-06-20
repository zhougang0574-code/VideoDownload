const configForm = document.getElementById("config-form");
const deeplKeyInput = document.getElementById("deepl-key-input");
const configStatus = document.getElementById("config-status");

const processForm = document.getElementById("process-form");
const videoInput = document.getElementById("video-input");
const processBtn = document.getElementById("process-btn");
const taskStatus = document.getElementById("task-status");
const historyList = document.getElementById("history");

const STAGE_LABELS = {
  queued: "排队中...",
  extracting_audio: "提取音频...",
  transcribing: "语音识别中",
  translating: "翻译字幕中",
  burning_subtitles: "烧录字幕中",
  done: "完成",
  error: "出错",
};

async function init() {
  const res = await fetch("/api/subtitle/deepl-key");
  const data = await res.json();
  if (data.configured) {
    configStatus.textContent = "已配置 DeepL Key";
  }
}

configForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const apiKey = deeplKeyInput.value.trim();
  if (!apiKey) return;

  const res = await fetch("/api/subtitle/deepl-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  const data = await res.json();
  if (data.error) {
    configStatus.innerHTML = `<span class="error">${data.error}</span>`;
    return;
  }
  configStatus.textContent = "已保存，之后会优先用 DeepL 翻译";
  deeplKeyInput.value = "";
});

processForm.addEventListener("submit", (e) => {
  e.preventDefault();
  startProcess();
});

async function startProcess() {
  if (!videoInput.files[0]) return;

  processBtn.disabled = true;
  taskStatus.innerHTML = "正在提交...";

  const formData = new FormData();
  formData.append("video", videoInput.files[0]);

  const res = await fetch("/api/subtitle/process", { method: "POST", body: formData });
  const data = await res.json();

  if (data.error) {
    taskStatus.innerHTML = `<span class="error">${data.error}</span>`;
    processBtn.disabled = false;
    return;
  }

  pollStatus(data.task_id);
}

function pollStatus(taskId) {
  const timer = setInterval(async () => {
    const res = await fetch("/api/subtitle/status/" + taskId);
    const task = await res.json();
    renderTaskStatus(task);

    if (task.stage === "done" || task.stage === "error") {
      clearInterval(timer);
      processBtn.disabled = false;
      if (task.stage === "done") {
        addHistoryEntry(task);
        processForm.reset();
      }
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

function addHistoryEntry(task) {
  const li = document.createElement("li");
  const filename = task.output_path.split(/[\\/]/).pop();
  li.innerHTML = `字幕烧录完成 - <a href="/subtitled_videos/${encodeURIComponent(filename)}" target="_blank">打开文件</a>`;
  historyList.prepend(li);
}

init();

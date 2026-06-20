const configSection = document.getElementById("config-section");
const configForm = document.getElementById("config-form");
const clientIdInput = document.getElementById("client-id-input");
const clientSecretInput = document.getElementById("client-secret-input");
const configStatus = document.getElementById("config-status");

const loginSection = document.getElementById("login-section");
const loginMessage = document.getElementById("login-message");
const loginBtn = document.getElementById("login-btn");
const qrcodeImg = document.getElementById("qrcode-img");
const userCodeEl = document.getElementById("user-code");
const loginHint = document.getElementById("login-hint");

const downloadBlock = document.getElementById("download-block");
const urlInput = document.getElementById("url-input");
const downloadBtn = document.getElementById("download-btn");
const taskStatus = document.getElementById("task-status");
const historyList = document.getElementById("history");

const uploadBlock = document.getElementById("upload-block");
const uploadForm = document.getElementById("upload-form");
const videoInput = document.getElementById("video-input");
const titleInput = document.getElementById("title-input");
const descInput = document.getElementById("desc-input");
const tagsInput = document.getElementById("tags-input");
const privacyInput = document.getElementById("privacy-input");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const uploadHistoryList = document.getElementById("upload-history");

const STAGE_LABELS = {
  queued: "排队中...",
  downloading: "下载中",
  done: "完成",
  error: "出错",
};

const UPLOAD_STAGE_LABELS = {
  queued: "排队中...",
  uploading: "上传中",
  done: "完成",
  error: "出错",
};

let loginPollTimer = null;

async function init() {
  const configRes = await fetch("/api/youtube/config/status");
  const configData = await configRes.json();

  if (!configData.configured) {
    configSection.style.display = "block";
    downloadBlock.style.display = "none";
    return;
  }

  configSection.style.display = "none";
  downloadBlock.style.display = "block";

  const sessionRes = await fetch("/api/youtube/session");
  const sessionData = await sessionRes.json();
  if (sessionData.logged_in) {
    loginSection.style.display = "none";
    uploadBlock.style.display = "block";
  } else {
    loginSection.style.display = "block";
    loginBtn.style.display = "inline-block";
    loginMessage.textContent = "尚未登录";
    uploadBlock.style.display = "none";
  }
}

configForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/api/youtube/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: clientIdInput.value.trim(),
      client_secret: clientSecretInput.value.trim(),
    }),
  });
  const data = await res.json();
  if (data.error) {
    configStatus.innerHTML = `<span class="error">${data.error}</span>`;
    return;
  }
  init();
});

loginBtn.addEventListener("click", async () => {
  loginBtn.style.display = "none";
  loginMessage.textContent = "正在获取设备码...";
  await fetch("/api/youtube/login/start", { method: "POST" });
  pollLoginStatus();
});

function pollLoginStatus() {
  if (loginPollTimer) clearInterval(loginPollTimer);
  loginPollTimer = setInterval(async () => {
    const res = await fetch("/api/youtube/login/status");
    const state = await res.json();
    loginMessage.textContent = state.message || "";

    if (state.status === "waiting") {
      qrcodeImg.src = "/static/youtube_qrcode.png?t=" + Date.now();
      qrcodeImg.style.display = "block";
      userCodeEl.textContent = state.user_code || "";
      userCodeEl.style.display = "inline-block";
      loginHint.style.display = "block";
    }

    if (state.status === "success") {
      clearInterval(loginPollTimer);
      qrcodeImg.style.display = "none";
      userCodeEl.style.display = "none";
      loginHint.style.display = "none";
      loginSection.style.display = "none";
      uploadBlock.style.display = "block";
    } else if (["denied", "expired", "error"].includes(state.status)) {
      clearInterval(loginPollTimer);
      qrcodeImg.style.display = "none";
      userCodeEl.style.display = "none";
      loginHint.style.display = "none";
      loginBtn.textContent = "重新登录";
      loginBtn.style.display = "inline-block";
    }
  }, 1500);
}

downloadBtn.addEventListener("click", () => {
  const url = urlInput.value.trim();
  if (!url) return;
  startDownload(url);
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") downloadBtn.click();
});

async function startDownload(url) {
  downloadBtn.disabled = true;
  taskStatus.innerHTML = "正在提交...";

  const res = await fetch("/api/youtube/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await res.json();

  if (data.error) {
    taskStatus.innerHTML = `<span class="error">${data.error}</span>`;
    downloadBtn.disabled = false;
    return;
  }

  pollDownloadStatus(data.task_id);
}

function pollDownloadStatus(taskId) {
  const timer = setInterval(async () => {
    const res = await fetch("/api/youtube/download/status/" + taskId);
    const task = await res.json();
    renderTaskStatus(task);

    if (task.stage === "done" || task.stage === "error") {
      clearInterval(timer);
      downloadBtn.disabled = false;
      if (task.stage === "done") {
        addHistoryEntry(task);
        urlInput.value = "";
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
  const title = task.title ? `《${task.title}》` : "";
  const percent = typeof task.percent === "number" ? ` ${task.percent.toFixed(1)}%` : "";
  taskStatus.textContent = `${title}${label}${percent}`;
}

function addHistoryEntry(task) {
  const li = document.createElement("li");
  const filename = task.output_path.split(/[\\/]/).pop();
  li.innerHTML = `《${task.title}》下载完成 - <a href="/youtube_downloads/${encodeURIComponent(filename)}" target="_blank">打开文件</a>`;
  historyList.prepend(li);
}

uploadForm.addEventListener("submit", (e) => {
  e.preventDefault();
  startUpload();
});

async function startUpload() {
  if (!videoInput.files[0]) return;

  uploadBtn.disabled = true;
  uploadStatus.innerHTML = "正在提交...";

  const formData = new FormData();
  formData.append("video", videoInput.files[0]);
  formData.append("title", titleInput.value.trim());
  formData.append("desc", descInput.value.trim());
  formData.append("tags", tagsInput.value.trim());
  formData.append("privacy", privacyInput.value);

  const res = await fetch("/api/youtube/upload", { method: "POST", body: formData });
  const data = await res.json();

  if (data.error) {
    uploadStatus.innerHTML = `<span class="error">${data.error}</span>`;
    uploadBtn.disabled = false;
    return;
  }

  pollUploadStatus(data.task_id, titleInput.value.trim());
}

function pollUploadStatus(taskId, title) {
  const timer = setInterval(async () => {
    const res = await fetch("/api/youtube/upload/status/" + taskId);
    const task = await res.json();
    renderUploadStatus(task);

    if (task.stage === "done" || task.stage === "error") {
      clearInterval(timer);
      uploadBtn.disabled = false;
      if (task.stage === "done") {
        addUploadHistoryEntry(title, task);
        uploadForm.reset();
      }
    }
  }, 1000);
}

function renderUploadStatus(task) {
  const label = UPLOAD_STAGE_LABELS[task.stage] || task.stage;
  if (task.stage === "error") {
    uploadStatus.innerHTML = `<span class="error">${task.error}</span>`;
    return;
  }
  const percent = typeof task.percent === "number" ? ` ${task.percent.toFixed(1)}%` : "";
  uploadStatus.textContent = `${label}${percent}`;
}

function addUploadHistoryEntry(title, task) {
  const li = document.createElement("li");
  li.innerHTML = `《${title}》上传成功 - <a href="https://youtu.be/${task.video_id}" target="_blank">查看</a>`;
  uploadHistoryList.prepend(li);
}

init();

const loginSection = document.getElementById("login-section");
const loginMessage = document.getElementById("login-message");
const loginBtn = document.getElementById("login-btn");
const qrcodeImg = document.getElementById("qrcode-img");

const downloadSection = document.getElementById("download-section");
const urlInput = document.getElementById("url-input");
const downloadBtn = document.getElementById("download-btn");
const taskStatus = document.getElementById("task-status");
const historyList = document.getElementById("history");

const STAGE_LABELS = {
  queued: "排队中...",
  parsing: "解析链接...",
  fetching_info: "获取视频信息...",
  downloading_video: "下载视频流",
  downloading_audio: "下载音频流",
  merging: "合并音视频...",
  done: "完成",
  error: "出错",
};

const uploadForm = document.getElementById("upload-form");
const videoInput = document.getElementById("video-input");
const coverInput = document.getElementById("cover-input");
const titleInput = document.getElementById("title-input");
const descInput = document.getElementById("desc-input");
const tidInput = document.getElementById("tid-input");
const tagInput = document.getElementById("tag-input");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const uploadHistoryList = document.getElementById("upload-history");

const UPLOAD_STAGE_LABELS = {
  queued: "排队中...",
  uploading_video: "上传视频流",
  extracting_cover: "自动截取封面...",
  uploading_cover: "上传封面",
  submitting: "提交投稿...",
  done: "完成",
  error: "出错",
};

let loginPollTimer = null;

async function init() {
  const res = await fetch("/api/bilibili/session");
  const data = await res.json();
  if (data.logged_in) {
    showDownloadSection();
  } else {
    showLoginButton();
  }
}

function showLoginButton() {
  loginMessage.textContent = "尚未登录";
  loginBtn.style.display = "inline-block";
}

function showDownloadSection() {
  loginSection.style.display = "none";
  downloadSection.style.display = "block";
}

loginBtn.addEventListener("click", async () => {
  loginBtn.style.display = "none";
  loginMessage.textContent = "正在生成二维码...";
  await fetch("/api/bilibili/login/start", { method: "POST" });
  pollLoginStatus();
});

function pollLoginStatus() {
  if (loginPollTimer) clearInterval(loginPollTimer);
  loginPollTimer = setInterval(async () => {
    const res = await fetch("/api/bilibili/login/status");
    const state = await res.json();
    loginMessage.textContent = state.message || "";

    if (state.status === "waiting" || state.status === "scanned") {
      qrcodeImg.src = "/static/qrcode.png?t=" + Date.now();
      qrcodeImg.style.display = "block";
    }

    if (state.status === "success") {
      clearInterval(loginPollTimer);
      showDownloadSection();
    } else if (["expired", "timeout", "error"].includes(state.status)) {
      clearInterval(loginPollTimer);
      qrcodeImg.style.display = "none";
      loginBtn.textContent = "重新获取二维码";
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

  const res = await fetch("/api/bilibili/download", {
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
    const res = await fetch("/api/bilibili/download/status/" + taskId);
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
  li.innerHTML = `《${task.title}》下载完成 - <a href="/downloads/${encodeURIComponent(filename)}" target="_blank">打开文件</a>`;
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
  if (coverInput.files[0]) formData.append("cover", coverInput.files[0]);
  formData.append("title", titleInput.value.trim());
  formData.append("desc", descInput.value.trim());
  formData.append("tid", tidInput.value.trim());
  formData.append("tag", tagInput.value.trim());

  const res = await fetch("/api/bilibili/upload", { method: "POST", body: formData });
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
    const res = await fetch("/api/bilibili/upload/status/" + taskId);
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
  if (task.bvid) {
    li.innerHTML = `《${title}》投稿成功（审核中）- <a href="https://www.bilibili.com/video/${task.bvid}" target="_blank">查看</a>`;
  } else {
    li.textContent = `《${title}》投稿成功（审核中）`;
  }
  uploadHistoryList.prepend(li);
}

init();

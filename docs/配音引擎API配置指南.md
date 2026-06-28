# 配音引擎 API Key 申请与配置指南

鬼畜配音的「声音克隆」引擎里，云端三家（阿里云 CosyVoice、讯飞、火山引擎）需要
各自的 API 凭据。本文说明**怎么申请、填哪些字段、在哪里配置**。本地 XTTS 不需要
账号，见文末。

> 配置入口：启动应用 → 首页「鬼畜配音」→ 页面下方「**云端克隆引擎 Key 配置**」
> 折叠区，填对应字段点「保存」。保存后刷新，下拉里该引擎会从「不可用」变为可选。
> 凭据存到项目根目录 `voice_config.json`（已 gitignore，不会提交）。

---

## 通用前置

- 三家都要求**注册账号 + 实名认证**（国内云服务规定），实名一般几分钟到几小时。
- 免费的是「新用户额度 / 试用额度」，用完需付费；申请时留意各家**计费与免费额度**说明。
- 声音克隆通常要**单独开通**对应的语音服务/模型，不是注册就自动有。
- 下列控制台路径以撰写时为准，各家界面可能调整；找不到时在控制台搜「语音合成 / 声音复刻 / voice clone」。

---

## 1. 阿里云百炼 CosyVoice

**应用里需要的字段**：`api_key`

**申请步骤**
1. 注册并登录阿里云账号，完成实名认证：<https://www.aliyun.com>
2. 进入「百炼」大模型平台（DashScope）控制台：<https://bailian.console.aliyun.com>
3. 开通百炼服务（首次进入会引导开通；注意其免费额度政策）。
4. 在控制台右上角「API-KEY」/「我的 API-KEY」里**创建 API Key**，复制保存。
5. 如需声音复刻，确认已开通 CosyVoice 语音合成/复刻相关模型能力。

**填入**：把创建的 API Key 填到应用「阿里云 CosyVoice」的 `API Key` 框，保存。

**备注**：DashScope 的声音复刻通常是「上传参考音注册出 voice_id 再合成」，且参考音
可能要求可访问的 URL。这部分联调时按官方文档在 `CosyVoiceEngine.synth()` 里补全。

---

## 2. 讯飞开放平台 语音复刻

**应用里需要的字段**：`app_id`、`api_key`、`api_secret`

**申请步骤**
1. 注册并登录讯飞开放平台，完成实名认证：<https://www.xfyun.cn>
2. 进入控制台：<https://console.xfyun.cn>
3. 「创建应用」（填应用名等），创建后进入该应用。
4. 在应用详情 / 对应能力（语音合成 → 语音复刻 / 一句话复刻）页面，找到：
   - **APPID**
   - **APIKey**
   - **APISecret**
5. 在「语音复刻」能力页开通该服务（注意试用额度与是否需购买）。

**填入**：把 APPID/APIKey/APISecret 分别填到应用「讯飞 语音复刻」的三个框，保存。

**备注**：讯飞 WebAPI 用 APPID + APIKey + APISecret 做 HMAC 鉴权；复刻一般要先训练
得到音色 id 再合成。联调时在 `IflytekEngine.synth()` 里补全。

---

## 3. 火山引擎 / 豆包 声音复刻

**应用里需要的字段**：`app_id`、`access_token`、`cluster`

**申请步骤**
1. 注册并登录火山引擎，完成实名认证：<https://www.volcengine.com>
2. 进入「语音技术」控制台：<https://console.volcengine.com/speech>
3. 开通「语音合成 / 声音复刻」相关服务（注意免费额度与计费）。
4. 创建应用 / 获取凭据，记下：
   - **AppID**
   - **Access Token**（访问令牌）
   - **Cluster**（集群标识，按所用服务文档填，如声音复刻对应的 cluster 值）

**填入**：把 AppID/Access Token/Cluster 填到应用「火山引擎 声音复刻」的三个框，保存。

**备注**：火山 TTS 用 AppID + Access Token 鉴权；声音复刻先训练得 speaker_id 再合成。
联调时在 `VolcEngine.synth()` 里补全。

---

## 4. 本地 XTTS-v2（不需账号，免费无限）

不依赖任何云，装一个独立的 Python 环境即可，凭本机 GPU（已检测到 RTX 4060 Ti）跑。

在**项目根目录**执行：

```bat
python -m venv tts_clone_venv
tts_clone_venv\Scripts\python -m pip install --upgrade pip
tts_clone_venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
tts_clone_venv\Scripts\python -m pip install coqui-tts
```

- 首次合成会自动下载 XTTS-v2 模型（约 1.8GB）。
- 装好后应用里「本地 XTTS-v2」会自动从「不可用」变为可选，无需填任何 Key。
- 详细说明见 `tts_clone/xtts_worker.py` 文件头注释。

---

## 5. 配置存储与安全

- 云端凭据保存在项目根目录 `voice_config.json`，结构示例：
  ```json
  {
    "cosyvoice": { "api_key": "..." },
    "iflytek": { "app_id": "...", "api_key": "...", "api_secret": "..." },
    "volc": { "app_id": "...", "access_token": "...", "cluster": "..." }
  }
  ```
- 该文件已加入 `.gitignore`，**不会被提交**。请勿手动把它提交到仓库或分享给他人。
- 想更换某家凭据：在页面重新填写保存即可覆盖。

## 6. 配置完怎么验证

1. 填好 Key 保存 → 页面刷新 → 「配音引擎」下拉里对应项点亮。
2. 上传一小段（几秒）有清晰人声的视频，截取 → 选该克隆引擎 → 开始处理。
3. 看成品里配音音色是否贴近原片说话人。
4. 若报错，把错误信息发出来，按报错调整对应引擎的 `synth()`（网络调用都集中在
   那一个方法里，已标 `# 待验证`）。各引擎与改动位置见 `docs/鬼畜配音功能说明.md` §8。

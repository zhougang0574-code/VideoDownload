"""可插拔配音引擎。

统一接口 VoiceEngine：每个后端实现「把一段文字合成语音」，克隆型后端还会用
ref_audio（本句对应的原声切片）复刻说话人音色。上层（tts.py）只认接口，不关心
具体用 edge-tts 还是某家云或本地 XTTS。

引擎可用性 available() 按「是否装好/是否填了 key」判断，前端据此点亮选项，
实现「四种方案都拥有、按配置选择走向」。

云端三家（cosyvoice/iflytek/volc）的网络调用为「依文档实现、待联调」状态：
key 留空时 available()=False；填了 key 也需要明天拿真实 key 跑通后按报错微调。
对应代码段都标了 # 待验证。
"""

import asyncio
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_CONFIG_PATH = os.path.join(ROOT, "voice_config.json")
XTTS_VENV_PYTHON = os.path.join(ROOT, "tts_clone_venv", "Scripts", "python.exe")
XTTS_WORKER = os.path.join(ROOT, "tts_clone", "xtts_worker.py")


# ---- 云端 key / 本地配置的读写（结构同现有 deepl_key.json 风格） ----------

def load_voice_config():
    if not os.path.exists(VOICE_CONFIG_PATH):
        return {}
    try:
        with open(VOICE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_voice_config(provider, fields):
    """合并保存某家配置：fields 是 {字段名: 值}，只覆盖该 provider 的键。"""
    cfg = load_voice_config()
    cfg[provider] = {**cfg.get(provider, {}), **fields}
    with open(VOICE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _provider_cfg(provider):
    return load_voice_config().get(provider, {})


# ---- 引擎接口与各实现 --------------------------------------------------------

class VoiceEngine:
    id = ""
    name = ""
    supports_clone = False
    needs = []  # 配置字段名列表（前端据此渲染输入框）
    output_ext = "mp3"  # synth 产出的音频格式（供上层命名临时文件）

    def available(self):
        raise NotImplementedError

    def synth(self, text, out_path, ref_audio=None, voice=None):
        """合成一句到 out_path。克隆引擎使用 ref_audio 复刻音色。"""
        raise NotImplementedError

    def synth_batch(self, jobs, on_progress=None):
        """批量合成。jobs: [{"text","out_path","ref_audio","voice"}]。
        默认逐句调用 synth；需要「一次加载模型跑全部」的引擎（如本地 XTTS）会重写。"""
        total = len(jobs)
        for i, job in enumerate(jobs):
            self.synth(job["text"], job["out_path"], job.get("ref_audio"), job.get("voice"))
            if on_progress:
                on_progress((i + 1) / total * 100)


class EdgeEngine(VoiceEngine):
    """微软 edge-tts：免费、无 key、预设音色。不克隆（兜底用）。"""
    id = "edge"
    name = "Edge TTS（预设音色·免费）"
    supports_clone = False

    def available(self):
        return True

    def synth(self, text, out_path, ref_audio=None, voice=None):
        import edge_tts
        voice = voice or "zh-CN-YunjianNeural"

        async def _run():
            await edge_tts.Communicate(text, voice).save(out_path)

        asyncio.run(_run())


class XttsEngine(VoiceEngine):
    """本地 XTTS-v2：零样本克隆，不需账号。装在独立 venv tts_clone_venv 里，
    用子进程一次性跑完全部 job（避免每句重载模型）。"""
    id = "xtts"
    name = "本地 XTTS-v2（克隆·免费·需安装）"
    supports_clone = True
    output_ext = "wav"

    def available(self):
        return os.path.exists(XTTS_VENV_PYTHON) and os.path.exists(XTTS_WORKER)

    def synth(self, text, out_path, ref_audio=None, voice=None):
        self.synth_batch([{"text": text, "out_path": out_path, "ref_audio": ref_audio}])

    def synth_batch(self, jobs, on_progress=None):
        if not self.available():
            raise RuntimeError("本地 XTTS 未安装：缺少 tts_clone_venv 或 xtts_worker.py")
        spec_path = os.path.join(os.path.dirname(jobs[0]["out_path"]), "_xtts_jobs.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump({"language": "zh-cn", "jobs": jobs}, f, ensure_ascii=False)
        result = subprocess.run(
            [XTTS_VENV_PYTHON, XTTS_WORKER, spec_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"XTTS 合成失败：{result.stderr[-500:]}")
        if on_progress:
            on_progress(100)


class CosyVoiceEngine(VoiceEngine):
    """阿里云百炼 CosyVoice 声音复刻。# 待验证（明天用真实 key 联调）。

    注意：DashScope 的声音复刻是「用一段参考音注册出 voice_id，再合成」，参考音
    通常要求可访问的 URL。逐句本地切片需先上传/托管，联调时再决定是整段注册一个
    主音色，还是逐句注册。这里先把鉴权与合成调用骨架搭好。"""
    id = "cosyvoice"
    name = "阿里云 CosyVoice（克隆·需Key）"
    supports_clone = True
    needs = ["api_key"]

    def available(self):
        return bool(_provider_cfg("cosyvoice").get("api_key"))

    def synth(self, text, out_path, ref_audio=None, voice=None):
        import requests
        cfg = _provider_cfg("cosyvoice")
        api_key = cfg.get("api_key")
        if not api_key:
            raise RuntimeError("未配置阿里云 CosyVoice api_key")
        # 待验证：DashScope 语音合成 REST 端点 / 声音复刻 voice_id 流程
        # 联调时按官方文档补全：先 voice enrollment 得 voice_id，再合成。
        raise NotImplementedError(
            "CosyVoice 适配器骨架已就绪，待明天用真实 api_key 联调（参考音托管/voice_id 流程）"
        )


class IflytekEngine(VoiceEngine):
    """讯飞 语音复刻。# 待验证（明天用真实 key 联调）。

    讯飞 WebAPI 用 appid + api_key + api_secret 做 HMAC 鉴权，复刻为独立产品
    （训练得到音色 id 后再合成）。这里先存配置、搭骨架。"""
    id = "iflytek"
    name = "讯飞 语音复刻（克隆·需Key）"
    supports_clone = True
    needs = ["app_id", "api_key", "api_secret"]

    def available(self):
        cfg = _provider_cfg("iflytek")
        return all(cfg.get(k) for k in self.needs)

    def synth(self, text, out_path, ref_audio=None, voice=None):
        cfg = _provider_cfg("iflytek")
        if not all(cfg.get(k) for k in self.needs):
            raise RuntimeError("未配置讯飞 app_id/api_key/api_secret")
        # 待验证：讯飞复刻训练 + 合成（HMAC 鉴权、音色 id）
        raise NotImplementedError(
            "讯飞适配器骨架已就绪，待明天用真实凭据联调（HMAC 鉴权 + 复刻音色 id）"
        )


class VolcEngine(VoiceEngine):
    """火山引擎/豆包 声音复刻。# 待验证（明天用真实 key 联调）。

    火山 TTS 用 app_id + access_token（+cluster），声音复刻先训练得 speaker_id
    再合成。这里先存配置、搭骨架。"""
    id = "volc"
    name = "火山引擎 声音复刻（克隆·需Key）"
    supports_clone = True
    needs = ["app_id", "access_token", "cluster"]

    def available(self):
        cfg = _provider_cfg("volc")
        return all(cfg.get(k) for k in self.needs)

    def synth(self, text, out_path, ref_audio=None, voice=None):
        cfg = _provider_cfg("volc")
        if not all(cfg.get(k) for k in self.needs):
            raise RuntimeError("未配置火山 app_id/access_token/cluster")
        # 待验证：火山复刻训练 + 合成（access_token 鉴权、speaker_id）
        raise NotImplementedError(
            "火山适配器骨架已就绪，待明天用真实凭据联调（speaker_id 复刻 + 合成）"
        )


# ---- 注册表 ------------------------------------------------------------------

_ENGINE_CLASSES = [EdgeEngine, XttsEngine, CosyVoiceEngine, IflytekEngine, VolcEngine]
_ENGINES = {cls.id: cls() for cls in _ENGINE_CLASSES}


def get_engine(engine_id):
    engine = _ENGINES.get(engine_id)
    if engine is None:
        raise ValueError(f"未知配音引擎：{engine_id}")
    return engine


def list_engines():
    """给前端：每个引擎的 id/名称/是否克隆/是否可用/需要的配置字段。"""
    return [
        {
            "id": e.id,
            "name": e.name,
            "supports_clone": e.supports_clone,
            "available": e.available(),
            "needs": e.needs,
        }
        for e in _ENGINES.values()
    ]


def configured_providers():
    """给前端：各 provider 是否已配置（不回传具体密钥）。"""
    cfg = load_voice_config()
    return {pid: bool(cfg.get(pid)) for pid in ("cosyvoice", "iflytek", "volc")}

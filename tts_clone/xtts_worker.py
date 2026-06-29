"""XTTS-v2 批量克隆合成 worker —— 在独立 venv `tts_clone_venv` 里运行。

主程序通过子进程调用：`python xtts_worker.py jobs.json`。一次加载模型、循环
处理所有 job（避免每句重载模型）。每个 job 用自己的 ref_audio（本句原声切片）
做零样本克隆，复刻该句说话人的音色。

安装（在项目根目录，以下版本组合已在 RTX 4060 Ti / Python 3.11 实测跑通）：
    python -m venv tts_clone_venv
    tts_clone_venv\\Scripts\\python -m pip install --upgrade pip
    tts_clone_venv\\Scripts\\python -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
    tts_clone_venv\\Scripts\\python -m pip install coqui-tts "transformers==4.57.1" pypinyin jieba
注意：
  - torch 必须锁 2.6.0：不写版本会装到没有 cu124 的版本而退化成 CPU 包（cuda 不可用）。
  - transformers 必须锁 <5（4.57.1）：coqui-tts 用到 5.x 已删除的 isin_mps_friendly，否则 import 报错。
  - pypinyin、jieba 是中文合成必需（缺了会报 "Chinese requires: pypinyin"）。
首次合成会自动下载 XTTS-v2 模型（约 1.8GB）。

jobs.json 结构：
    {"language": "zh-cn", "jobs": [{"text": "...", "ref_audio": "ref.wav", "out_path": "out.wav"}, ...]}
"""

import json
import os
import sys

# 同意 Coqui 模型许可（XTTS 首次下载需要），避免交互式询问卡住子进程
os.environ.setdefault("COQUI_TOS_AGREED", "1")


def main(spec_path):
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    jobs = spec["jobs"]
    language = spec.get("language", "zh-cn")

    import torch
    from TTS.api import TTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

    for job in jobs:
        text = job["text"]
        ref = job.get("ref_audio")
        out = job["out_path"]
        if not text or not text.strip():
            continue
        tts.tts_to_file(
            text=text,
            speaker_wav=ref,        # 本句原声切片 → 复刻该说话人
            language=language,
            file_path=out,
        )
    print(f"XTTS done: {len(jobs)} clips on {device}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python xtts_worker.py jobs.json", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])

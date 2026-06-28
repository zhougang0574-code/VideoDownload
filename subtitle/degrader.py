"""台词劣化器：把一句中文在多种语言间来回机翻 N 遍，让语义层层失真。

笑点来源是「笨」机器翻译的累积误差，所以这里刻意用 Google 网页版引擎多轮
硬翻，而不是用大模型（大模型会自我纠错、保持通顺，反而没了荒腔走板的味道）。
"""

import time

from deep_translator import GoogleTranslator

# 轴心语言链：每翻一遍就换一门语言，循环使用。挑了几门语法/语序差异大的，
# 误差累积更快、跑偏更狠。代码用 Google 的语言代码。
PIVOT_LANGS = ["en", "ja", "ko", "ar", "ru", "fr", "de", "hi"]

FINAL_LANG = "zh-CN"  # 最终一定翻回中文
SOURCE_LANG = "zh-CN"  # 原台词是中文


def _translate(text, source, target, retries=2):
    """单步翻译，带重试。Google 网页接口偶尔返回空（限流/怪输入）会抛
    TranslationNotFound，重试常能成功；retries 次后仍失败则抛给调用方。"""
    last_err = None
    for _ in range(retries + 1):
        try:
            result = GoogleTranslator(source=source, target=target).translate(text)
            if result and result.strip():
                return result
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    if last_err:
        raise last_err
    raise ValueError("空翻译结果")


def degrade_text(text, rounds=20, langs=None, delay=0.3, on_step=None):
    """把一句中文来回机翻 rounds 遍，最后翻回中文。

    rounds 指中间经过的「轴心语言」步数；之后再强制翻回中文。任何一步失败就
    跳过该步、保留上一步文本，保证整体不中断。delay 是每步之间的限速秒数，
    避免触发 Google 网页接口的频率限制。
    """
    if not text or not text.strip():
        return text

    langs = langs or PIVOT_LANGS
    current = text
    source = SOURCE_LANG

    for i in range(rounds):
        target = langs[i % len(langs)]
        if target == source:
            continue
        try:
            translated = _translate(current, source, target)
            if translated and translated.strip():
                current = translated
                source = target
        except Exception as e:
            # 不打印异常里的外文原文，否则在 GBK 控制台会再抛 UnicodeEncodeError
            print(f"[劣化] 第{i + 1}遍 {source}->{target} 失败，跳过（{type(e).__name__}）")
        if on_step:
            on_step(i + 1, rounds)
        if delay:
            time.sleep(delay)

    # 收尾：翻回中文
    if source != FINAL_LANG:
        try:
            final = _translate(current, source, FINAL_LANG)
            if final and final.strip():
                current = final
        except Exception as e:
            print(f"[劣化] 收尾翻回中文失败，保留上一步（{type(e).__name__}）")

    return current


def degrade_segments(segments, rounds=20, langs=None, delay=0.3, on_progress=None):
    """对每段字幕的 text 做劣化，保持 start/end 不变，让字幕仍与时间轴对齐。

    segments 是 [{"start", "end", "text"}]，返回同结构、text 已被搞坏的新列表。
    """
    result = []
    total = len(segments)
    for i, seg in enumerate(segments):
        degraded = degrade_text(seg["text"], rounds=rounds, langs=langs, delay=delay)
        result.append({"start": seg["start"], "end": seg["end"], "text": degraded})
        if on_progress:
            on_progress((i + 1) / total * 100)
    return result


if __name__ == "__main__":
    import sys

    # Windows 控制台默认 GBK，编码不了法语/阿拉伯语等中间结果，切到 utf-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # 快速验证「翻 N 遍」的味道
    samples = [
        "宁教我负天下人，休教天下人负我。",
        "今天下英雄，唯使君与操耳。",
        "大丈夫生于乱世，当带三尺剑立不世之功。",
    ]
    for s in samples:
        print(f"\n原句: {s}")
        for r in (5, 10, 20):
            out = degrade_text(s, rounds=r, delay=0.2)
            print(f"  翻{r:>2}遍: {out}")

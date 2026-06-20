import json
import os

from deep_translator import DeeplTranslator, GoogleTranslator

DEEPL_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepl_key.json")

TARGET_LANG = "zh-CN"


def load_deepl_key():
    if not os.path.exists(DEEPL_KEY_PATH):
        return None
    with open(DEEPL_KEY_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("api_key")


def save_deepl_key(api_key):
    with open(DEEPL_KEY_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_key": api_key}, f, ensure_ascii=False, indent=2)


def translate_segments(segments, on_progress=None):
    """Translate each segment's text to Chinese, keeping start/end untouched
    so the subtitles stay in sync with the audio."""
    deepl_key = load_deepl_key()
    use_deepl = deepl_key is not None
    deepl = DeeplTranslator(api_key=deepl_key, source="auto", target=TARGET_LANG) if use_deepl else None
    google = GoogleTranslator(source="auto", target=TARGET_LANG)

    translated = []
    total = len(segments)
    for i, seg in enumerate(segments):
        text = seg["text"]
        if use_deepl:
            try:
                zh_text = deepl.translate(text)
            except Exception as e:
                print(f"[字幕翻译] DeepL 失败（{e}），剩余字幕切换到免费引擎")
                use_deepl = False
                zh_text = google.translate(text)
        else:
            zh_text = google.translate(text)

        translated.append({"start": seg["start"], "end": seg["end"], "text": zh_text or text})
        if on_progress:
            on_progress((i + 1) / total * 100)

    return translated

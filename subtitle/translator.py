import json
import os

from deep_translator import DeeplTranslator, GoogleTranslator

DEEPL_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepl_key.json")

# Whisper returns ISO-639-1 source codes (en/ja/ko/...). Google takes those
# codes directly and uses "zh-CN" for Simplified Chinese; DeepL uses "zh".
GOOGLE_TARGET = "zh-CN"
DEEPL_TARGET = "zh"


def load_deepl_key():
    if not os.path.exists(DEEPL_KEY_PATH):
        return None
    with open(DEEPL_KEY_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("api_key")


def save_deepl_key(api_key):
    with open(DEEPL_KEY_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_key": api_key}, f, ensure_ascii=False, indent=2)


def _google_engine(source_lang):
    """Build a Google engine for the detected source language, falling back to
    auto-detect if the code isn't one deep_translator recognises."""
    try:
        return GoogleTranslator(source=source_lang or "auto", target=GOOGLE_TARGET)
    except Exception:
        return GoogleTranslator(source="auto", target=GOOGLE_TARGET)


def _deepl_engine(source_lang, deepl_key):
    """Build a DeepL engine, or return None when DeepL can't handle this source
    language (e.g. Korean) or the key is unusable, so the caller falls back to
    the free Google engine."""
    if not deepl_key:
        return None
    try:
        return DeeplTranslator(api_key=deepl_key, source=source_lang or "auto", target=DEEPL_TARGET)
    except Exception as e:
        print(f"[字幕翻译] DeepL 不支持源语言「{source_lang}」或密钥不可用（{e}），改用免费引擎")
        return None


def translate_text(text, source_lang=None):
    """Translate a single string to Chinese (used for titles / filenames).
    Returns the original text unchanged if translation isn't possible."""
    if not text:
        return text
    deepl = _deepl_engine(source_lang, load_deepl_key())
    if deepl is not None:
        try:
            return deepl.translate(text) or text
        except Exception as e:
            print(f"[文件名翻译] DeepL 失败（{e}），改用免费引擎")
    try:
        return _google_engine(source_lang).translate(text) or text
    except Exception as e:
        print(f"[文件名翻译] 翻译失败（{e}），保留原文件名")
        return text


def translate_segments(segments, source_lang=None, on_progress=None):
    """Translate each segment's text to Chinese, keeping start/end untouched so
    the subtitles stay in sync with the audio. source_lang is whisper's detected
    language code (en/ja/ko/...); any non-Chinese source is supported."""
    deepl = _deepl_engine(source_lang, load_deepl_key())
    google = _google_engine(source_lang)

    translated = []
    total = len(segments)
    for i, seg in enumerate(segments):
        text = seg["text"]
        zh_text = None
        if deepl is not None:
            try:
                zh_text = deepl.translate(text)
            except Exception as e:
                print(f"[字幕翻译] DeepL 失败（{e}），剩余字幕切换到免费引擎")
                deepl = None
        if zh_text is None:
            zh_text = google.translate(text)

        translated.append({"start": seg["start"], "end": seg["end"], "text": zh_text or text})
        if on_progress:
            on_progress((i + 1) / total * 100)

    return translated

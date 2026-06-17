"""作戦ボードのAI解説（Gemini）。

各銘柄の「根拠・確信度・リスク」を日本語生成する。listing-text-generator と同じ
環境変数（GEMINI_API_KEY / GEMINI_MODEL）を使う。キー未設定・失敗時は None を返し、
作戦ボードは従来どおり動作する（完全オプトイン）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# .env（backend/.env）を読む。未導入でも環境変数があれば動く。
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

DEFAULT_MODEL = "gemini-2.5-flash"


def _api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def _model_name() -> str:
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def extract_json(text: str) -> Any:
    """Gemini 応答から JSON オブジェクトを取り出してパースする。

    コードフェンス ```json ... ``` や前後テキストを許容する。
    """
    if not text:
        raise ValueError("empty response")
    candidate = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if m:
            candidate = m.group(1)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in response: {text[:200]}")
    return json.loads(candidate[start : end + 1])

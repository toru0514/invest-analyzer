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


def build_prompt(plan: dict, market_ctx: dict) -> str:
    detail = plan.get("detail") or {}
    return "\n".join([
        "あなたは日本株スイングトレード（数日〜1週間）の作戦補佐です。",
        "以下のテクニカル判定データをもとに、なぜこの方向なのかの根拠・確信度・主なリスクを日本語で簡潔にまとめてください。",
        "予測の保証はできません。投資判断は利用者の自己責任です。",
        "新しい数値（指値・損切り等）は作らず、与えられた値の解釈に徹してください。",
        "",
        f"銘柄: {plan.get('ticker')} {plan.get('name') or ''}",
        f"判定: {plan.get('direction')}（スコア {plan.get('score')}）",
        f"終値: {plan.get('close')}",
        f"指標内訳: {json.dumps(detail, ensure_ascii=False)}",
        f"出来高倍率: {plan.get('vol_ratio')}",
        f"週足トレンド: {plan.get('weekly_trend')}",
        f"提案指値: {plan.get('limit_price')} / 利確: {plan.get('target_price')} / 損切: {plan.get('stop_price')}",
        f"地合い(指数トレンド): {market_ctx.get('index_trend')}",
        f"決算まで日数: {market_ctx.get('days_to_earnings')}",
        "",
        "次のJSON形式のみで出力してください（前後に文章を付けない）:",
        '{"confidence": <0-100の整数>, "summary": "<2〜3文の根拠>", "risks": ["<短いリスク>", "..."]}',
    ])


def _generate_text(prompt: str) -> str:
    """Gemini を呼んで生のテキストを返す。テストはここを monkeypatch する。

    import は実行時のみ（SDK 未導入でも本モジュールの読込・単体テストは通る）。
    """
    import google.generativeai as genai

    genai.configure(api_key=_api_key())
    model = genai.GenerativeModel(_model_name())
    resp = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json", "temperature": 0.4},
    )
    return resp.text or ""


def _coerce(obj: Any) -> dict | None:
    """Gemini の出力を {confidence:int(0-100), summary:str, risks:[str]} に整える。"""
    if not isinstance(obj, dict):
        return None
    try:
        conf = int(round(float(obj.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(100, conf))
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    risks = obj.get("risks") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [str(r) for r in risks if str(r).strip()][:5]
    return {"confidence": conf, "summary": summary.strip(), "risks": risks}


def generate_commentary(plan: dict, market_ctx: dict) -> dict | None:
    """作戦1行分のAI解説を生成。キー無し/API失敗は None（作戦ボードは継続）。"""
    if not _api_key():
        return None
    prompt = build_prompt(plan, market_ctx)
    for _ in range(2):  # JSONパース失敗時に1回だけリトライ
        try:
            text = _generate_text(prompt)
        except Exception:
            return None  # API失敗 → スキップ（v1は待機/バックオフなし）
        try:
            obj = extract_json(text)
        except Exception:
            continue  # パース失敗 → リトライ
        return _coerce(obj)
    return None

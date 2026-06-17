import ai_commentary as ac


def test_extract_json_plain():
    assert ac.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "```json\n{\"confidence\": 70, \"summary\": \"x\"}\n```"
    assert ac.extract_json(text)["confidence"] == 70


def test_extract_json_with_surrounding_text():
    text = 'これはJSONです: {"summary": "ok"} 以上。'
    assert ac.extract_json(text)["summary"] == "ok"


def test_extract_json_no_object_raises():
    import pytest
    with pytest.raises(ValueError):
        ac.extract_json("JSONはありません")


def test_coerce_clamps_confidence():
    assert ac._coerce({"confidence": 250, "summary": "x"})["confidence"] == 100
    assert ac._coerce({"confidence": -5, "summary": "x"})["confidence"] == 0


def test_coerce_requires_summary():
    assert ac._coerce({"confidence": 50, "summary": ""}) is None
    assert ac._coerce({"confidence": 50}) is None
    assert ac._coerce("not a dict") is None


def test_coerce_risks_normalized():
    out = ac._coerce({"confidence": 50, "summary": "x", "risks": ["a", "b", "c", "d", "e", "f"]})
    assert out["risks"] == ["a", "b", "c", "d", "e"]  # 最大5件
    out2 = ac._coerce({"confidence": 50, "summary": "x", "risks": "単一文字列"})
    assert out2["risks"] == ["単一文字列"]


def test_generate_commentary_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ac.generate_commentary({"ticker": "8306.T"}, {}) is None


def test_generate_commentary_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(
        ac, "_generate_text",
        lambda prompt: '{"confidence": 72, "summary": "押し目買い。", "risks": ["決算が近い"]}',
    )
    out = ac.generate_commentary({"ticker": "8306.T", "direction": "buy"}, {})
    assert out == {"confidence": 72, "summary": "押し目買い。", "risks": ["決算が近い"]}


def test_generate_commentary_bad_json_then_none(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(ac, "_generate_text", lambda prompt: "JSONではない応答")
    assert ac.generate_commentary({"ticker": "8306.T"}, {}) is None


def test_generate_commentary_api_error_returns_none(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def boom(prompt):
        raise RuntimeError("network")

    monkeypatch.setattr(ac, "_generate_text", boom)
    assert ac.generate_commentary({"ticker": "8306.T"}, {}) is None

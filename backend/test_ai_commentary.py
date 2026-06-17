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

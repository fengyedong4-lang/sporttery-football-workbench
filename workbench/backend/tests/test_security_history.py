import json
from pathlib import Path

import pytest

from app.services.history import atomic_json_write, build_history_index
from app.services.security import is_official_hostname, safe_child, validate_redirect_chain


def test_real_hostname_boundary_and_redirects(tmp_path: Path):
    assert is_official_hostname("https://webapi.sporttery.cn/a")
    assert is_official_hostname("https://lottery.gov.cn/a")
    assert not is_official_hostname("https://sporttery.cn.evil.test/a")
    assert not validate_redirect_chain(["https://sporttery.cn/a", "https://evil.test/b"])
    with pytest.raises(ValueError):
        safe_child(tmp_path, "../escape.json")


def test_index_reports_stale_declared_count_and_rebuilds(tmp_path: Path):
    source = tmp_path / "history.json"
    target = tmp_path / "index.json"
    atomic_json_write(source, {"record_count": 1, "records": [{"prediction_date": "d", "match_number": "1"}, {"prediction_date": "d", "match_number": "1"}]})
    first = build_history_index(source, target)
    assert first["declared_record_count"] == 1
    assert first["actual_record_count"] == 2
    assert first["independent_match_count"] == 1
    data = json.loads(source.read_text(encoding="utf-8"))
    data["records"].append({"prediction_date": "d", "match_number": "2"})
    atomic_json_write(source, data)
    second = build_history_index(source, target)
    assert second["actual_record_count"] == 3


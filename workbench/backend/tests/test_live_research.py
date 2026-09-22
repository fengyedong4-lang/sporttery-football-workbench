import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import live_research as live


def fixture(match_id="100"):
    return {
        "match_id": match_id,
        "competition": "测试联赛",
        "home_team": "甲队",
        "away_team": "乙队",
        "kickoff_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }


def candidate(url="https://example.com/report"):
    return {
        "url": url, "discovered_title": "甲队赛前消息", "source_level": "major_media",
        "team_mapping": ["home"], "target_dimensions": ["injuries", "market"],
    }


def fetched_source(url="https://example.com/report"):
    raw = b"verified body"
    return {
        **candidate(url), "source_id": "web:abc", "title": "甲队赛前消息",
        "published_at": "2026-09-22T08:00:00+00:00", "fetched_at": "2026-09-22T09:00:00+00:00",
        "confirmation_status": "single_source_unconfirmed", "fetch_status": "fetched",
        "failure_reason": None, "final_url": url, "redirect_chain": [], "retrieval_method": "direct",
        "content_type": "text/html", "content_sha256": hashlib.sha256(raw).hexdigest(),
        "content_bytes": len(raw), "body_path": "body", "receipt_path": "receipt",
        "text": "甲队官方确认主力门将因伤缺席本场比赛。该报道同时说明球队将在赛前公布最后名单。",
        "fixture_ids": ["100"],
        "fixture_team_mapping": {"100": ["home"]},
    }


def search_data(match_id="100", url="https://example.com/report"):
    return {"fixtures": [{
        "fixture_id": match_id, "queries": ["甲队 injuries"], "candidates": [candidate(url)], "missing": [],
    }]}


def extract_data(match_id="100", *, observations=None):
    return {
        "facts": [{
            "fixture_id": match_id, "dimension": "injuries", "summary": "甲队确认主力门将因伤缺席。",
            "support_excerpt": "甲队官方确认主力门将因伤缺席本场比赛。",
            "team_mapping": ["home"], "subject_level": "first_team", "scope": "matchday",
            "valid_from": "2026-09-22", "valid_to": None,
            "confirmation_status": "reported_by_major_media", "source_ids": ["web:abc"],
        }],
        "market_observations": observations or [], "missing": [],
    }


def attempt(data, stage):
    return {
        "stage": stage, "started_at": "2026-09-22T00:00:00+00:00",
        "completed_at": "2026-09-22T00:00:01+00:00", "status": "completed", "reason": None,
        "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3},
        "search_actions": [{"query": "query", "action": "search"}] if stage == "search" else [],
        "prompt_sha256": "0" * 64, "data": data,
    }


def install_pipeline(monkeypatch, *, extract=None, fetch=None):
    monkeypatch.setattr(live, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(live, "_require_public_url", lambda url: None)
    calls = []

    def invoke(prompt, schema, **kwargs):
        calls.append(kwargs["search_enabled"])
        return attempt(search_data(), "search") if kwargs["search_enabled"] else attempt(extract or extract_data(), "extract")

    monkeypatch.setattr(live, "_invoke_codex", invoke)
    monkeypatch.setattr(live, "_fetch_candidate", fetch or (lambda *args, **kwargs: fetched_source()))
    return calls


def test_cli_search_uses_native_search_and_rejects_other_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "_binary", lambda: "codex.exe")
    captured = {}
    final = {"fixtures": []}
    stream = "\n".join([
        json.dumps({"type": "thread.started"}), json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"type": "web_search", "query": "football", "action": {"type": "search"}}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(final)}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}),
    ])

    def run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=stream, stderr="secret")

    monkeypatch.setattr(live, "_run", run)
    result = live._invoke_codex("prompt", live._search_schema(), search_enabled=True, model="gpt-6-astra",
                                effort="low", timeout_seconds=30, parent_dir=tmp_path)
    args = captured["args"]
    assert args[1:3] == ["--search", "exec"]
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "shell_tool" in args and "code_mode_host" not in args
    assert "secret" not in str(result)
    assert result["status"] == "completed" and result["search_actions"][-1]["query"] == "football"

    malicious = stream.replace('"web_search"', '"command_execution"')
    final_text, usage, reason, _ = live._event_stream(malicious, search_enabled=True)
    assert final_text and usage and reason == "tool_event_rejected"


def test_fetch_writes_raw_body_and_hash_verified_receipt(monkeypatch, tmp_path):
    raw = ("<html><head><title>Team news</title>"
           '<meta property="article:published_time" content="2026-09-22T08:00:00Z">'
           "</head><body>甲队官方确认主力门将因伤缺席本场比赛，报道正文足够长以通过检查。"
           "球队还说明将在训练结束后公布最后名单，记者不会使用旧伤消息推断本场状态。"
           "这是用于检验正文回执的附加文字，确保抓取结果不是空白页面。</body></html>").encode()
    monkeypatch.setattr(live, "_download", lambda *a, **k: (raw, "text/html; charset=utf-8", "https://example.com/report", [], "direct"))
    source = live._fetch_candidate(candidate(), tmp_path, timeout_seconds=10)
    assert source["fetch_status"] == "fetched"
    assert source["title"] == "Team news"
    assert source["published_at"] == "2026-09-22T08:00:00+00:00"
    body = Path(source["body_path"]).read_bytes()
    receipt = json.loads(Path(source["receipt_path"]).read_text(encoding="utf-8"))
    assert body == raw
    assert receipt["body_sha256_verified"] is True
    assert receipt["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["content_excerpt_rule"].startswith("visible_text_v2")
    assert receipt["content_excerpt_truncated"] is False


def test_markdown_cleanup_keeps_visible_labels_and_moves_body_into_excerpt():
    navigation = "\n".join(
        f"[Navigation item {index % 5}](https://example.com/path/{index}/{'x' * 80})"
        for index in range(400)
    )
    markdown = f"Title: Match page\n{navigation}\n## Japan Thailand\n### Staff\nCoach name"
    assert markdown.index("Japan Thailand") > live._MAX_SOURCE_TEXT
    text, title, _ = live._body_text(markdown.encode(), "text/plain; charset=utf-8")
    assert title == "Match page"
    assert "Japan Thailand" in text[:live._MAX_SOURCE_TEXT]
    assert "Staff" in text[:live._MAX_SOURCE_TEXT]
    assert "https://" not in text


def test_fresh_batch_returns_denormalized_source_and_dimension_status(monkeypatch, tmp_path):
    calls = install_pipeline(monkeypatch)
    result = live.collect_live_research([fixture()], evidence_dir=tmp_path, effort="low", timeout_seconds=30)
    assert result["status"] == "completed" and calls == [True, False]
    assert result["calls"] == 2
    assert result["usage"] == {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 6}
    row = result["fixtures"][0]
    assert row["dimensions"]["injuries"] == {
        "attempted": True, "found": 1, "missing": False, "source_ids": ["web:abc"],
    }
    assert row["dimensions"]["transfers"]["missing"] is True
    fact = row["facts"][0]
    assert fact["sources"][0]["url"] == "https://example.com/report"
    assert fact["sources"][0]["fetched_at"]
    assert fact["sources"][0]["confirmation_status"] == "reported_by_major_media"
    assert Path(result["request_dir"], "batch.json").is_file()
    assert Path(result["request_dir"], "attempts.json").is_file()


def test_new_request_never_reuses_previous_request_source(monkeypatch, tmp_path):
    counter = {"fetch": 0}

    def fetch(*args, **kwargs):
        counter["fetch"] += 1
        return fetched_source()

    install_pipeline(monkeypatch, fetch=fetch)
    first = live.collect_live_research([fixture()], evidence_dir=tmp_path, effort="low", timeout_seconds=30)
    second = live.collect_live_research([fixture()], evidence_dir=tmp_path, effort="low", timeout_seconds=30)
    assert first["request_id"] != second["request_id"]
    assert first["request_dir"] != second["request_dir"]
    assert counter["fetch"] == 2
    assert first["cache_policy"].startswith("no_cross_request_cache")


def test_failed_body_fetch_cannot_create_fact(monkeypatch, tmp_path):
    failed = {**candidate(), "fetch_status": "failed", "failure_reason": "URLError"}
    calls = install_pipeline(monkeypatch, fetch=lambda *a, **k: failed)
    result = live.collect_live_research([fixture()], evidence_dir=tmp_path, effort="low", timeout_seconds=30)
    assert calls == [True]
    assert result["status"] == "partial"
    assert result["fixtures"][0]["facts"] == []
    assert result["fixtures"][0]["dimensions"]["injuries"]["missing"] is True
    assert any("source_fetch_failed" in warning for warning in result["fixtures"][0]["warnings"])


def market_observation(observed_at, *, bookmaker="Book A", market_type="european_1x2", line=None,
                       home=2.0, draw=3.2, away=3.5):
    return {
        "fixture_id": "100", "source_id": "web:abc", "bookmaker": bookmaker,
        "team_mapping": ["home"],
        "market_type": market_type, "observed_at": observed_at,
        "support_excerpt": f"Book A {line if line is not None else ''} {home} {draw if draw is not None else ''} {away}",
        "published_at": None, "valid_time": None, "line": line,
        "home": home, "draw": draw, "away": away, "observation_basis": "retrieval_snapshot",
    }


def test_market_change_requires_same_bookmaker_market_line_and_distinct_times():
    one = [
        {**market_observation("2026-09-22T08:00:00+00:00"), "observation_id": "a"},
    ]
    assert live._market_comparisons(one) == []
    mixed = one + [
        {**market_observation("2026-09-22T09:00:00+00:00", bookmaker="Book B", home=1.9), "observation_id": "b"},
        {**market_observation("2026-09-22T10:00:00+00:00", market_type="asian_handicap", line=-0.5,
                              home=1.9, draw=None, away=1.95), "observation_id": "c"},
    ]
    assert live._market_comparisons(mixed) == []
    valid = one + [
        {**market_observation("2026-09-22T10:00:00+00:00", home=1.85), "observation_id": "d"},
    ]
    comparisons = live._market_comparisons(valid)
    assert len(comparisons) == 1
    assert comparisons[0]["recognition_rule"] == "same_bookmaker_same_market_same_line_distinct_times"
    assert comparisons[0]["changes"] == {"home": {"from": 2.0, "to": 1.85}}


def test_youth_fact_is_not_mapped_to_first_team():
    raw = extract_data()
    raw["facts"][0]["subject_level"] = "youth"
    facts, observations, missing = live._validate_extraction(raw, live._normalize_fixtures([fixture()]), [fetched_source()])
    assert facts == [] and observations == [] and missing == []


def test_expired_injury_and_unverified_injury_authority_are_filtered():
    rows = live._normalize_fixtures([fixture()])
    expired = extract_data()
    expired["facts"][0]["valid_from"] = "2025-01-01"
    expired["facts"][0]["valid_to"] = "2025-01-10"
    facts, _, _ = live._validate_extraction(expired, rows, [fetched_source()])
    assert facts == []
    untrusted_source = fetched_source()
    untrusted_source["source_level"] = "other"
    current = extract_data()
    current["facts"][0]["confirmation_status"] = "single_source_unconfirmed"
    facts, _, _ = live._validate_extraction(current, rows, [untrusted_source])
    assert facts == []


def test_validity_must_cover_kickoff_and_future_transfer_is_explicit():
    now = datetime.now(timezone.utc)
    target = fixture()
    target["kickoff_time"] = (now + timedelta(days=3)).isoformat()
    rows = live._normalize_fixtures([target])
    source = fetched_source()
    source["published_at"] = (now - timedelta(hours=1)).isoformat()

    expires_before_match = extract_data()
    expires_before_match["facts"][0]["valid_from"] = now.date().isoformat()
    expires_before_match["facts"][0]["valid_to"] = (now + timedelta(days=1)).date().isoformat()
    facts, _, _ = live._validate_extraction(expires_before_match, rows, [source])
    assert facts == []

    effective = (now + timedelta(days=1)).date().isoformat()
    source["text"] = f"甲队宣布新球员将在{effective}正式加盟并进入一线队名单。"
    transfer = extract_data()
    transfer["facts"][0].update(
        dimension="transfers", summary=f"甲队新球员将在{effective}正式加盟。",
        support_excerpt=source["text"], valid_from=effective, valid_to=None,
    )
    facts, _, _ = live._validate_extraction(transfer, rows, [source])
    assert facts[0]["temporal_status"] == "announced_future_effective_by_kickoff"
    assert facts[0]["currently_effective"] is False


def test_new_valid_from_cannot_hide_stale_publication():
    now = datetime.now(timezone.utc)
    source = fetched_source()
    source["published_at"] = (now - timedelta(days=800)).isoformat()
    raw = extract_data()
    raw["facts"][0]["valid_from"] = now.date().isoformat()
    facts, _, _ = live._validate_extraction(raw, live._normalize_fixtures([fixture()]), [source])
    assert facts == []


def test_fact_source_must_belong_to_fixture_and_cover_team_mapping():
    second = fixture("200")
    rows = live._normalize_fixtures([fixture(), second])
    raw = extract_data("200")
    with pytest.raises(ValueError, match="fact_source_fixture"):
        live._validate_extraction(raw, rows, [fetched_source()])
    source = fetched_source()
    source["team_mapping"] = ["away"]
    source["fixture_team_mapping"] = {"100": ["away"]}
    with pytest.raises(ValueError, match="fact_source_team"):
        live._validate_extraction(extract_data(), live._normalize_fixtures([fixture()]), [source])
    prompt = live._extract_prompt(live._normalize_fixtures([fixture()]), [fetched_source()])
    assert '"fixture_ids": ["100"]' in prompt
    assert '"fixture_team_mapping": {"100": ["home"]}' in prompt


def test_retrieval_snapshot_time_is_bound_to_source_fetch_time():
    source = fetched_source()
    source["text"] += " Book A 2.0 3.2 3.5"
    first = market_observation("2026-09-22T01:00:00+00:00")
    second = market_observation("2026-09-22T02:00:00+00:00", home=1.9)
    second["support_excerpt"] = "Book A 1.9 3.2 3.5"
    source["text"] += " Book A 1.9 3.2 3.5"
    data = extract_data(observations=[first, second])
    _, observations, _ = live._validate_extraction(data, live._normalize_fixtures([fixture()]), [source])
    assert {row["observed_at"] for row in observations} == {"2026-09-22T09:00:00+00:00"}
    assert live._market_comparisons(observations) == []

def test_source_level_claim_is_downgraded_when_domain_is_not_allowlisted():
    assert live._verified_source_level("https://random-blog.example/news", "official") == ("other", False)
    assert live._verified_source_level("https://www.uefa.com/news/x", "official") == ("official", True)
    assert live._verified_source_level("https://www.jfa.jp/news/x", "official") == ("official", True)
    assert live._verified_source_level("https://www.news.cn/sports/x", "major_media") == ("major_media", True)


def test_fake_ip_requires_public_doh_and_private_networks_remain_blocked(monkeypatch):
    fake = [(None, None, None, None, ("198.18.1.75", 443)),
            (None, None, None, None, ("fdfe:dcba:9876::14c", 443, 0, 0))]
    monkeypatch.setattr(live.socket, "getaddrinfo", lambda *a, **k: fake)
    monkeypatch.setattr(live, "_doh_public_addresses", lambda host: ("211.9.61.15",))
    live._require_public_url("https://www.jfa.jp/news/x")
    monkeypatch.setattr(live, "_doh_public_addresses", lambda host: ())
    with pytest.raises(ValueError, match="doh_public_validation_failed"):
        live._require_public_url("https://www.jfa.jp/news/x")
    monkeypatch.setattr(live.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("10.0.0.8", 443))])
    with pytest.raises(ValueError, match="unsafe_address"):
        live._require_public_url("https://internal.example/news")


def test_secret_environment_is_not_forwarded(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@example")
    assert "OPENAI_API_KEY" not in live._environment()
    assert "HTTPS_PROXY" not in live._environment()


def test_research_is_one_fixture_per_chunk_bounded_and_age_group_aware():
    assert live._CHUNK_SIZE == 1
    assert live._team_level("中国亚", "亚运男足") == "national_age_group"
    assert live._team_level("日本U21", "亚运男足") == "national_age_group"
    normalized = live._normalize_fixtures([{
        **fixture(), "competition": "亚运男足", "home_team": "中国亚", "away_team": "阿联酋亚",
    }])
    assert normalized[0]["team_level"] == "national_age_group"
    prompt = live._search_prompt(normalized)
    assert "最多执行8个搜索或打开动作" in prompt
    assert "当前赛事、比赛日期和成年/青年代际" in prompt
    assert "search阶段只发现候选链接" in prompt


def test_high_analysis_effort_is_capped_to_medium_for_research(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "status", lambda: {"available": True, "reason": None})
    monkeypatch.setattr(live, "_require_public_url", lambda url: None)
    efforts = []

    def invoke(prompt, schema, **kwargs):
        efforts.append(kwargs["effort"])
        return attempt(search_data(), "search") if kwargs["search_enabled"] else attempt(extract_data(), "extract")

    monkeypatch.setattr(live, "_invoke_codex", invoke)
    monkeypatch.setattr(live, "_fetch_candidate", lambda *a, **k: fetched_source())
    result = live.collect_live_research([fixture()], evidence_dir=tmp_path, effort="xhigh", timeout_seconds=30)
    assert efforts == ["medium", "medium"]
    assert result["requested_effort"] == "xhigh" and result["effort"] == "medium"


def test_bad_extracted_fact_does_not_discard_valid_fact():
    valid = extract_data()["facts"][0]
    bad = dict(valid)
    bad["support_excerpt"] = "这是正文中不存在的虚构引用片段。"
    facts, observations, missing, rejected = live._validate_extraction_rows(
        {"facts": [valid, bad], "market_observations": [], "missing": []},
        live._normalize_fixtures([fixture()]), [fetched_source()],
    )
    assert len(facts) == 1 and facts[0]["fact_id"] == "100:LIVE:F001"
    assert observations == [] and missing == []
    assert rejected == [{"fixture_id": "100", "dimension": "injuries", "kind": "fact", "reason": "fact_excerpt"}]


def test_unknown_fixture_rejects_whole_extraction_packet():
    foreign = extract_data("foreign")
    with pytest.raises(ValueError, match="extract_unknown_fixture"):
        live._validate_extraction_rows(foreign, live._normalize_fixtures([fixture()]), [fetched_source()])

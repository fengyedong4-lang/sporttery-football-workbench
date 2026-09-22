import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services import evidence_llm as llm


@pytest.fixture
def items():
    return [{"fixture": {"match_id": "x", "teams": ["甲", "乙"], "competition": "测试杯",
                          "kickoff_time": "2026-09-23 02:00", "official_handicap": -1,
                          "odds": {"h": 1.01}},
             "evidence": {"facts": [{"fact_id": "f1", "dimension": "recent_form",
                 "summary": "甲近5场全部取胜，但只有一场净胜超过一球。", "source_ids": ["s1"]},
                 {"fact_id": "f2", "dimension": "injury", "summary": "甲首发门将确认缺席。",
                  "source_ids": ["s2"]}], "missing": ["最终首发"], "warnings": []},
             "baseline": {"analysis_status": "insufficient", "method_parameters": {"rate": 1.23}}}]


def suggestion(**overrides):
    return {"match_id": "x", "analysis_result": "胜", "analysis_handicap_result": "让平",
            "summary": "甲近期正式比赛表现支持取胜，但门将缺席增加丢球风险。",
            "support_fact_ids": ["f1"], "counter_fact_ids": ["f2"],
            "counterevidence_effect": "门将缺席令方向信心降至低。", "missing": ["最终首发"],
            "confidence": "低", "handicap_reason": "f1显示多数胜场净胜一球，仍存在较大不确定性。",
            **overrides}


def stream(output, tool=False, usage=True):
    events = [{"type": "thread.started", "thread_id": "opaque"}, {"type": "turn.started"}]
    if tool:
        events.append({"type": "item.started", "item": {"type": "command_execution"}})
    events.append({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(output, ensure_ascii=False)}})
    events.append({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30} if usage else None})
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in events)


def install_mock(monkeypatch, output, **options):
    calls = []
    monkeypatch.setattr(llm, "_binary", lambda: "codex.exe")
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "login" in args:
            return SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT")
        return SimpleNamespace(returncode=0, stdout=stream(output, **options), stderr="secret must never surface")
    monkeypatch.setattr(llm, "_run", run)
    return calls


def test_single_batch_sandbox_usage_and_no_mutation(monkeypatch, items, tmp_path):
    before = deepcopy(items)
    calls = install_mock(monkeypatch, {"matches": [suggestion()]})
    result = llm.suggest_evidence_batch(items, work_dir=tmp_path)
    assert result["status"] == "completed"
    assert result["calls"] == 1
    assert result["usage"] == {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30}
    assert result["matches"][0]["method_status"] == "llm_evidence"
    args, options = calls[-1]
    assert result["model"] == "gpt-6-astra"
    assert args[args.index("-m") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="xhigh"' in args
    assert "--ignore-user-config" in args and args[args.index("--sandbox") + 1] == "read-only"
    assert 'web_search="disabled"' in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert "odds" not in options["input"] and "1.23" not in options["input"]
    assert items == before
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("overrides", [
    {"match_id": "foreign"}, {"support_fact_ids": []}, {"counter_fact_ids": ["unknown"]},
    {"analysis_result": "负"}, {"confidence": "高"}, {"handicap_reason": ""},
    {"handicap_reason": "有一球净胜依据"}, {"summary": "胜率80%"},
    {"summary": "最近999场取胜"}, {"probabilities": {"胜": 0.7}},
])
def test_rejects_unsupported_output(monkeypatch, items, overrides):
    install_mock(monkeypatch, {"matches": [suggestion(**overrides)]})
    result = llm.suggest_evidence_batch(items)
    assert result["status"] == "unavailable" and result["matches"] == []
    assert result["usage"]["output_tokens"] == 30


@pytest.mark.parametrize("matches", [[], [suggestion(), suggestion()]])
def test_rejects_missing_and_duplicate_matches(monkeypatch, items, matches):
    install_mock(monkeypatch, {"matches": matches})
    assert llm.suggest_evidence_batch(items)["reason"] == "output_validation_failed"


def test_tool_event_is_rejected_even_with_valid_final(monkeypatch, items):
    install_mock(monkeypatch, {"matches": [suggestion()]}, tool=True)
    result = llm.suggest_evidence_batch(items)
    assert result["reason"] == "tool_event_rejected"
    assert result["usage"]["input_tokens"] == 100


def test_market_cannot_be_only_support_for_a_direction(monkeypatch, items):
    items[0]["evidence"]["facts"][0]["dimension"] = "market"
    install_mock(monkeypatch, {"matches": [suggestion()]})
    assert llm.suggest_evidence_batch(items)["reason"] == "output_validation_failed"


def test_missing_handicap_and_source_rejected(monkeypatch, items):
    install_mock(monkeypatch, {"matches": [suggestion()]})
    items[0]["fixture"]["official_handicap"] = None
    assert llm.suggest_evidence_batch(items)["reason"] == "output_validation_failed"
    items[0]["fixture"]["official_handicap"] = -1
    items[0]["evidence"]["facts"][0]["source_ids"] = []
    assert llm.suggest_evidence_batch(items)["reason"] == "output_validation_failed"


def test_insufficient_facts_may_return_null_without_faking_usage(monkeypatch, items):
    install_mock(monkeypatch, {"matches": [suggestion(analysis_result=None, analysis_handicap_result=None,
        support_fact_ids=[], handicap_reason="", summary="资料不足，无法可靠判断。") ]}, usage=False)
    result = llm.suggest_evidence_batch(items)
    assert result["status"] == "completed" and result["usage"] is None


def test_no_login_no_model_call(monkeypatch, items):
    monkeypatch.setattr(llm, "_binary", lambda: None)
    result = llm.suggest_evidence_batch(items)
    assert result["calls"] == 0 and result["usage"] is None and result["reason"] == "cli_not_found"


def test_timeout_does_not_leak_stderr(monkeypatch, items):
    monkeypatch.setattr(llm, "_binary", lambda: "codex.exe")
    monkeypatch.setattr(llm, "status", lambda: {"available": True})
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("private command", 1, output=b"", stderr=b"private key")
    monkeypatch.setattr(llm, "_run", run)
    result = llm.suggest_evidence_batch(items)
    assert result["calls"] == 1 and result["reason"] == "timeout" and result["usage"] is None
    assert "private" not in str(result)


def test_subprocess_environment_does_not_forward_api_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "private")
    assert not any("KEY" in key for key in llm._environment())


def test_startup_error_is_not_silently_ignored_or_misreported_as_tool():
    text = json.dumps({"type": "item.completed", "item": {
        "type": "error", "message": "sensitive provider configuration failure"}}) + "\n"
    text += stream({"matches": [suggestion()]})
    final, usage, reason = llm._parse_events(text)
    assert reason == "provider_startup_error_unknown"
    assert usage["input_tokens"] == 100
    assert "sensitive" not in str((usage, reason))


def test_only_exact_intentionally_disabled_host_notice_is_allowed():
    notice = json.dumps({"type": "item.completed", "item": {
        "type": "error", "message": llm.CODE_MODE_DISABLED_NOTICE}}) + "\n"
    warnings = []
    final, usage, reason = llm._parse_events(notice + stream({"matches": [suggestion()]}), warnings)
    assert reason is None and final and usage["output_tokens"] == 30
    assert warnings == ["code_mode_disabled_fail_closed"]
    assert llm._parse_events(notice.replace("fail closed", "fail open") + stream({"matches": []}))[2] == "provider_startup_error_unknown"
    assert llm._parse_events('{"type":"turn.started"}\n' + notice + stream({"matches": []}))[2] == "provider_error"


@pytest.mark.parametrize("invalid", [[], [None], [{"fixture": None}],
    [{"fixture": {"match_id": "x"}, "evidence": {"facts": [], "warnings": [float("nan")]}}]])
def test_invalid_input_never_launches(monkeypatch, invalid):
    monkeypatch.setattr(llm, "status", lambda: pytest.fail("must not launch"))
    result = llm.suggest_evidence_batch(invalid)
    assert result["calls"] == 0 and result["reason"] == "invalid_input"

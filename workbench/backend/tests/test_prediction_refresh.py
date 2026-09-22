from datetime import datetime, timedelta, timezone

from app.schemas import Fixture, OfficialPlay
from app.services import evidence_workflow, live_research, manual_review
from app.services.model import GoalModel
from app.services.workflows import run_daily_prediction


class NoRules:
    def match(self, *args, **kwargs):
        return []


def test_registered_models_also_refresh_dynamic_research_each_prediction(monkeypatch, tmp_path, training_matches):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    path = tmp_path / "model.json"
    model.save(path)
    fixture = Fixture(sequence=1, match_id="123", match_number="001", business_date="2026-09-22",
                      competition="测试联赛",home_team="A",away_team="B",
                      kickoff_time=datetime.now(timezone.utc)+timedelta(days=2),official_handicap=-1,
                      result_play=OfficialPlay(status="销售中",home=2,draw=3,away=4),
                      handicap_play=OfficialPlay(status="销售中",home=2,draw=3,away=4))
    calls = []
    def refresh(fixtures, **kwargs):
        calls.append([f.match_id for f in fixtures])
        return {"status":"completed", "request_id": f"fresh-{len(calls)}", "calls":2,
                "fixtures":[{"fixture_id":"123", "facts":[{"fact_id":"live", "dimension":"injuries", "summary":"本次确认主力缺阵", "source_ids":["web"]}],
                             "sources":[{"source_id":"web","url":"https://example.com/","content_sha256":"a"*64}]}]}
    monkeypatch.setattr(live_research,"collect_live_research",refresh)
    monkeypatch.setattr(manual_review,"load_applicable_lessons",lambda *a,**k: [])
    monkeypatch.setattr(evidence_workflow,"collect_fixture_evidence",lambda *a,**k: {"identity_verified":True,"sources":[],"facts":[],"teams":{},"missing":[],"warnings":[]})
    monkeypatch.setattr(evidence_workflow,"build_auto_models",lambda *a,**k: [({"status":"unavailable"},None)])
    docs = [run_daily_prediction([fixture],model_path=path,prefilter=NoRules(),rule_budget=5,output_dir=tmp_path/"drafts",
                                evidence_dir=tmp_path/"evidence",llm_enabled=False,dynamic_research_enabled=True) for _ in range(2)]
    assert calls == [["123"],["123"]]
    assert docs[0]["matches"][0]["live_research"]["request_id"] != docs[1]["matches"][0]["live_research"]["request_id"]
    assert docs[0]["matches"][0]["dedicated_model"]["model_version"] == "model"
    assert docs[0]["matches"][0]["evidence"]["facts"][0]["dimension"] == "injuries"
    assert len(list((tmp_path/"evidence"/"independent").glob("*.json"))) == 2
    assert docs[0]["research_llm_calls"] == docs[0]["total_llm_calls"] == 2

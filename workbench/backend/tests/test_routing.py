import json
from pathlib import Path

from app.services.model import GoalModel
from app.services.routing import load_active_model_routing


def test_registry_does_not_auto_select_unregistered_newer_model(training_matches, tmp_path: Path):
    workbench = tmp_path / "workbench"
    runtime = workbench / "runtime"
    (workbench / "config").mkdir(parents=True)
    (runtime / "models").mkdir(parents=True)
    approved = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    experiment = GoalModel.fit(training_matches, competition="测试联赛", model_type="dixon_coles")
    approved.save(runtime / "models" / "approved.json")
    experiment.save(runtime / "models" / "newer-experiment.json")
    (workbench / "config" / "active_models.json").write_text(json.dumps({
        "competitions": {"测试联赛": {"model": "approved", "status": "active_research"}}
    }), encoding="utf-8")
    (workbench / "config" / "team_aliases.json").write_text(json.dumps({
        "competitions": {"测试联赛": {"甲": "A", "乙": "B"}}
    }), encoding="utf-8")
    paths, aliases, _ = load_active_model_routing(workbench, runtime)
    assert paths["测试联赛"].stem == "approved"
    assert aliases["测试联赛"]["甲"] == "A"

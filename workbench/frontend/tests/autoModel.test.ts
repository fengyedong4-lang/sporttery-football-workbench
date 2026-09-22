import assert from "node:assert/strict";
import test from "node:test";

import { getAutoModelPresentation } from "../src/autoModel.ts";

test("旧候选行没有 auto_model 时保持兼容", () => {
  assert.equal(getAutoModelPresentation({ model_status: "evidence_fallback" }), null);
});

test("跨过开球的阻止状态优先于模型已训练", () => {
  const view = getAutoModelPresentation({analysis_status:"blocked",summary:"分析完成时已开赛",auto_model:{status:"trained",prior_dominated:true},independent_model:{analysis_result:"胜"}});
  assert.ok(view);
  assert.equal(view.status,"blocked");
  assert.equal(view.reason,"分析完成时已开赛");
  assert.equal(view.independent?.analysis_result,"胜"); // retained only as an audit value
});

test("自动模型展示样本、先验状态与两套独立概率", () => {
  const view = getAutoModelPresentation({
    model_status: "auto_trained",
    auto_model: {
      status: "trained",
      version: "auto-v1",
      training_matches: 42,
      training_cutoff: "2026-09-21",
      home_matches: 2,
      away_matches: 4,
      prior_dominated: true,
      source_count: 3,
      excluded: { future: 2 },
      evaluation: { status: "insufficient", denominator: 0, no_future_leakage: true },
      scope: { competition_id: "comp-1", season_id: "season-1" },
      limitations: ["概率未校准"],
    },
    independent_model: {
      analysis_method: "competition_shrinkage_poisson_v1",
      raw_probabilities: {
        result: { 胜: 0.512, 平: 0.278, 负: 0.21 },
        handicap_result: { 让胜: 0.301, 让平: 0.244, 让负: 0.455 },
      },
    },
  });

  assert.ok(view);
  assert.equal(view.statusLabel, "自动模型已训练");
  assert.equal(view.priorDominated, true);
  assert.equal(view.homeMatches, 2);
  assert.deepEqual(view.resultProbabilities.map((item) => item.text), ["51.2%", "27.8%", "21.0%"]);
  assert.deepEqual(view.handicapProbabilities.map((item) => item.text), ["30.1%", "24.4%", "45.5%"]);
});

test("自动建模受阻时保留真实原因且不生成概率", () => {
  const view = getAutoModelPresentation({
    auto_model: { status: "blocked", reason: "比赛身份未核验", training_matches: 0, source_count: 0, excluded: {}, evaluation: { status: "not_run" }, scope: {}, limitations: [] },
  });

  assert.ok(view);
  assert.equal(view.statusLabel, "自动建模受阻");
  assert.equal(view.reason, "比赛身份未核验");
  assert.deepEqual(view.resultProbabilities, []);
});

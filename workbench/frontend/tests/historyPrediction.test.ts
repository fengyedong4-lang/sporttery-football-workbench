import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));

async function loadNormalizer() {
  const server = await createServer({ root, appType: "custom", server: { middlewareMode: true } });
  const module = await server.ssrLoadModule("/src/HistoryPredictionPanel.tsx");
  return { normalizeHistoryMatch: module.normalizeHistoryMatch, close: () => server.close() };
}

test("旧版正式记录保留四项预测并读取旧让球", async () => {
  const loaded = await loadNormalizer();
  try {
    const view = loaded.normalizeHistoryMatch({
      prediction_date: "2026-08-04", prediction_version: 1, match_number: "周二001",
      competition: "巴西杯", home_team: "里莫", away_team: "桑托斯",
      official: { handicap: { line: 1 } },
      prediction: { result: "平", handicap_result: "让胜", score: "1:1", total_goals: 2, confidence: "中低" },
    }, 0);
    assert.equal(view.result, "平");
    assert.equal(view.handicapResult, "让胜");
    assert.equal(view.handicap, 1);
    assert.equal(view.legacyScore, "1:1");
    assert.equal(view.legacyTotalGoals, 2);
  } finally { await loaded.close(); }
});

test("v4记录读取两项正式选择、概率、理由和反证", async () => {
  const loaded = await loadNormalizer();
  try {
    const view = loaded.normalizeHistoryMatch({
      prediction_date: "2026-09-22", prediction_version: 2, match_number: "周二003",
      competition: "英锦标赛", home_team: "诺茨郡", away_team: "格里姆",
      official_data: { handicap: { official_handicap: -1 } },
      prediction: {
        predicted_result: "负", predicted_handicap_result: "让负", prediction_reason: "原判断理由",
        risk_level: "高", counter_evidence_effect: "英甲主场是主要反证",
        play_status: { result: { status: "已确认", selection: "负", confidence: "低" }, handicap_result: { status: "已确认", selection: "让负" } },
        three_way_probabilities: { home: 25, draw: 30, away: 45 },
      },
    }, 0);
    assert.equal(view.result, "负");
    assert.equal(view.handicapResult, "让负");
    assert.equal(view.handicap, -1);
    assert.equal(view.reason, "原判断理由");
    assert.deepEqual(view.counterEvidence, ["英甲主场是主要反证"]);
    assert.deepEqual(view.probabilities, { home: 25, draw: 30, away: 45 });
  } finally { await loaded.close(); }
});

test("未知旧记录类型安全降级并保留原始值", async () => {
  const loaded = await loadNormalizer();
  try {
    const nullView = loaded.normalizeHistoryMatch(null, 0);
    const stringView = loaded.normalizeHistoryMatch("legacy-row", 1);
    assert.equal(nullView.mappingAvailable, false);
    assert.equal(nullView.raw, null);
    assert.equal(nullView.result, undefined);
    assert.equal(stringView.mappingAvailable, false);
    assert.equal(stringView.raw, "legacy-row");
    assert.equal(stringView.matchNumber, "—");
  } finally { await loaded.close(); }
});

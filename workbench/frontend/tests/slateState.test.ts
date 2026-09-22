import assert from "node:assert/strict";
import test from "node:test";

import { deriveSelection, kickoffDate } from "../src/slateState.ts";

const fixtures = [
  { match_id: "1", sequence: 1, business_date: "2026-09-22", kickoff_time: "2026-09-23T00:30:00+08:00" },
  { match_id: "2", sequence: 2, business_date: "2026-09-22", kickoff_time: "2026-09-23T01:00:00+08:00" },
  { match_id: "3", sequence: 3, business_date: "2026-09-22", kickoff_time: "2026-09-23T02:00:00+08:00" },
  { match_id: "4", sequence: 4, business_date: "2026-09-22", kickoff_time: "2026-09-22T20:00:00+08:00" },
  { match_id: "5", sequence: 5, business_date: "2026-09-23", kickoff_time: "2026-09-23T18:00:00+08:00" },
  { match_id: "6", sequence: 6, business_date: "2026-09-23", kickoff_time: "2026-09-23T19:00:00+08:00" },
];

const slate = { fixtures };

test("北京时间开赛日筛出5场并为预测重新连续编号", () => {
  const next = deriveSelection(slate, "kickoff_date", "2026-09-23");
  assert.equal(next.fixtures.length, 5);
  assert.deepEqual(JSON.parse(next.payload).fixtures.map((item: { sequence: number }) => item.sequence), [1,2,3,4,5]);
});

test("竞彩编号日期筛出2场且切换必定使旧结果失效", () => {
  const next = deriveSelection(slate, "business_date", "2026-09-23");
  assert.equal(next.fixtures.length, 2);
  assert.equal(next.result, null);
});

test("UTC时间统一转换为北京时间日期", () => {
  assert.equal(kickoffDate("2026-09-22T16:30:00Z"), "2026-09-23");
});

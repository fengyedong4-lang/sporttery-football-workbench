export type Json = Record<string, any>;

export type SlateScope = "all" | "business_date" | "kickoff_date";

export const SCOPE_LABELS: Record<SlateScope, string> = {
  all: "全部官方赛单",
  business_date: "按竞彩编号日期",
  kickoff_date: "按北京时间开赛日",
};

export const EMPTY_PREDICTION_PAYLOAD = JSON.stringify(
  { fixtures: [], model_version: "auto", rule_budget: 6 },
  null,
  2,
);

export function beijingToday(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function kickoffDate(value: unknown): string {
  if (typeof value !== "string") return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return beijingToday(parsed);
}

function uniqueDates(values: unknown[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)))];
}

export function scopeDates(slate: Json | null, scope: SlateScope): string[] {
  if (!slate || scope === "all") return [];
  const fixtures = Array.isArray(slate.fixtures) ? slate.fixtures : [];
  if (scope === "business_date") {
    const supplied = Array.isArray(slate.business_dates) ? slate.business_dates : [];
    return uniqueDates(supplied.length ? supplied : fixtures.map((item: Json) => item.business_date));
  }
  const supplied = Array.isArray(slate.kickoff_dates) ? slate.kickoff_dates : [];
  return uniqueDates(supplied.length ? supplied : fixtures.map((item: Json) => kickoffDate(item.kickoff_time)));
}

export function filterFixtures(slate: Json | null, scope: SlateScope, date: string): Json[] {
  const fixtures = slate && Array.isArray(slate.fixtures) ? slate.fixtures : [];
  if (scope === "all") return fixtures;
  if (!date) return [];
  return fixtures.filter((fixture: Json) => (
    scope === "business_date"
      ? fixture.business_date === date
      : kickoffDate(fixture.kickoff_time) === date
  ));
}

export function buildPredictionPayload(fixtures: Json[]): string {
  return JSON.stringify(
    {
      fixtures: fixtures.map((fixture, index) => ({ ...fixture, sequence: index + 1 })),
      model_version: "auto",
      rule_budget: 6,
    },
    null,
    2,
  );
}

export function deriveSelection(slate: Json | null, scope: SlateScope, date: string) {
  const fixtures = filterFixtures(slate, scope, date);
  return {
    fixtures,
    payload: buildPredictionPayload(fixtures),
    result: null,
  };
}

export function slateIsComplete(slate: Json | null): boolean {
  if (!slate || slate.complete_for_scope !== true) return false;
  const invalidRows = Array.isArray(slate.invalid_rows) ? slate.invalid_rows : [];
  const validationErrors = Array.isArray(slate.validation_errors) ? slate.validation_errors : [];
  return invalidRows.length === 0 && validationErrors.length === 0;
}

export type PayloadValidation =
  | { ok: true; data: Json; fixtureCount: number }
  | { ok: false; message: string };

export function validatePredictionPayload(value: string): PayloadValidation {
  let data: unknown;
  try { data = JSON.parse(value); }
  catch { return { ok: false, message: "JSON 格式尚未完成" }; }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { ok: false, message: "预测输入必须是 JSON 对象" };
  }
  const document = data as Json;
  if (!Array.isArray(document.fixtures)) return { ok: false, message: "预测输入缺少 fixtures 数组" };
  if (document.fixtures.length === 0) return { ok: false, message: "当前范围为 0 场，不能运行预测" };
  const required = ["match_id", "match_number", "business_date", "competition", "home_team", "away_team", "kickoff_time"];
  const ids = new Set<string>();
  for (let index = 0; index < document.fixtures.length; index += 1) {
    const fixture = document.fixtures[index];
    if (!fixture || typeof fixture !== "object" || Array.isArray(fixture)) return { ok: false, message: `第 ${index + 1} 场不是有效对象` };
    const missing = required.filter((key) => fixture[key] === null || fixture[key] === undefined || fixture[key] === "");
    if (missing.length) return { ok: false, message: `第 ${index + 1} 场缺少 ${missing.join("、")}` };
    if (fixture.sequence !== index + 1) return { ok: false, message: "sequence 必须按当前范围连续排列为 1..N" };
    const id = String(fixture.match_id);
    if (ids.has(id)) return { ok: false, message: `match_id 重复：${id}` };
    ids.add(id);
  }
  return { ok: true, data: document, fixtureCount: document.fixtures.length };
}

export function removeSnapshotBinding(document: Json): Json {
  return {
    ...document,
    fixtures: Array.isArray(document.fixtures)
      ? document.fixtures.map((fixture: Json) => {
          const { source_snapshot_id: _ignored, ...rest } = fixture;
          return rest;
        })
      : document.fixtures,
  };
}

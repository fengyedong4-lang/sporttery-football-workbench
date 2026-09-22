export type JsonLike = Record<string, any>;

export type ProbabilityItem = {
  label: string;
  value: number;
  text: string;
};

export type AutoModelPresentation = {
  status: string;
  statusLabel: string;
  tone: "success" | "warning" | "blocked" | "neutral";
  reason: string;
  version: string;
  trainingMatches: number | null;
  trainingCutoff: string;
  homeMatches: number | null;
  awayMatches: number | null;
  priorDominated: boolean;
  sourceCount: number | null;
  competitionId: string;
  seasonId: string;
  artifactPath: string;
  datasetPath: string;
  limitations: string[];
  exclusions: string[];
  evaluation: JsonLike;
  independent: JsonLike | null;
  resultProbabilities: ProbabilityItem[];
  handicapProbabilities: ProbabilityItem[];
};

const asObject = (value: unknown): JsonLike | null => (
  value && typeof value === "object" && !Array.isArray(value) ? value as JsonLike : null
);

const asString = (value: unknown): string => (
  typeof value === "string" && value.trim() ? value.trim() : ""
);

const asCount = (value: unknown): number | null => (
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null
);

const textList = (value: unknown): string[] => (
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : []
);

const STATUS_LABELS: Record<string, {label: string; tone: AutoModelPresentation["tone"]}> = {
  trained: { label: "自动模型已训练", tone: "success" },
  unavailable: { label: "本场无法自动建模", tone: "warning" },
  blocked: { label: "自动建模受阻", tone: "blocked" },
};

const probabilityItems = (value: unknown, labels: string[]): ProbabilityItem[] => {
  const probabilities = asObject(value);
  if (!probabilities) return [];
  return labels.flatMap((label) => {
    const probability = probabilities[label];
    if (typeof probability !== "number" || !Number.isFinite(probability) || probability < 0 || probability > 1) return [];
    return [{ label, value: probability, text: `${(probability * 100).toFixed(1)}%` }];
  });
};

const exclusionItems = (value: unknown): string[] => {
  const exclusions = asObject(value);
  if (!exclusions) return [];
  return Object.entries(exclusions).map(([key, item]) => {
    if (Array.isArray(item)) return `${key}：${item.length ? item.join("、") : "0"}`;
    if (item && typeof item === "object") return `${key}：${JSON.stringify(item)}`;
    return `${key}：${String(item)}`;
  });
};

export function getAutoModelPresentation(match: JsonLike): AutoModelPresentation | null {
  const autoModel = asObject(match.auto_model);
  if (!autoModel) return null;
  const independent = asObject(match.independent_model);
  const scope = asObject(autoModel.scope) || {};
  const evaluation = asObject(autoModel.evaluation) || {};
  const rawProbabilities = asObject(independent?.raw_probabilities) || {};
  const blocked = match.analysis_status === "blocked";
  const status = blocked ? "blocked" : asString(autoModel.status) || "unknown";
  const statusView = STATUS_LABELS[status] || { label: "自动建模状态未报告", tone: "neutral" as const };

  return {
    status,
    statusLabel: statusView.label,
    tone: statusView.tone,
    reason: blocked ? asString(match.summary || match.analysis_summary) || "分析已阻止，保留数值仅供审计" : asString(autoModel.reason),
    version: asString(autoModel.version),
    trainingMatches: asCount(autoModel.training_matches),
    trainingCutoff: asString(autoModel.training_cutoff),
    homeMatches: asCount(autoModel.home_matches),
    awayMatches: asCount(autoModel.away_matches),
    priorDominated: autoModel.prior_dominated === true,
    sourceCount: asCount(autoModel.source_count),
    competitionId: asString(scope.competition_id),
    seasonId: asString(scope.season_id),
    artifactPath: asString(autoModel.artifact_path),
    datasetPath: asString(autoModel.dataset_path),
    limitations: textList(autoModel.limitations),
    exclusions: exclusionItems(autoModel.excluded),
    evaluation,
    independent,
    resultProbabilities: probabilityItems(rawProbabilities.result, ["胜", "平", "负"]),
    handicapProbabilities: probabilityItems(rawProbabilities.handicap_result, ["让胜", "让平", "让负"]),
  };
}

export function compactMetric(value: unknown, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

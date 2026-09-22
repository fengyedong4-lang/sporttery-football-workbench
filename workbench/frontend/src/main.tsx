import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import "./styles.css";
import {
  EMPTY_PREDICTION_PAYLOAD,
  SCOPE_LABELS,
  beijingToday,
  deriveSelection,
  filterFixtures,
  removeSnapshotBinding,
  scopeDates,
  slateIsComplete,
  validatePredictionPayload,
  type Json,
  type SlateScope,
} from "./slateState";
import { compactMetric, getAutoModelPresentation, type ProbabilityItem } from "./autoModel";
import ResearchDetails from "./ResearchDetails";
import ReviewPanel from "./ReviewPanel";
import HistoryPredictionPanel from "./HistoryPredictionPanel";

declare global {
  interface Window { __SPORTTERY_ROOT__?: Root }
}

type PayloadOrigin =
  | { kind: "none" }
  | { kind: "manual" }
  | { kind: "official"; snapshotId: string; scope: SlateScope; filterDate: string; fixtureCount: number };

type ResultContext = {
  kind: "official" | "manual" | "saved";
  scopeLabel: string;
  filterDate?: string;
  snapshotId?: string;
  observedAt?: string;
  fixtureCount: number;
};

const api = async (path: string, options?: RequestInit): Promise<Json> => {
  const response = await fetch(path, options);
  const raw = await response.text();
  let body: Json = {};
  if (raw) {
    try { body = JSON.parse(raw); }
    catch { throw new Error(`服务返回了无法识别的内容（HTTP ${response.status}）`); }
  }
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
};

const statusText = (value: boolean) => value ? "一致" : "有差异";
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const isAbortError = (error: unknown) => error instanceof DOMException && error.name === "AbortError";
const shortId = (value: unknown) => typeof value === "string" && value ? `${value.slice(0, 12)}…` : "—";
const displayValue = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);

const displayBeijingTime = (value: unknown) => {
  if (typeof value !== "string" || !value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(parsed).replaceAll("/", "-");
};

const stringList = (value: unknown): string[] => Array.isArray(value)
  ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
  : [];

const jsonList = (value: unknown): Json[] => Array.isArray(value)
  ? value.filter((item): item is Json => Boolean(item) && typeof item === "object" && !Array.isArray(item))
  : [];

const uniqueText = (...values: unknown[]): string[] => [...new Set(values.flatMap(stringList))];

const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  available: "证据分析可用",
  insufficient_evidence: "证据不足",
  blocked: "证据获取受阻",
};

const ANALYSIS_METHOD_LABELS: Record<string, string> = {
  recent_form_poisson_v1: "近期战绩 Poisson 证据分析",
  codex_evidence_v1: "Codex 证据分析",
  competition_shrinkage_poisson_v1: "赛事收缩 Poisson 自动模型",
  competition_2025_history_shrinkage_poisson_v3: "2025年起赛事独立 Poisson 模型",
};

const OFFICIAL_SOURCE_DOMAINS = [
  "sporttery.cn", "lottery.gov.cn", "fifa.com", "uefa.com", "the-afc.com", "cafonline.com",
  "concacaf.com", "conmebol.com", "oceaniafootball.com", "premierleague.com", "thefa.com",
  "bundesliga.com", "dfb.de", "laliga.com", "rfef.es", "legaseriea.it", "figc.it",
  "ligue1.com", "fff.fr", "jleague.co", "jfa.jp",
];

const safeOfficialSourceUrl = (value: unknown): string | null => {
  if (typeof value !== "string" || !value) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
    const hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
    return OFFICIAL_SOURCE_DOMAINS.some((domain) => hostname === domain || hostname.endsWith(`.${domain}`))
      ? parsed.toString()
      : null;
  } catch { return null; }
};

const scalarText = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const confidenceText = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "低（未校准）";
  const entries = Object.entries(value as Json);
  return entries.length ? entries.map(([key,item]) => `${key}: ${scalarText(item)}`).join("；") : "低（未校准）";
};

const analysisMethodText = (match: Json) => {
  const method = typeof match.analysis_method === "string" ? match.analysis_method : "";
  if (method) return ANALYSIS_METHOD_LABELS[method] || method;
  if (match.model_status === "auto_trained") return "赛事收缩 Poisson 自动模型";
  if (match.model_version) return `${match.model_version}${match.model_training_matches ? ` · ${match.model_training_matches}场` : ""}`;
  return match.model_status === "unavailable" ? "本场未形成可用模型" : "—";
};

const selectionSummary = (primary: unknown, status: unknown, potential: unknown) => {
  const official = displayValue(primary || status);
  const hasPotential = potential !== null && potential !== undefined && potential !== "" && potential !== primary;
  return <div className="selection-summary"><b>{official}</b>{hasPotential&&<small>潜在方向：{displayValue(potential)}（非开售选项）</small>}</div>;
};

const matchStatusSummary = (match: Json) => {
  const autoModel = getAutoModelPresentation(match);
  const analysisStatus = typeof match.analysis_status === "string" ? match.analysis_status : "";
  const status = autoModel
    ? autoModel.statusLabel
    : analysisStatus
    ? (ANALYSIS_STATUS_LABELS[analysisStatus] || analysisStatus)
    : match.model_status === "available" ? "专属模型可用" : match.model_status === "evidence_fallback" ? "证据分析回退" : "本场未形成可用分析";
  const detail = analysisStatus === "blocked" ? "不可作为赛前候选" : autoModel?.priorDominated ? "先验主导 · 低置信" : match.analysis_label || match.consistency;
  const reason = autoModel ? autoModel.reason : match.reason;
  return <div className="match-status"><b>{status}</b>{detail&&<span>{displayValue(detail)}</span>}{reason&&<small>{autoModel?"自动建模":"模型状态"}：{displayValue(reason)}</small>}</div>;
};

const ProbabilityStrip = ({title,items}:{title:string;items:ProbabilityItem[]}) => <div className="probability-block">
  <h4>{title}</h4>
  {items.length>0?<div className="probability-strip">{items.map(item=><span key={item.label}><b>{item.label}</b><strong>{item.text}</strong></span>)}</div>:<p>无可展示概率（可能未取得官方让球，或模型未形成该玩法结果）。</p>}
</div>;

const AutoModelDetails = ({match}:{match:Json}) => {
  const view = getAutoModelPresentation(match);
  if (!view) return null;
  const independent = view.independent;
  const evaluation = view.evaluation;
  const evaluationLabel = ({ok:"已完成时间切分评估",not_enough_data:"样本不足",prior_transfer_not_validated:"旧赛季先验迁移尚未验证"} as Record<string,string>)[evaluation.status] || displayValue(evaluation.status);
  const evaluationParts = [
    `状态 ${evaluationLabel}`,
    `分母 ${displayValue(evaluation.denominator)}`,
    `Brier ${compactMetric(evaluation.brier)}`,
    `基线 Brier ${compactMetric(evaluation.baseline_brier ?? evaluation.baseline?.brier)}`,
    `对数损失 ${compactMetric(evaluation.log_loss)}`,
    `准确率 ${compactMetric(evaluation.accuracy)}`,
    `未来泄漏 ${evaluation.no_future_leakage===true?"未检出":evaluation.no_future_leakage===false?"未通过":"未报告"}`,
  ];
  return <section className="auto-model-layer">
    <div className="layer-heading"><div><h3>自动建模状态</h3><p>{match.auto_model?.prior_only?"当前代际样本不足：旧赛季仅用于本赛事进球环境，未迁移旧代际球队实力。":match.auto_model?.historical_cache?"首次建模采集2025年起本赛事可核验历史，随后复用版本缓存；当次人员及战术另行检索。":"按留档模型所注明的赛事和样本范围分析。"}</p></div><span className={`model-badge ${view.tone}`}>{view.statusLabel}</span></div>
    {view.reason&&<p className="model-reason">原因：{view.reason}</p>}
    <div className="model-facts">
      <span><small>赛事 / 赛季</small><b>{displayValue(view.competitionId)} / {displayValue(view.seasonId)}</b></span>
      <span><small>训练样本</small><b>{view.trainingMatches??"未报告"} 场</b></span>
      <span><small>双方自身样本</small><b>主队 {view.homeMatches??"—"} · 客队 {view.awayMatches??"—"}</b></span>
      <span><small>训练截止</small><b>{displayValue(view.trainingCutoff)}</b></span>
      <span><small>数据来源</small><b>{view.sourceCount??"未报告"} 个</b></span>
      <span><small>模型版本</small><b>{displayValue(view.version)}</b></span>
    </div>
    <p className={view.priorDominated?"prior-warning":"model-note"}>{view.priorDominated?"先验主导：双方本季自身样本较少，模型更多依赖上方注明来源的本赛事基准。":"双方样本已进入模型；这不等于概率已经校准或可靠性已经验证。"}</p>
    <div className="evaluation-line">样本外评估：{evaluationParts.join(" · ")}</div>
    {match.auto_model?.historical_cache&&<p className="model-note">历史缓存：{match.auto_model.historical_cache.cache_hit?"本次复用":"本次首次采集"} · 请求范围 {displayValue(match.auto_model.historical_cache.requested_start)} 至 {displayValue(match.auto_model.historical_cache.requested_end)} · 实际范围 {displayValue(match.auto_model.historical_cache.coverage?.first_match)} 至 {displayValue(match.auto_model.historical_cache.coverage?.last_match)} · {displayValue(match.auto_model.historical_cache.coverage?.reason)}</p>}
    {(view.datasetPath||view.artifactPath)&&<div className="trace-paths">{view.datasetPath&&<span>数据集：{view.datasetPath}</span>}{view.artifactPath&&<span>模型产物：{view.artifactPath}</span>}</div>}
    {view.exclusions.length>0&&<p className="model-note">排除记录：{view.exclusions.join("；")}</p>}
    {view.limitations.length>0&&<div className="model-limitations"><h4>模型限制</h4><ul>{view.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
    <div className="independent-model">
      {match.analysis_status==="blocked"&&<p className="prior-warning">分析已阻止；以下独立模型方向与概率仅供历史审计，不可作为赛前候选。</p>}
      <div className="layer-heading"><div><h3>独立自动模型</h3><p>这组概率来自自动模型，不是 Astra 概率。</p></div>{independent?.analysis_label&&<span className="layer-label">{displayValue(independent.analysis_label)}</span>}</div>
      {independent?<><p>方法：{ANALYSIS_METHOD_LABELS[independent.analysis_method]||displayValue(independent.analysis_method)} · 模型方向：胜平负 {displayValue(independent.analysis_result)}；让球胜平负 {displayValue(independent.analysis_handicap_result)}</p><div className="probability-grid"><ProbabilityStrip title="胜平负原始概率" items={view.resultProbabilities}/><ProbabilityStrip title="让球胜平负原始概率" items={view.handicapProbabilities}/></div></>:<p>后端未返回独立模型分析。</p>}
      <p className="uncalibrated-note">原始概率未校准；本场候选未冻结，不能据此声称模型可靠性已验证。</p>
    </div>
  </section>;
};

const EvidenceDetails = ({match}:{match:Json}) => {
  const evidence = match.evidence && typeof match.evidence === "object" && !Array.isArray(match.evidence) ? match.evidence as Json : {};
  const supporting = stringList(match.supporting_evidence);
  const counterevidence = stringList(match.major_counterevidence);
  const counterActions = jsonList(match.counterevidence_actions);
  const missing = uniqueText(match.missing,evidence.missing);
  const warnings = stringList(evidence.warnings);
  const facts = jsonList(evidence.facts);
  const sources = jsonList(evidence.sources);
  const potentialResult = match.analysis_result || "未形成";
  const potentialHandicap = match.analysis_handicap_result || "未形成";
  const isEvidenceFallback = match.model_status === "evidence_fallback" || Boolean(match.analysis_status);
  const autoModel = getAutoModelPresentation(match);
  const isAstraLayer = match.llm_status === "completed" || match.analysis_method === "codex_evidence_v1";
  return <details className="evidence-details">
    <summary>{autoModel?"查看自动模型与分析分层":isEvidenceFallback?"查看证据详情":"查看分析详情"}</summary>
    <div className="evidence-content">
      <div className="evidence-caveat">{autoModel?"自动模型概率与 Astra 定性解释分层展示；概率未经校准，候选结果尚未冻结。":isEvidenceFallback?"低置信、未经概率校准；这是证据型回退，不是专属训练模型；候选结果尚未冻结。":"当前为旧版专属模型候选行；如未返回证据包，页面不会补造证据；候选结果尚未冻结。"}</div>
      <AutoModelDetails match={match}/>
      <ResearchDetails match={match}/>
      <div className="layer-heading analysis-layer-heading"><div><h3>{isAstraLayer?"Astra 定性解释":"分析结论"}</h3>{isAstraLayer&&<p>依据证据形成方向与文字解释；不会把独立模型概率标成 Astra 概率。</p>}</div>{isAstraLayer&&<span className="layer-label">LLM {displayValue(match.llm_status)}</span>}</div>
      <div className="evidence-grid">
        <section><h3>结论</h3><p><b>{displayValue(match.analysis_label || ANALYSIS_STATUS_LABELS[match.analysis_status] || "候选分析")}</b></p><p>{displayValue(match.summary || match.analysis_summary)}</p><p>潜在方向：胜平负 {displayValue(potentialResult)}；让球胜平负 {displayValue(potentialHandicap)}</p><p>置信信息：{confidenceText(match.confidence)}</p></section>
        <section><h3>支持证据</h3>{supporting.length?<ul>{supporting.map((item,index)=><li key={index}>{item}</li>)}</ul>:<p>未取得足够支持证据。</p>}</section>
        <section><h3>反向证据及影响</h3>{counterevidence.length?<ul>{counterevidence.map((item,index)=><li key={`counter-${index}`}>{item}</li>)}</ul>:<p>未记录主要反向证据。</p>}{counterActions.length>0&&<ul className="impact-list">{counterActions.map((item,index)=><li key={`effect-${index}`}><b>{displayValue(item.evidence)}</b><span>影响：{displayValue(item.effect)}</span></li>)}</ul>}</section>
        <section><h3>证据缺口</h3>{missing.length?<ul>{missing.map((item,index)=><li key={index}>{item}</li>)}</ul>:<p>未记录额外缺口。</p>}{warnings.length>0&&<p className="evidence-warning">警告：{warnings.join("；")}</p>}</section>
      </div>
      {facts.length>0&&<section className="evidence-facts"><h3>已采集事实</h3><ul>{facts.map((fact,index)=><li key={fact.fact_id||index}><b>{displayValue(fact.dimension)}</b>：{displayValue(fact.summary)}</li>)}</ul></section>}
      <section className="evidence-sources"><h3>来源与获取时间</h3><p>身份核验：{evidence.identity_verified===true?"已通过":evidence.identity_verified===false?"未通过":"未报告"}；证据汇集：{displayBeijingTime(evidence.collected_at)}</p>{sources.length?<ul>{sources.map((source,index)=>{const safeUrl=safeOfficialSourceUrl(source.url); return <li key={source.source_id||index}><b>{displayValue(source.source_id)}</b> · 获取 {displayBeijingTime(source.fetched_at)}{source.source_updated_at&&<> · 来源更新 {displayBeijingTime(source.source_updated_at)}</>}<br/>{safeUrl?<a href={safeUrl} target="_blank" rel="noreferrer">{displayValue(source.url)}</a>:<span className="plain-url">{displayValue(source.url)}</span>}</li>;})}</ul>:<p>未取得可展示来源。</p>}</section>
      <p className="evidence-meta">LLM 状态：{displayValue(match.llm_status)} · 风险：{displayValue(match.risk)} · 冻结资格：{match.freeze_eligible===true?"可冻结":"不可冻结"}</p>
    </div>
  </details>;
};

const llmUsageText = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "未提供";
  const usage = value as Json;
  return `输入 ${scalarText(usage.input_tokens)}，缓存输入 ${scalarText(usage.cached_input_tokens)}，输出 ${scalarText(usage.output_tokens)}`;
};

const playSummary = (play: Json | null | undefined, handicap = false) => {
  const labels = handicap ? ["让胜", "让平", "让负"] : ["主胜", "平", "客胜"];
  if (!play) return <><span className="play-status">状态未获取</span><span className="odds">{labels[0]} — · {labels[1]} — · {labels[2]} —</span></>;
  return <><span className="play-status">{displayValue(play.status)}</span><span className="odds">{labels[0]} {displayValue(play.home)} · {labels[1]} {displayValue(play.draw)} · {labels[2]} {displayValue(play.away)}</span></>;
};

function App() {
  const [tab, setTab] = useState("workbench");
  const [health, setHealth] = useState<Json | null>(null);
  const [quality, setQuality] = useState<Json | null>(null);
  const [models, setModels] = useState<Json[]>([]);
  const [archivedModelCount, setArchivedModelCount] = useState(0);
  const [showArchivedModels, setShowArchivedModels] = useState(false);
  const [history, setHistory] = useState<Json>({ items: [], total: 0 });
  const [backtests, setBacktests] = useState<Json[]>([]);
  const [datasets, setDatasets] = useState<Json>({datasets:[],dataset_count:0,training_match_count:0});
  const [historicalCoverage, setHistoricalCoverage] = useState<Json>({items:[],errors:[]});
  const [requestDate, setRequestDate] = useState(beijingToday);
  const [slate, setSlate] = useState<Json | null>(null);
  const [scope, setScope] = useState<SlateScope>("all");
  const [filterDate, setFilterDate] = useState("");
  const [trainForm, setTrainForm] = useState({csv_path:"imports/clean/E0_2024_2025_normalized.csv",competition:"英超",model_type:"dixon_coles",decay:0.003,model_name:""});
  const [backtestForm, setBacktestForm] = useState({csv_path:"imports/clean/E0_2024_2025_normalized.csv",competition:"英超",model_type:"dixon_coles",output_name:"",min_train:150,refit_every:100});
  const [payload, setPayload] = useState(EMPTY_PREDICTION_PAYLOAD);
  const [payloadOrigin, setPayloadOrigin] = useState<PayloadOrigin>({kind:"none"});
  const [result, setResult] = useState<Json | null>(null);
  const [resultContext, setResultContext] = useState<ResultContext | null>(null);
  const [systemError, setSystemError] = useState("");
  const [workbenchError, setWorkbenchError] = useState("");
  const [taskError, setTaskError] = useState("");
  const [systemBusy, setSystemBusy] = useState(false);
  const [officialBusy, setOfficialBusy] = useState(false);
  const [predictionBusy, setPredictionBusy] = useState(false);
  const [savedDraftBusy, setSavedDraftBusy] = useState(false);
  const [taskBusy, setTaskBusy] = useState(false);
  const initialLoadStarted = useRef(false);
  const officialRequestSequence = useRef(0);
  const predictionRequestSequence = useRef(0);
  const officialController = useRef<AbortController | null>(null);
  const predictionController = useRef<AbortController | null>(null);

  const availableDates = useMemo(() => scopeDates(slate, scope), [slate, scope]);
  const visibleFixtures = useMemo(() => filterFixtures(slate, scope, filterDate), [slate, scope, filterDate]);
  const payloadValidation = useMemo(() => validatePredictionPayload(payload), [payload]);
  const sourceMatchCount = slate?.source_match_count ?? slate?.match_count ?? 0;
  const combinedError = workbenchError || taskError || systemError;

  const invalidatePrediction = () => {
    predictionRequestSequence.current += 1;
    predictionController.current?.abort();
    predictionController.current = null;
    setPredictionBusy(false);
    setSavedDraftBusy(false);
    setResult(null);
    setResultContext(null);
  };

  const bindOfficialPayload = (nextSlate: Json, nextScope: SlateScope, nextFilterDate: string) => {
    invalidatePrediction();
    const selection = deriveSelection(nextSlate,nextScope,nextFilterDate);
    setPayload(selection.payload);
    setResult(selection.result);
    setPayloadOrigin({kind:"official",snapshotId:String(nextSlate.snapshot_id||""),scope:nextScope,filterDate:nextFilterDate,fixtureCount:selection.fixtures.length});
  };

  const refreshSystemStatus = async () => {
    setSystemBusy(true); setSystemError("");
    try {
      const [h,q,m,b,d,c] = await Promise.all([api("/api/health"),api("/api/data-quality"),api("/api/models"),api("/api/backtests"),api("/api/datasets"),api("/api/historical-coverage")]);
      setHealth(h); setQuality(q); setModels(m.items||[]); setArchivedModelCount(m.archived_count||0); setShowArchivedModels(false); setBacktests(b.items||[]); setDatasets(d); setHistoricalCoverage(c);
    } catch (error) { setSystemError(errorText(error)); }
    finally { setSystemBusy(false); }
  };

  const toggleModelArchive = async () => {
    setTaskBusy(true); setTaskError("");
    try {
      const next = !showArchivedModels;
      const data = await api(`/api/models?include_archived=${next}`);
      setModels(data.items||[]); setArchivedModelCount(data.archived_count||0); setShowArchivedModels(next);
    } catch (error) { setTaskError(errorText(error)); }
    finally { setTaskBusy(false); }
  };

  const fetchOfficialSlate = async () => {
    const requestedDate = beijingToday();
    setRequestDate(requestedDate);
    const sequence = officialRequestSequence.current + 1;
    officialRequestSequence.current = sequence;
    officialController.current?.abort();
    const controller = new AbortController();
    officialController.current = controller;
    invalidatePrediction();
    setOfficialBusy(true); setWorkbenchError(""); setSlate(null); setScope("all"); setFilterDate("");
    setPayload(EMPTY_PREDICTION_PAYLOAD); setPayloadOrigin({kind:"none"});
    try {
      const data = await api("/api/official/slate", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({business_date:requestedDate,date_basis:"all"}), signal:controller.signal,
      });
      if (sequence !== officialRequestSequence.current) return;
      setSlate(data); setScope("all"); setFilterDate(""); bindOfficialPayload(data,"all","");
    } catch(error) {
      if (sequence !== officialRequestSequence.current || isAbortError(error)) return;
      setSlate(null); setPayload(EMPTY_PREDICTION_PAYLOAD); setPayloadOrigin({kind:"none"});
      setWorkbenchError(`官方赛程未获取：${errorText(error)}`);
    } finally {
      if (sequence === officialRequestSequence.current) { setOfficialBusy(false); officialController.current = null; }
    }
  };

  useEffect(() => {
    if (initialLoadStarted.current) return;
    initialLoadStarted.current = true;
    void refreshSystemStatus();
    void fetchOfficialSlate();
  }, []);

  const changeScope = (nextScope: SlateScope) => {
    if (!slate) return;
    const dates = scopeDates(slate,nextScope);
    const nextDate = nextScope==="all"?"":(dates.includes(filterDate)?filterDate:(dates[0]||""));
    setScope(nextScope); setFilterDate(nextDate); setWorkbenchError(""); bindOfficialPayload(slate,nextScope,nextDate);
  };

  const changeFilterDate = (nextDate: string) => {
    if (!slate) return;
    setFilterDate(nextDate); setWorkbenchError(""); bindOfficialPayload(slate,scope,nextDate);
  };

  const editPayload = (value: string) => {
    invalidatePrediction(); setPayload(value); setPayloadOrigin({kind:"manual"}); setWorkbenchError("");
  };

  const predictionBlockReason = (() => {
    if (savedDraftBusy) return "正在读取已保存草稿";
    if (officialBusy) return "正在获取官方赛程";
    if (predictionBusy) return "正在获取证据并分析（可能需要数分钟）";
    if (!payloadValidation.ok) return payloadValidation.message;
    if (payloadOrigin.kind === "official") {
      if (!slate || payloadOrigin.snapshotId !== String(slate.snapshot_id||"")) return "预测输入未绑定当前官方快照";
      if (!slateIsComplete(slate)) return "官方快照未通过完整校验，不能预测";
      if (visibleFixtures.length===0) return "当前范围为 0 场，不能预测";
    }
    if (payloadOrigin.kind==="none") return "尚未取得可预测赛单";
    return "";
  })();

  const predict = async () => {
    if (predictionBlockReason || !payloadValidation.ok) return;
    const sequence = predictionRequestSequence.current + 1;
    predictionRequestSequence.current = sequence;
    predictionController.current?.abort();
    const controller = new AbortController();
    predictionController.current = controller;
    const submitted = payloadOrigin.kind==="manual"?removeSnapshotBinding(payloadValidation.data):payloadValidation.data;
    const context: ResultContext = payloadOrigin.kind==="official"
      ? {kind:"official",scopeLabel:SCOPE_LABELS[payloadOrigin.scope],filterDate:payloadOrigin.filterDate||undefined,snapshotId:payloadOrigin.snapshotId,observedAt:slate?.observed_at,fixtureCount:payloadValidation.fixtureCount}
      : {kind:"manual",scopeLabel:"高级 JSON 手动输入（未绑定官方快照）",fixtureCount:payloadValidation.fixtureCount};
    setPredictionBusy(true); setWorkbenchError(""); setResult(null); setResultContext(null);
    try {
      const data = await api("/api/predictions/daily", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(submitted),signal:controller.signal});
      if (sequence !== predictionRequestSequence.current) return;
      setResult(data); setResultContext(context);
    } catch (error) {
      if (sequence !== predictionRequestSequence.current || isAbortError(error)) return;
      setWorkbenchError(errorText(error));
    } finally {
      if (sequence===predictionRequestSequence.current) { setPredictionBusy(false); predictionController.current=null; }
    }
  };

  const loadSavedDraft = async () => {
    invalidatePrediction();
    const sequence = ++predictionRequestSequence.current;
    const controller = new AbortController();
    predictionController.current = controller;
    setSavedDraftBusy(true); setWorkbenchError("");
    try {
      const data = await api("/api/drafts/latest", {signal:controller.signal});
      if (sequence !== predictionRequestSequence.current) return;
      setResult(data);
      const snapshots = [...new Set((data.matches||[]).map((m:Json)=>m.source_snapshot_id).filter(Boolean))];
      setResultContext({kind:"saved",scopeLabel:"已保存草稿（只读，不代表当前赛前建议）",fixtureCount:data.matches?.length||0,snapshotId:snapshots.length===1?String(snapshots[0]):undefined});
    } catch(error) {
      if (sequence === predictionRequestSequence.current && !isAbortError(error)) setWorkbenchError(errorText(error));
    } finally {
      if (sequence === predictionRequestSequence.current) { setSavedDraftBusy(false); predictionController.current=null; }
    }
  };

  const loadHistory = async () => {
    setTaskBusy(true); setTaskError("");
    try { setHistory(await api("/api/history?page=1&page_size=20")); }
    catch (error) { setTaskError(errorText(error)); } finally { setTaskBusy(false); }
  };

  const runTrain = async () => {
    setTaskBusy(true); setTaskError("");
    try { await api("/api/training/train",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(trainForm)}); await refreshSystemStatus(); }
    catch(error){ setTaskError(errorText(error)); } finally { setTaskBusy(false); }
  };

  const runBacktest = async () => {
    setTaskBusy(true); setTaskError("");
    try { await api("/api/backtests/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(backtestForm)}); await refreshSystemStatus(); }
    catch(error){ setTaskError(errorText(error)); } finally { setTaskBusy(false); }
  };

  return <div className="shell">
    <aside>
      <div className="brand"><span>体彩足球</span><strong>预测与复盘工作台</strong></div>
      {[["workbench","当日工作台"],["predictions","历史预测"],["history","正式记录明细"],["model","复盘与模型"],["settings","设置与状态"]].map(([key,label])=><button className={tab===key?"active":""} key={key} onClick={()=>setTab(key)}>{label}</button>)}
      <div className="side-note">本地模式<br/>官方赛单：进入页面获取一次<br/>预测：补证据 → 建模 → Astra<br/>术数：关闭</div>
    </aside>
    <main>
      <header><div><h1>{tab==="workbench"?"当日工作台":tab==="predictions"?"历史预测":tab==="history"?"正式记录明细":tab==="model"?"复盘与模型":"设置与状态"}</h1><p>官方赛单、候选预测、正式冻结、复盘互不自动串联</p></div><button className="ghost" onClick={refreshSystemStatus} disabled={systemBusy}>{systemBusy?"刷新中…":"刷新系统状态"}</button></header>
      {combinedError && <div className="alert">操作失败：{combinedError}</div>}
      {tab==="workbench" && <>
        <section className="metrics">
          <article><label>官方赛单</label><b>{officialBusy?"获取中…":slate?`${visibleFixtures.length} / ${sourceMatchCount} 场`:"未获取"}</b><small>{slate?`当前筛选 / 官方原始 · 快照 ${shortId(slate.snapshot_id)}`:"未获取到官方数据时不会沿用旧赛单"}</small></article>
          <article><label>2025年起历史样本</label><b>{jsonList(historicalCoverage.items).reduce((total,c)=>total+(c.coverage?.match_count||0),0)} 场</b><small>{jsonList(historicalCoverage.items).length} 个赛事缓存 · 实际来源覆盖</small></article>
          <article><label>正式写入</label><b>{health?.formal_write_enabled?"已启用":"已关闭"}</b><small>须环境开关+明确动作</small></article>
          <article><label>大模型</label><b>{health?.llm||"未知"}</b><small>后端真实状态；CLI 不可用不会伪装启用</small></article>
        </section>

        <section className="panel official-panel">
          <div className="panel-title"><div><h2>官方当前公布赛单</h2><p>进入页面自动获取当前已公布的全部官方赛单；不轮询、不自动预测。按钮只更新这一次快照。</p></div><button className="ghost" onClick={fetchOfficialSlate} disabled={officialBusy||predictionBusy}>{officialBusy?"获取中…":"刷新官方赛程"}</button></div>
          <div className="source-strip"><span>请求基准：北京时间 {requestDate}</span><span>官方更新时间：{displayValue(slate?.last_update_time)}</span><span>本次获取时间：{displayBeijingTime(slate?.observed_at||slate?.fetched_at)}</span><span>当前筛选 {visibleFixtures.length} / 官方原始 {sourceMatchCount} 场</span>{slate?.source_reported_total!==null&&slate?.source_reported_total!==undefined&&<span>官方报告总数：{slate.source_reported_total}</span>}</div>
          {slate && <div className="filter-bar">
            <label>赛单范围<select value={scope} onChange={event=>changeScope(event.target.value as SlateScope)} disabled={officialBusy||predictionBusy}><option value="all">全部官方赛单</option><option value="business_date">按竞彩编号日期</option><option value="kickoff_date">按北京时间开赛日</option></select></label>
            {scope!=="all"&&<label>{scope==="business_date"?"竞彩编号日期":"北京时间开赛日"}<select value={filterDate} onChange={event=>changeFilterDate(event.target.value)} disabled={officialBusy||predictionBusy}>{availableDates.map(date=><option value={date} key={date}>{date}</option>)}</select></label>}
            <span className={slateIsComplete(slate)?"validation-ok":"validation-bad"}>{slateIsComplete(slate)?"官方快照完整校验通过":"官方快照未通过完整校验"}</span>
          </div>}
          {officialBusy?<div className="empty">正在获取官方当前公布赛单…</div>:!slate?<div className="empty warning-empty">官方赛程尚未获取，当前没有可认证的预测输入。</div>:visibleFixtures.length===0?<div className="empty warning-empty">当前官方公布赛单中没有符合此筛选的比赛；这不等于该自然日没有任何足球比赛。</div>:<div className="table-wrap schedule-table"><table><thead><tr><th>编号</th><th>赛事</th><th>主队</th><th>客队</th><th>竞彩日期</th><th>北京时间开赛</th><th>让球</th><th>胜平负状态 / 奖金</th><th>让球胜平负状态 / 奖金</th></tr></thead><tbody>{visibleFixtures.map((fixture:Json)=><tr key={fixture.match_id}><td><b>{displayValue(fixture.match_number)}</b></td><td>{displayValue(fixture.competition)}</td><td>{displayValue(fixture.home_team)}</td><td>{displayValue(fixture.away_team)}</td><td>{displayValue(fixture.business_date)}</td><td>{displayBeijingTime(fixture.kickoff_time)}<small>UTC+8</small></td><td>{displayValue(fixture.official_handicap)}</td><td className="play-cell">{playSummary(fixture.result_play)}</td><td className="play-cell">{playSummary(fixture.handicap_play,true)}</td></tr>)}</tbody></table></div>}
          {slate&&!slateIsComplete(slate)&&<div className="inline-warning">完整性校验未通过：无效行 {Array.isArray(slate.invalid_rows)?slate.invalid_rows.length:0} 条，校验错误 {Array.isArray(slate.validation_errors)?slate.validation_errors.length:0} 条。预测已禁用。</div>}
          {Array.isArray(slate?.warnings)&&slate.warnings.length>0&&<ul className="warnings">{slate.warnings.map((warning:unknown,index:number)=><li key={index}>{displayValue(warning)}</li>)}</ul>}
          {slate?.source_url&&<p className="foot source-url">官方来源：<a href={slate.source_url} target="_blank" rel="noreferrer">{slate.source_url}</a></p>}
        </section>

        <section className="panel"><div className="panel-title"><div><h2>日常预测（模式 A）</h2><p>当前筛选会同步生成预测输入；点击后依次自动补充可核验证据、构建或调用模型，再由 Astra 做定性分析。切换范围、刷新官方赛程或编辑 JSON 都会清除旧结果。</p></div><span className="tag">候选，未冻结</span></div>
          <div className="payload-status"><b>{payloadOrigin.kind==="official"?`已绑定官方快照 ${shortId(payloadOrigin.snapshotId)}`:payloadOrigin.kind==="manual"?"手动 JSON，未绑定官方快照":"尚无可用输入"}</b><span>{payloadOrigin.kind==="official"?`${SCOPE_LABELS[payloadOrigin.scope]}${payloadOrigin.filterDate?` · ${payloadOrigin.filterDate}`:""} · ${payloadOrigin.fixtureCount} 场`:payloadOrigin.kind==="manual"?"手工输入的让球、奖金和销售状态会降为待核实；请刷新官方赛程恢复官方输入。":"请先取得官方赛程。"}</span></div>
          <details className="advanced-json"><summary>高级编辑：查看或手动输入预测 JSON</summary><p>编辑即取消当前输入的官方认证标识并清除旧预测结果。</p><textarea value={payload} onChange={event=>editPayload(event.target.value)} spellCheck={false} disabled={officialBusy||predictionBusy}/></details>
          <div className="workflow-line"><span>1 检索伤停与阵容、转会、教练、盘口</span><i>→</i><span>2 赛事独立模型</span><i>→</i><span>3 基本面为主，Astra 复核</span><i>→</i><span>4 保存赛前候选</span></div>
          <div className="actions"><button onClick={predict} disabled={Boolean(predictionBlockReason)}>{predictionBusy?"正在补证据、建模并分析…":"运行自动预测"}</button><button className="danger" disabled>正式冻结（独立入口）</button><span className={predictionBlockReason?"action-note blocked":"action-note"}>{predictionBusy?"批量流程可能需要数分钟，请勿重复提交":predictionBlockReason||`可提交 ${payloadValidation.ok?payloadValidation.fixtureCount:0} 场`}</span></div>
        </section>
        <p className="foot history-shortcuts"><button className="ghost" onClick={loadSavedDraft} disabled={officialBusy||predictionBusy||savedDraftBusy}>{savedDraftBusy?"读取草稿…":"查看最近已保存草稿"}</button><button onClick={()=>setTab("predictions")}>查看全部历史预测</button><span>只读取保存结果，不取证、不训练、不调用 Astra。</span></p>
        {result&&resultContext&&<section className="panel"><div className="panel-title"><div><h2>{resultContext.kind==="saved"?"已保存草稿（只读）":"候选结果"}</h2><p>来源范围：{resultContext.scopeLabel}{resultContext.filterDate?` · ${resultContext.filterDate}`:""}</p></div><span className="tag">{result.matches?.length||0} 场</span></div>
          {resultContext.kind==="saved"&&<p className="prior-warning">生成时间：{displayBeijingTime(result.completed_at||result.created_at)}。下方方向、开售状态和概率均为当时保存的记录，未按当前赛程重新核验。</p>}
          <div className="result-source"><span>输入场数：{resultContext.fixtureCount}</span><span>来源快照：{resultContext.snapshotId?shortId(resultContext.snapshotId):"未绑定"}</span><span>快照获取：{resultContext.observedAt?displayBeijingTime(resultContext.observedAt):"未记录"}</span><span>运行编号：{shortId(result.run_id)}</span></div>
          <div className="table-wrap result-table"><table><thead><tr><th>编号</th><th>对阵</th><th>胜平负</th><th>官方让球</th><th>让球胜平负</th><th>分析方式</th><th>风险</th><th>状态</th></tr></thead><tbody>{(result.matches||[]).map((m:Json)=><React.Fragment key={m.match_id}><tr><td>{m.match_number}</td><td><b>{m.home_team}</b><br/><span>{m.away_team}</span></td><td>{selectionSummary(m.result,m.result_status,m.analysis_result)}</td><td>{m.official_handicap??"未获取"}</td><td>{selectionSummary(m.handicap_result,m.handicap_status,m.analysis_handicap_result)}</td><td>{analysisMethodText(m)}</td><td>{m.risk||"—"}</td><td>{matchStatusSummary(m)}</td></tr><tr className="evidence-row"><td colSpan={8}><EvidenceDetails match={m}/></td></tr></React.Fragment>)}</tbody></table></div>
          <p className="foot result-audit">规则摘要 {result.prefilter_report?.compact_rule_bytes??"—"} bytes；规则去重后 {result.prefilter_report?.unique_rules??"—"} 条；证据获取 {result.evidence_fetch_performed===true?"已执行":result.evidence_fetch_performed===false?"未执行":"未报告"}；LLM 状态 {displayValue(result.llm_status)}；分析调用 {result.analysis_llm_calls??result.llm_calls??"未报告"} 次；动态检索调用 {result.research_llm_calls??"未报告"} 次；合计 {result.total_llm_calls??"未报告"} 次。分析用量 {llmUsageText(result.llm_usage)}；动态检索用量 {llmUsageText(result.dynamic_research?.usage)}。</p>
        </section>}
      </>}
      {tab==="predictions"&&<HistoryPredictionPanel renderDraftMatchDetails={match=><EvidenceDetails match={match}/>}/>} 
      {tab==="history"&&<section className="panel"><div className="panel-title"><div><h2>权威正式记录明细</h2><p>沿用原有逐场分页视图，不把全部权威历史一次送到浏览器。</p></div><button onClick={loadHistory} disabled={taskBusy}>加载前 20 条</button></div><div className="table-wrap"><table><thead><tr><th>日期</th><th>编号</th><th>对阵</th><th>版本</th><th>状态</th></tr></thead><tbody>{(history.items||[]).map((r:Json,i:number)=><tr key={i}><td>{r.prediction_date}</td><td>{r.match_number}</td><td>{r.home_team} — {r.away_team}</td><td>V{r.prediction_version}</td><td>{r.state||"历史格式"}</td></tr>)}</tbody></table></div><p className="foot">共 {history.total} 条</p></section>}
      {tab==="model"&&<><ReviewPanel/><section className="metrics"><article><label>权威历史</label><b>{quality?.history?.actual_record_count??"—"} 条</b><small>{quality&&statusText(quality.record_count_consistent)}</small></article><article><label>独立比赛</label><b>{quality?.history?.independent_match_count??"—"} 场</b><small>按日期+编号去重</small></article><article><label>当前登记模型</label><b>{models.filter(m=>!m.archived).length}</b><small>历史版本已收进归档</small></article><article><label>回测报告</label><b>{backtests.length}</b><small>重建研究，非实盘</small></article></section>
        <section className="panel"><h2>2025年起赛事历史样本</h2><p>初次建模时采集并保留来源，此后复用版本缓存。表中显示实际取得范围；未完成全赛程对账的来源不标为全量。</p><div className="table-wrap"><table><thead><tr><th>赛事 / 来源</th><th>样本</th><th>实际覆盖</th><th>建库截止</th><th>状态</th></tr></thead><tbody>{jsonList(historicalCoverage.items).map(c=><tr key={c.key}><td><b>{c.competition||({"604":"英锦标赛","76":"亚运男足"} as Record<string,string>)[String(c.scope?.competition_id)]||c.scope?.competition_id||c.key}</b><small>{({football_data:"Football-Data",manifest:"已核验赛程来源",sporttery:"体彩官方"} as Record<string,string>)[c.provider]||c.provider||"官方接口"}</small></td><td>{c.coverage?.match_count??"—"}</td><td>{c.coverage?.first_match||"无"} 至 {c.coverage?.last_match||"无"}</td><td>{c.requested_end}</td><td>{c.coverage?.complete?"全量核验":"实际来源样本"}<small>{c.source_count} 个来源</small></td></tr>)}</tbody></table></div>{jsonList(historicalCoverage.errors).length>0&&<p className="inline-warning">部分历史缓存未通过完整性核验，请查看源文件。</p>}</section>
        <details className="panel"><summary>已有赛季导入档案（{datasets.training_match_count||0} 场）</summary><p>原始导入记录保留；实际自动模型使用上方2025年起赛事缓存。</p><div className="table-wrap"><table><thead><tr><th>赛事</th><th>阶段</th><th>赛季</th><th>训练</th><th>日期范围</th></tr></thead><tbody>{(datasets.datasets||[]).map((d:Json)=><tr key={d.key}><td>{d.family||d.competition}</td><td>{d.phase}</td><td>{d.season}</td><td>{d.training_match_count}</td><td>{d.date_start} 至 {d.date_end}</td></tr>)}</tbody></table></div></details>
        <section className="panel"><div className="panel-title"><div><h2>{showArchivedModels?"模型与历史归档":"当前登记模型"}</h2><p>旧版 {archivedModelCount} 个已收进归档，用于追溯旧预测及回测；新比赛会使用独立赛事模型，并主动刷新人员与战术资料。</p></div><button className="ghost" onClick={toggleModelArchive} disabled={taskBusy}>{showArchivedModels?"收起历史归档":"查看历史归档"}</button></div>{models.length===0?<div className="empty">尚无登记模型，运行预测时将按赛事自动构建。</div>:models.map(m=><div className="model-card" key={m.name}><b>{m.name}</b><span>{m.model_type} · {m.competition}{m.archived?" · 历史归档":""}</span><small>{m.legacy_blocked?"保留原始版本供历史追溯":`截止 ${m.training_cutoff} · ${m.validation?.matches} 场 · 未校准`}</small></div>)}</section>
        <section className="panel"><h2>手动训练</h2><div className="form-grid"><input value={trainForm.csv_path} onChange={e=>setTrainForm({...trainForm,csv_path:e.target.value})}/><input value={trainForm.competition} onChange={e=>setTrainForm({...trainForm,competition:e.target.value})}/><select value={trainForm.model_type} onChange={e=>setTrainForm({...trainForm,model_type:e.target.value})}><option value="poisson">Poisson</option><option value="dixon_coles">Dixon–Coles</option></select><input placeholder="新模型名（不可覆盖）" value={trainForm.model_name} onChange={e=>setTrainForm({...trainForm,model_name:e.target.value})}/><button disabled={taskBusy||!trainForm.model_name} onClick={runTrain}>{taskBusy?"任务运行中…":"开始训练"}</button></div></section>
        <section className="panel"><h2>时间顺序回测</h2><div className="form-grid"><input value={backtestForm.csv_path} onChange={e=>setBacktestForm({...backtestForm,csv_path:e.target.value})}/><input value={backtestForm.competition} onChange={e=>setBacktestForm({...backtestForm,competition:e.target.value})}/><select value={backtestForm.model_type} onChange={e=>setBacktestForm({...backtestForm,model_type:e.target.value})}><option value="poisson">Poisson</option><option value="dixon_coles">Dixon–Coles</option></select><input placeholder="新报告名（不可覆盖）" value={backtestForm.output_name} onChange={e=>setBacktestForm({...backtestForm,output_name:e.target.value})}/><button disabled={taskBusy||!backtestForm.output_name} onClick={runBacktest}>{taskBusy?"任务运行中…":"开始回测"}</button></div><div className="table-wrap"><table><thead><tr><th>报告</th><th>模型</th><th>命中/分母</th><th>覆盖</th><th>Brier</th><th>对数损失</th><th>未来泄漏</th></tr></thead><tbody>{backtests.map(b=><tr key={b.name}><td>{b.name}</td><td>{b.model_type}</td><td>{b.hits}/{b.denominator}</td><td>{(b.coverage*100).toFixed(1)}%</td><td>{b.brier?.toFixed(4)}</td><td>{b.log_loss?.toFixed(4)}</td><td>{b.no_future_leakage?"未检出":"存在"}</td></tr>)}</tbody></table></div></section></>}
      {tab==="settings"&&<section className="panel"><h2>运行边界</h2><dl><dt>监听地址</dt><dd>127.0.0.1</dd><dt>存储</dt><dd>JSON 权威源；SQLite 未实施</dd><dt>官方赛单</dt><dd>进入页面自动获取一次；之后仅由“刷新官方赛程”手动更新，不轮询</dd><dt>预测流程</dt><dd>仅在点击“运行自动预测”后补充相关证据、构建或调用模型，并在可用时由 Astra 做定性分析；批量流程可能需要数分钟</dd><dt>自动建模</dt><dd>逐场报告样本、截止日、先验主导和失败原因；训练成功不代表概率已校准，也不代表任意赛事的可靠性已验证</dd><dt>自动动作</dt><dd>不会自行运行预测、正式冻结或复盘；所有候选仍不可直接冻结</dd><dt>系统状态</dt><dd>页眉“刷新系统状态”只更新健康、数据、模型和回测状态</dd><dt>历史计数</dt><dd>声明 {quality?.history?.declared_record_count??"—"} / 实际 {quality?.history?.actual_record_count??"—"}</dd><dt>大模型</dt><dd>{health?.llm||"未启用"}；以此后端字符串为准，CLI 不可用不会显示为已启用</dd></dl></section>}
    </main>
  </div>;
}

const appRoot = window.__SPORTTERY_ROOT__ ?? createRoot(document.getElementById("root")!);
window.__SPORTTERY_ROOT__ = appRoot;
appRoot.render(<React.StrictMode><App /></React.StrictMode>);

import { FormEvent, Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type { Json } from "./slateState";
import "./HistoryPredictionPanel.css";

type HistoryKind = "all" | "draft" | "formal" | "export";
type SavedKind = Exclude<HistoryKind, "all">;

type HistoryItem = {
  id: string;
  kind: SavedKind;
  label: string;
  created_at?: string | null;
  prediction_date?: string | null;
  match_count: number;
  competitions: string[];
  status?: string | null;
  source_path?: string | null;
};

type HistoryList = {
  items: HistoryItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  counts: { draft_batches: number; formal_batches: number; formal_records: number; export_batches: number; export_records: number };
  warnings?: string[];
};

type HistoryDetail = HistoryItem & {
  matches: unknown[];
  raw: Json;
  read_only: true;
  source_set_index?: number | null;
};

export type HistoryMatchView = {
  key: string;
  matchNumber: string;
  competition: string;
  homeTeam: string;
  awayTeam: string;
  kickoffTime: unknown;
  result: unknown;
  resultStatus: unknown;
  handicap: unknown;
  handicapResult: unknown;
  handicapStatus: unknown;
  confidence: unknown;
  risk: unknown;
  reason: unknown;
  counterEvidence: unknown[];
  probabilities: Json | null;
  handicapAssessment: Json | null;
  dynamicEvidence: Json | null;
  legacyScore: unknown;
  legacyTotalGoals: unknown;
  state: unknown;
  raw: unknown;
  mappingAvailable: boolean;
};

const emptyList: HistoryList = {
  items: [], total: 0, page: 1, page_size: 20, total_pages: 0,
  counts: { draft_batches: 0, formal_batches: 0, formal_records: 0, export_batches: 0, export_records: 0 }, warnings: [],
};

const asObject = (value: unknown): Json => value && typeof value === "object" && !Array.isArray(value) ? value as Json : {};
const asList = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const shown = (value: unknown) => value === null || value === undefined || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
const requestError = (error: unknown) => error instanceof Error ? error.message : String(error);
const isAbort = (error: unknown) => error instanceof DOMException && error.name === "AbortError";
const kindName = (kind: SavedKind) => ({ draft: "候选草稿", formal: "权威正式版本", export: "赛前原始存档" })[kind];

const beijingTime = (value: unknown) => {
  if (typeof value !== "string" || !value) return "未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(parsed).replaceAll("/", "-");
};

async function getJson(path: string, signal: AbortSignal): Promise<Json> {
  const response = await fetch(path, { signal });
  const text = await response.text();
  let data: Json = {};
  if (text) {
    try { data = JSON.parse(text); }
    catch { throw new Error(`历史服务返回了无法识别的内容（HTTP ${response.status}）`); }
  }
  if (!response.ok) throw new Error(data.detail || `历史服务请求失败（HTTP ${response.status}）`);
  return data;
}

export function normalizeHistoryMatch(input: unknown, index: number): HistoryMatchView {
  const match = asObject(input);
  const prediction = asObject(match.prediction);
  const playStatus = asObject(prediction.play_status);
  const resultPlay = asObject(playStatus.result);
  const handicapPlay = asObject(playStatus.handicap_result);
  const officialData = asObject(match.official_data);
  const officialHandicap = asObject(officialData.handicap);
  const legacyOfficial = asObject(match.official);
  const legacyHandicap = asObject(legacyOfficial.handicap);
  const preMatchFacts = asObject(match.pre_match_facts);
  const evidence = asObject(match.evidence);
  const liveResearch = asObject(match.live_research);
  const threeWay = asObject(prediction.three_way_probabilities);
  const handicapAssessment = asObject(prediction.handicap_assessment);
  const counter = prediction.counter_evidence_effect ?? prediction.favorite_no_win_path ?? match.major_counterevidence;

  return {
    key: String(match.match_id || `${match.prediction_date || "unknown"}-${match.match_number || index}-${match.prediction_version || ""}`),
    matchNumber: shown(match.match_number), competition: shown(match.competition),
    homeTeam: shown(match.home_team), awayTeam: shown(match.away_team), kickoffTime: match.kickoff_time,
    result: resultPlay.selection ?? prediction.predicted_result ?? prediction.result ?? match.result,
    resultStatus: resultPlay.status ?? match.result_status ?? match.status,
    handicap: officialHandicap.official_handicap ?? legacyHandicap.line ?? match.official_handicap,
    handicapResult: handicapPlay.selection ?? prediction.predicted_handicap_result ?? prediction.handicap_result ?? match.handicap_result,
    handicapStatus: handicapPlay.status ?? match.handicap_status ?? match.status,
    confidence: resultPlay.confidence ?? prediction.direction_confidence ?? prediction.confidence ?? match.confidence,
    risk: prediction.risk_level ?? preMatchFacts.risk_level ?? match.risk,
    reason: prediction.prediction_reason ?? prediction.analysis_result ?? match.summary ?? match.analysis_summary ?? match.reason,
    counterEvidence: asList(counter).length ? asList(counter) : counter ? [counter] : [],
    probabilities: Object.keys(threeWay).length ? threeWay : null,
    handicapAssessment: Object.keys(handicapAssessment).length ? handicapAssessment : null,
    dynamicEvidence: Object.keys(liveResearch).length ? liveResearch : Object.keys(evidence).length ? evidence : Object.keys(preMatchFacts).length ? preMatchFacts : null,
    legacyScore: prediction.predicted_score ?? prediction.score,
    legacyTotalGoals: prediction.predicted_total_goals ?? prediction.total_goals,
    state: match.state ?? match.match_status ?? match.status,
    raw: input,
    mappingAvailable: Object.keys(match).length > 0,
  };
}

const ProbabilityLine = ({ probabilities }: { probabilities: Json | null }) => probabilities
  ? <p>保存概率：主胜 {shown(probabilities.home)} · 平 {shown(probabilities.draw)} · 客胜 {shown(probabilities.away)}{probabilities.method ? `；方法：${shown(probabilities.method)}` : ""}{probabilities.reason_unavailable ? `；未量化原因：${shown(probabilities.reason_unavailable)}` : ""}</p>
  : <p>模型或主观概率：原记录未保存或不可用。</p>;

function FormalMatchDetails({ view }: { view: HistoryMatchView }) {
  const match = asObject(view.raw);
  if (!view.mappingAvailable) return <div className="history-match-details"><p className="history-unmapped">这条旧记录不是可映射的对象结构，无法生成中文字段摘要。原始值仍完整保留在下方 JSON 中。</p></div>;
  const finalResult = asObject(match.final_result);
  const review = asObject(match.review);
  const prediction = asObject(match.prediction);
  const analysisDimensions = asObject(prediction.analysis_dimensions);
  return <div className="history-match-details">
    <div className="history-detail-grid">
      <section><h4>原预测理由</h4><p>{shown(view.reason)}</p></section>
      <section><h4>反证与风险处理</h4>{view.counterEvidence.length ? <ul>{view.counterEvidence.map((item, itemIndex) => <li key={itemIndex}>{shown(item)}</li>)}</ul> : <p>原记录未单独保存反证。</p>}<p>风险：{shown(view.risk)}；置信：{shown(view.confidence)}</p></section>
      <section><h4>概率与让球判断</h4><ProbabilityLine probabilities={view.probabilities}/>{view.handicapAssessment ? <p>让球区间：{shown(view.handicapAssessment.most_likely_region)}；判断：{shown(view.handicapAssessment.note || view.handicapAssessment.reason_unavailable)}</p> : <p>原记录未保存结构化让球概率。</p>}</section>
      <section><h4>保存状态</h4><p>记录状态：{shown(view.state)}；赛果状态：{shown(finalResult.match_completion_status)}</p><p>版本：V{shown(match.prediction_version)}；冻结时间：{beijingTime(match.finalized_at || prediction.finalized_at)}</p></section>
    </div>
    {Object.keys(analysisDimensions).length > 0 && <details><summary>查看原分析维度</summary><dl className="history-dimensions">{Object.entries(analysisDimensions).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{shown(value)}</dd></div>)}</dl></details>}
    {view.dynamicEvidence && <details><summary>查看当时保存的动态证据</summary><pre>{JSON.stringify(view.dynamicEvidence, null, 2)}</pre></details>}
    {(view.legacyScore !== null && view.legacyScore !== undefined || view.legacyTotalGoals !== null && view.legacyTotalGoals !== undefined) && <p className="history-legacy">旧版原四项保留：比分 {shown(view.legacyScore)}；总进球 {shown(view.legacyTotalGoals)}。</p>}
    {(review.review_summary || asList(review.next_round_adjustment).length > 0) && <details><summary>查看已保存复盘字段</summary><p>{shown(review.review_summary)}</p>{asList(review.next_round_adjustment).length > 0 && <ul>{asList(review.next_round_adjustment).map((item, i) => <li key={i}>{shown(item)}</li>)}</ul>}</details>}
  </div>;
}

function HistoryMatchTable({ detail, renderDraftMatchDetails }: { detail: HistoryDetail; renderDraftMatchDetails?: (match: Json) => ReactNode }) {
  const views = detail.matches.map(normalizeHistoryMatch);
  return <div className="table-wrap history-match-table"><table><thead><tr><th>编号 / 对阵</th><th>赛事 / 开赛</th><th>胜平负</th><th>官方让球</th><th>让球胜平负</th><th>风险 / 状态</th></tr></thead><tbody>{views.map((view, index) => <Fragment key={`${view.key}-${index}`}>
    <tr className="history-match-summary-row"><td data-label="编号 / 对阵"><b>{view.matchNumber}</b><small>{view.homeTeam} — {view.awayTeam}</small></td><td data-label="赛事 / 开赛">{view.competition}<small>{beijingTime(view.kickoffTime)}</small></td><td data-label="胜平负"><b>{shown(view.result)}</b><small>{shown(view.resultStatus)}</small></td><td data-label="官方让球">{shown(view.handicap)}</td><td data-label="让球胜平负"><b>{shown(view.handicapResult)}</b><small>{shown(view.handicapStatus)}</small></td><td data-label="风险 / 状态">{shown(view.risk)}<small>{shown(view.state)}</small></td></tr>
    <tr className="history-match-detail-row"><td colSpan={6}>{renderDraftMatchDetails && view.mappingAvailable && (detail.kind === "draft" || detail.kind === "export" && Boolean(asObject(view.raw).live_research || asObject(view.raw).auto_model || asObject(view.raw).analysis_status)) ? renderDraftMatchDetails(asObject(detail.matches[index])) : <FormalMatchDetails view={view}/>}<details className="history-raw-match"><summary>查看本场原始 JSON</summary><pre>{JSON.stringify(detail.matches[index], null, 2) ?? shown(detail.matches[index])}</pre></details></td></tr>
  </Fragment>)}</tbody></table></div>;
}

export default function HistoryPredictionPanel({ renderDraftMatchDetails }: { renderDraftMatchDetails?: (match: Json) => ReactNode }) {
  const [kind, setKind] = useState<HistoryKind>("all");
  const [query, setQuery] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [competition, setCompetition] = useState("");
  const [page, setPage] = useState(1);
  const [list, setList] = useState<HistoryList>(emptyList);
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [listBusy, setListBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [reload, setReload] = useState(0);
  const applied = useRef({ query: "", dateFrom: "", dateTo: "", competition: "" });
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const detailController = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLElement | null>(null);
  const detailRef = useRef<HTMLElement | null>(null);

  const competitionChoices = useMemo(() => [...new Set(list.items.flatMap(item => item.competitions || []))].sort((a, b) => a.localeCompare(b, "zh-CN")), [list.items]);

  useEffect(() => {
    const sequence = ++listSequence.current;
    const controller = new AbortController();
    const filters = applied.current;
    const params = new URLSearchParams({ kind, page: String(page), page_size: "20" });
    if (filters.query) params.set("q", filters.query);
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
    if (filters.competition) params.set("competition", filters.competition);
    setListBusy(true); setListError("");
    getJson(`/api/prediction-history?${params}`, controller.signal).then(data => {
      if (sequence !== listSequence.current) return;
      setList({ ...emptyList, ...data, counts: { ...emptyList.counts, ...asObject(data.counts) } } as HistoryList);
    }).catch(error => { if (sequence === listSequence.current && !isAbort(error)) setListError(requestError(error)); })
      .finally(() => { if (sequence === listSequence.current) setListBusy(false); });
    return () => controller.abort();
  }, [kind, page, reload]);

  useEffect(() => () => detailController.current?.abort(), []);

  useEffect(() => {
    if (!selectedKey || !detailRef.current || !(detailBusy || detailError || detail)) return;
    const frame = requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, [selectedKey, detailBusy, detailError, detail]);

  const applyFilters = (event: FormEvent) => {
    event.preventDefault();
    applied.current = { query: query.trim(), dateFrom, dateTo, competition: competition.trim() };
    setPage(1); setReload(value => value + 1);
  };

  const resetFilters = () => {
    setQuery(""); setDateFrom(""); setDateTo(""); setCompetition(""); setKind("all");
    applied.current = { query: "", dateFrom: "", dateTo: "", competition: "" };
    setPage(1); setReload(value => value + 1);
  };

  const openDetail = (item: HistoryItem) => {
    const key = `${item.kind}:${item.id}`;
    const sequence = ++detailSequence.current;
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setSelectedKey(key); setDetail(null); setDetailError(""); setDetailBusy(true);
    getJson(`/api/prediction-history/${item.kind}/${encodeURIComponent(item.id)}`, controller.signal).then(data => {
      if (sequence === detailSequence.current) setDetail(data as HistoryDetail);
    }).catch(error => { if (sequence === detailSequence.current && !isAbort(error)) setDetailError(requestError(error)); })
      .finally(() => { if (sequence === detailSequence.current) { setDetailBusy(false); detailController.current = null; } });
  };

  const backToList = () => {
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return <>
    <section className="panel history-browser" aria-label="历史预测浏览器" ref={listRef}>
      <div className="panel-title"><div><h2>历史预测</h2><p>候选草稿、权威正式版本和赛前原始存档统一浏览；三类独立计数，不混算。</p></div><button className="ghost" onClick={() => setReload(value => value + 1)} disabled={listBusy}>{listBusy ? "读取中…" : "刷新列表"}</button></div>
      <div className="history-counts"><span>草稿批次 <b>{list.counts.draft_batches}</b></span><span>权威正式批次 <b>{list.counts.formal_batches}</b></span><span>权威正式记录 <b>{list.counts.formal_records}</b></span><span>原始存档批次 <b>{list.counts.export_batches}</b></span><span>原始存档记录 <b>{list.counts.export_records}</b></span></div>
      <form className="history-filters" onSubmit={applyFilters}>
        <label>类型<select value={kind} onChange={event => { setKind(event.target.value as HistoryKind); setPage(1); }}><option value="all">全部</option><option value="draft">候选草稿</option><option value="formal">权威正式版本</option><option value="export">赛前原始存档</option></select></label>
        <label>队伍 / 编号 / 赛事<input value={query} onChange={event => setQuery(event.target.value)} placeholder="输入关键词"/></label>
        <label>开始日期<input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)}/></label>
        <label>结束日期<input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)}/></label>
        <label>赛事<input value={competition} list="history-competitions" onChange={event => setCompetition(event.target.value)} placeholder="精确赛事名"/><datalist id="history-competitions">{competitionChoices.map(value => <option value={value} key={value}/>)}</datalist></label>
        <div className="history-filter-actions"><button type="submit" disabled={listBusy}>筛选</button><button type="button" className="ghost" onClick={resetFilters} disabled={listBusy}>清除</button></div>
      </form>
      {listError && <div className="alert" role="alert">历史列表读取失败：{listError}</div>}
      {asList(list.warnings).length > 0 && <ul className="warnings">{asList(list.warnings).map((warning, index) => <li key={index}>{shown(warning)}</li>)}</ul>}
      {listBusy && list.items.length === 0 ? <div className="empty" role="status">正在读取历史预测…</div> : list.items.length === 0 ? <div className="empty">当前筛选没有保存的历史预测。</div> : <div className="history-batch-list">{list.items.map(item => {
        const key = `${item.kind}:${item.id}`;
        return <article className={selectedKey === key ? "selected" : ""} key={key}>
          <div><span className={`history-kind ${item.kind}`}>{kindName(item.kind)}</span><h3>{item.label}</h3><p>预测日期：{item.prediction_date || "未记录"} · 保存时间：{beijingTime(item.created_at)}</p><p>赛事：{item.competitions?.join("、") || "未记录"} · 状态：{item.status || "未记录"}</p>{item.source_path && <p>来源：{item.source_path}</p>}</div>
          <div className="history-batch-action"><b>{item.match_count} 场</b><button onClick={() => openDetail(item)} disabled={detailBusy && selectedKey === key}>{detailBusy && selectedKey === key ? "打开中…" : "查看完整批次"}</button></div>
        </article>;
      })}</div>}
      <div className="history-pagination"><button className="ghost" disabled={listBusy || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span>第 {list.page || page} / {Math.max(list.total_pages || 0, 1)} 页 · 共 {list.total} 个批次</span><button className="ghost" disabled={listBusy || page >= list.total_pages} onClick={() => setPage(value => value + 1)}>下一页</button></div>
    </section>
    {(detailBusy || detailError || detail) && <section className="panel history-detail" aria-live="polite" ref={detailRef}>
      {detailBusy && <div className="empty" role="status">正在读取所选批次的完整保存内容…</div>}
      {detailError && <div className="alert" role="alert">批次详情读取失败：{detailError}</div>}
      {detail && !detailBusy && <>
        <div className="panel-title"><div><h2>{detail.label}</h2><p>{kindName(detail.kind)} · 保存 {beijingTime(detail.created_at)} · 只读{detail.source_path ? ` · ${detail.source_path}` : ""}</p>{detail.kind === "export" && Number.isInteger(detail.source_set_index) && <p>当前为源文件内第 {Number(detail.source_set_index) + 1} 个预测集合（source_set_index={detail.source_set_index}）。下方批次 JSON 保留完整源文件。</p>}</div><div className="history-detail-actions"><span className="tag">{detail.match_count} 场</span><button className="ghost" onClick={backToList}>返回批次列表</button></div></div>
        <p className="prior-warning">这是当时保存的原始记录。页面不按当前赛程、伤停或模型状态重新计算，也不会写回任何数据。</p>
        <HistoryMatchTable detail={detail} renderDraftMatchDetails={renderDraftMatchDetails}/>
        <details className="history-raw-batch"><summary>查看本批次原始 JSON（遗留字段完整保留）</summary><pre>{JSON.stringify(detail.raw, null, 2)}</pre></details>
      </>}
    </section>}
  </>;
}

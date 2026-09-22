import { useEffect, useState } from "react";
import "./ReviewPanel.css";

type Data = Record<string, any>;
const ROOT = "/api/manual-reviews";
async function request(path: string, body?: Data): Promise<Data> {
  const res = await fetch(ROOT + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}
const date = (value: string) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "时间未核实";
const labels: Record<string, string> = { result: "胜平负", handicap_result: "让球胜平负", combined: "两项合计" };
const pick = (value: unknown) => typeof value === "string" && value ? value : "未形成选项";

export default function ReviewPanel() {
  const [batches, setBatches] = useState<Data[]>([]);
  const [selected, setSelected] = useState("");
  const [reports, setReports] = useState<Data[]>([]);
  const [report, setReport] = useState<Data | null>(null);
  const [astra, setAstra] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adopted, setAdopted] = useState<string[]>([]);
  async function refresh() {
    try {
      const data = await request("/batches");
      setBatches(data.items);
      setSelected(previous => data.items.some((b: Data) => b.batch_id === previous) ? previous : data.items[0]?.batch_id || "");
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    let cancelled = false;
    setReport(null); setReports([]); setError(""); setNotice("");
    if (selected) request(`/reports?batch_id=${encodeURIComponent(selected)}`).then(data => {
      if (!cancelled) setReports(data.items);
    }).catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [selected]);
  async function run() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await request("/run", { batch_id: selected, use_astra: astra });
      setReport(result);
      const list = await request(`/reports?batch_id=${encodeURIComponent(selected)}`);
      setReports(list.items);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function load(id: string) {
    if (!id) return;
    setBusy(true); setError("");
    try { setReport(await request(`/reports/${id}`)); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function adopt(row: Data, candidate: Data) {
    if (!report) return;
    setBusy(true); setError("");
    try {
      await request(`/reports/${report.report_id}/lessons`, { match_id: row.match_id, candidate_id: candidate.candidate_id });
      setAdopted(ids => [...ids, candidate.candidate_id]);
      setNotice("经验已保存为对应赛事的候选提醒。只用于后续条件匹配与定性检查，单场结果不修改概率。");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  const current = batches.find(b => b.batch_id === selected);
  return <section className="manual-review" aria-label="手动赛后复盘">
    <div className="review-heading"><div><h2>手动赛后复盘</h2><p>选择保存版本，核对真实赛果，再决定是否保留经验。</p></div><button disabled={busy} onClick={() => void refresh()}>刷新批次列表</button></div>
    <div className="review-controls">
      <label>预测批次 / 历史版本<select aria-label="复盘预测批次" value={selected} disabled={busy} onChange={e => setSelected(e.target.value)}><option value="">请选择保存批次</option>{batches.map(b => <option key={b.batch_id} value={b.batch_id}>{b.label} · {b.match_count} 场</option>)}</select></label>
      <label className="review-check"><input type="checkbox" checked={astra} disabled={busy} onChange={e => setAstra(e.target.checked)}/>调用 Astra 分析已核验材料</label>
      <button className="primary" disabled={busy || !selected} onClick={() => void run()}>{busy ? "正在处理…" : "抓取赛果并复盘"}</button>
    </div>
    <p className="review-note">{current?.kind === "research" ? "研究草稿只做研究对照，分析方向单独标注；赛后生成的草稿不计赛前成绩。" : "正式成绩只纳入可确认在开赛前冻结的选项；历史独立版本不与最高合法版本混算。"} 页面不定时抓取赛果，只有点击复盘按钮才联网。</p>
    {busy && <p role="status">正在核验或保存。Astra 定性分析可能需要数分钟；结束后显示实际完成状态和独立概率评分。</p>}
    {error && <p className="review-error" role="alert">{error}</p>}
    {notice && <p className="review-notice" role="status">{notice}</p>}
    {reports.length > 0 && <label className="review-saved">已保存复盘（读取不联网）<select aria-label="已保存复盘版本" value={report?.report_id || ""} disabled={busy} onChange={e => void load(e.target.value)}><option value="">选择报告</option>{reports.map(r => <option key={r.report_id} value={r.report_id}>{date(r.created_at)} · {r.status === "completed" ? "赛果齐全" : "部分待确认"} · {r.report_id.slice(0, 8)}</option>)}</select></label>}
    {report && <>
      <p><strong>{report.statistics_label}</strong> · {date(report.created_at)} · {report.status === "completed" ? "赛果齐全" : "部分赛果待确认"}</p>
      <div className="review-stats">{["result", "handicap_result", "combined"].map(p => { const s = report.statistics[p]; return <article key={p}><span>{labels[p]}</span><strong>{s.hits} / {s.denominator}</strong><small>{s.rate === null ? "无有效分母" : `${(s.rate * 100).toFixed(1)}%`}{p !== "combined" && ` · 排除 ${s.excluded} 项`}</small>{p !== "combined" && <small>Brier {s.brier_mean == null ? "不可计算" : s.brier_mean.toFixed(4)} · 有效概率 {s.brier_samples} 场（覆盖 {(s.brier_coverage * 100).toFixed(0)}%）</small>}</article>; })}</div>
      <p className="review-note">Astra：{report.astra_analysis.status === "completed" ? "本次定性复盘已完成；因果解释仍需事实验证" : `未完成（${report.astra_analysis.reason || report.astra_analysis.status}），以下本地报告完整保留`}。{report.probability_layer}</p>
      {report.fetch_errors?.length > 0 && <p className="review-error">{report.fetch_errors.map((e: Data) => `${e.date} ${e.reason}`).join("；")}</p>}
      <div className="review-table-wrap"><table><thead><tr><th>编号 / 对阵</th><th>原预测</th><th>官方 90 分钟</th><th>胜平负对照</th><th>让球对照</th></tr></thead><tbody>{report.matches.map((row: Data) => <tr key={row.match_id}><td>{row.match_number}<br/>{row.home_team} — {row.away_team}<small>{row.competition} · {row.pregame_eligible ? "赛前保存" : "不计赛前成绩"}</small></td><td>{pick(row.comparison.result.predicted)} / {pick(row.comparison.handicap_result.predicted)}<small>官方让球 {row.official_handicap ?? "未获取"}</small></td><td>{row.result.score || "待确认"}<small>{row.result.official_settlement_verified ? "官方已结算" : row.result.reason}</small></td>{["result", "handicap_result"].map(p => <td key={p}>{row.comparison[p].actual || "—"} · {row.comparison[p].hit === true ? "命中" : row.comparison[p].hit === false ? "未中" : "排除"}<small>{row.comparison[p].analysis_only ? "研究分析方向；" : ""}{row.comparison[p].exclusion_reason}</small></td>)}</tr>)}</tbody></table></div>
      {report.matches.map((row: Data) => { const analysis = report.astra_analysis.matches?.find((r: Data) => r.match_id === row.match_id); return <details key={row.match_id} className="review-detail"><summary>{row.match_number} {row.home_team} — {row.away_team}：偏差原因与经验</summary>
        <p><strong>原始判断：</strong>{row.original_reasoning}</p>
        <p><strong>已确认偏差：</strong>{row.confirmed_deviations.length ? row.confirmed_deviations.map((d: Data) => d.detail).join("；") : "没有可确认的选项偏差，或赛果尚未确认。"}</p>
        <p><strong>赛前反证：</strong>{row.counterevidence.join("；") || "没有单独保存反证，不能赛后补称已识别。"}</p>
        <p><strong>原因边界：</strong>{row.causal_hypotheses.join("；")}</p>
        {row.process?.facts?.length > 0 && <div className="review-astra"><strong>已核验过程事实</strong><ul>{row.process.facts.map((f: Data) => <li key={f.fact_id}>{f.summary} <a href={f.source_url} target="_blank" rel="noreferrer">来源</a></li>)}</ul></div>}
        {analysis && <div className="review-astra"><p><strong>Astra 理由与事实差距：</strong>{analysis.reasoning_gap}</p><p><strong>待验证假设：</strong>{analysis.hypothesis}</p><p><strong>下轮检查：</strong>{analysis.next_check}</p></div>}
        <p><strong>未获取过程字段：</strong>{row.missing_process_fields.join("、")}</p>
        <p>{row.next_check}</p>
        {row.result.source && <p><a href={row.result.source.url} target="_blank" rel="noreferrer">官方原始赛果来源</a> · 核验于 {date(row.result.source.fetched_at)}</p>}
        {row.lesson_candidates.map((c: Data) => <article className="review-candidate" key={c.candidate_id}><p>{c.text}</p><p>触发：{c.trigger_conditions}<br/>不适用：{c.excluded_conditions}</p><small>适用：{Object.entries(c.scope).map(([k, v]) => `${k}=${v}`).join("；")} · 有效证据 1 场 · 尚不能评估增益</small><button disabled={busy || adopted.includes(c.candidate_id)} onClick={() => void adopt(row, c)}>{adopted.includes(c.candidate_id) ? "已加入候选经验" : "将此经验加入对应赛事模型"}</button></article>)}
      </details>; })}
      <details className="review-detail"><summary>报告校验与统计限制</summary><p>源记录 SHA-256：{report.source_sha256}</p><p>复盘 SHA-256：{report.report_sha256}</p><ul>{report.limitations.map((s: string) => <li key={s}>{s}</li>)}</ul></details>
    </>}
  </section>;
}

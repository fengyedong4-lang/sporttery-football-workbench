import type { Json } from "./slateState";

const labels: Record<string,string> = {injuries:"伤病与停赛",transfers:"球员转会",coach:"教练与战术",formation:"常用阵型",market:"欧赔与亚洲盘"};
const text = (value: unknown) => value == null ? "未记录" : typeof value === "object" ? JSON.stringify(value) : String(value);
const statusText = (value: unknown) => ({completed:"已完成检索",partial:"部分资料缺失",unavailable:"暂不可用",single_source_unconfirmed:"单一来源，尚未交叉确认",conflicting:"来源存在冲突",confirmed:"已确认"}[String(value)] || text(value));
const list = (value: unknown): Json[] => Array.isArray(value) ? value.filter(v=>v && typeof v === "object") : [];
const link = (value: unknown) => { try { const url = new URL(String(value)); return url.protocol === "https:" && !url.username && !url.password ? url.href : null; } catch { return null; } };

export default function ResearchDetails({match}:{match:Json}) {
  const research = match.live_research;
  if (!research) return null;
  const market = match.market_context || {};
  const budget = market.reference_budget || {};
  const lessons = list(match.lesson_assessments);
  const observations = list(research.market?.observations);
  return <section className="research-layer">
    <div className="layer-heading"><div><h3>本次主动检索</h3><p>伤停、转会、教练、阵型和盘口在每次预测时重新查询；未查到报道不等于不存在。</p></div><span className="layer-label">{statusText(research.status)}</span></div>
    <p className="model-note">检索开始：{text(research.started_at || research.fetched_at)} · 请求：{text(research.request_id)}</p>
    <div className="research-dimensions">{Object.entries(labels).map(([key,label])=>{
      const dimension = research.dimensions?.[key] || {};
      const related = list(research.facts).filter(f=>f.dimension===key);
      return <article key={key}><h4>{label}</h4><p>{dimension.attempted?"已主动检索":"未完成检索"} · {related.length} 条文字资料{key==="market"&&` · ${observations.length} 条数值观察`}</p>
        {related.length>0&&<ul>{related.map((f,index)=><li key={f.fact_id||index}>{text(f.summary)}{f.confirmation_status&&<small>状态：{statusText(f.confirmation_status)}</small>}</li>)}</ul>}
        {dimension.missing&&<p className="model-note">{dimension.missing===true?"已查询，尚未取得可核实资料":text(dimension.missing)}</p>}
      </article>;
    })}</div>
    <div className="market-review"><h4>基本面与市场参考</h4>
      <p>本次参考预算：基本面 {Math.round((budget.fundamentals??1)*100)}% · 市场 {Math.round((budget.market??0)*100)}%。这是证据参考上限，不是概率混合或收益承诺。</p>
      <p>{text(market.reason)}</p>
      <p>已核验可比盘变 {list(market.changes).length} 组；单点快照 {list(market.snapshots).length} 组。单点不推断升降盘。</p>
      <p>爆冷配对样本 {market.upset_profile?.paired_matches??0} 场；达到增权条件：{market.upset_profile?.frequent?"是":"否"}。</p>
      {observations.length>0&&<details><summary>查看盘口数值与时间（{observations.length}）</summary><ul>{observations.map((observation,index)=><li key={observation.observation_id||index}>{text(observation.bookmaker)} · {observation.market_type==="asian_handicap"?`亚洲让球，主队盘线 ${text(observation.line)}`:"欧赔胜平负"} · 主 {text(observation.home)}{observation.draw!=null&&` / 平 ${text(observation.draw)}`} / 客 {text(observation.away)}<small>观察：{text(observation.observed_at)} · {observation.observation_basis==="retrieval_snapshot"?"本次抓取快照":"来源记录时间"} · 来源 {text(observation.source_id)}</small></li>)}</ul></details>}
      {list(market.changes).map((change,index)=><p key={index}>{text(change.bookmaker)} · {text(change.market_type)} · {text(change.from)} → {text(change.to)} · {change.change_type==="line_change"?`主队盘线 ${change.from_line} → ${change.to_line}`:`同口径变化 ${text(change.implied_probability_change)}`}</p>)}
    </div>
    {lessons.length>0&&<div><h4>赛事模型经验的本次处理</h4><ul>{lessons.map((lesson,index)=><li key={lesson.lesson_id||index}>{text(lesson.lesson_id)} · {lesson.used?"已采用":"未采用"}：{text(lesson.reason)}</li>)}</ul></div>}
    <details><summary>查看本次检索来源（{list(research.sources).length}）</summary><ul>{list(research.sources).map((source,index)=>{ const url=link(source.url); return <li key={source.source_id||index}>{url?<a href={url} target="_blank" rel="noreferrer">{text(source.title||source.url)}</a>:text(source.title)}<small>发布：{text(source.published_at||source.source_updated_at)} · 获取：{text(source.fetched_at)}</small></li>;})}</ul></details>
  </section>;
}

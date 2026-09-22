import copy, hashlib, json
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from validate_prediction import statistics_eligibility
ROOT=Path(__file__).resolve().parents[1]
stamp=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
out=ROOT/'exports/2026-09-17'/('review_001_'+stamp[11:19].replace(':',''))
out.mkdir()
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
hp=ROOT/'data/prediction_history.json';h=read(hp);original=copy.deepcopy(h)
(out/'prediction_history_before.json').write_bytes(hp.read_bytes())
protected={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'exports/2026-09-17').glob('*') if p.is_file()}
urls={
'report':'https://news.zhibo8.com/zuqiu/2026-09-17/match2113579date2026vnative.htm',
'statistics':'https://news.zhibo8.com/zuqiu/2026-09-17/6aab8cb123b28native.htm',
'lineups':'https://news.zhibo8.com/zuqiu/2026-09-17/6aab3d2f18701native.htm',
'previous':'https://www.sport.org.cn/shouye/jjty/2026/0915/707989.html'}
findings=[
'已确认输出偏差：中国获胜的分析方向错误；独立比分0:2未中；正式+3让胜命中。不得用让球命中掩盖方向和零封判断错误。',
'已确认流程遗漏：首发报道11:53:49已发布，12:51:24补比分时仍未重新核验；王霜替补、门将改为潘红艳。V2在11:16冻结早于首发，不能用后来信息指责其当时未读已知首发。',
'赛前记录已有中国首轮被反击破门和阵容不确定，却只降低大胜把握，补充比分仍选零封两球胜；中国不胜与对手破门路径没有得到充分复核。不能事后声称本来预测了平局。',
'战报支持中国创造较多射门、乌兹通过战术角球和二次进攻得分；没有xG和完整录像，不能把24射10正解释为本应获胜，也不能确定每次射门质量或把所有责任归门将。',
'下半场换上王霜、陈巧珠、李晴潼，70分钟扳平、72分钟中框。替补参与扳平可支持阵容影响的解释，但不足以证明王霜首发就必胜。']
next_steps=['相似女足杯赛场景，临场追加预测先核对已公布首发，尤其门将、组织核心与中卫；未知保留未知。',
'把能否获胜、能否零封和净胜区间分开；对连续失球及大量人员变化，不只下调大胜，还需重新比较不胜路径。',
'本场只记录个案和已确认执行遗漏，不因单场平局修改全局参数，不改今晚其他联赛预测。']
missing=['完整黄牌及犯规','红牌全量核验','点球与VAR全量核验','xG','控球率','角球数量','禁区触球','完整伤病事件','完整扑救质量及录像','官方体彩结算']
for r in h['records']:
 if r.get('prediction_date')!='2026-09-17' or r.get('match_number')!='周四001': continue
 f=r.setdefault('final_result',{})
 f.update(match_completion_status='已完赛',score_90='1:1',final_score='1:1',verified_90_minutes=True,actual_result='平',actual_handicap_result='让胜',actual_total_goals=2,result_source=urls['report'],source_checked_at=stamp,
   first_goal={'team':'乌兹别克斯坦女足','minute':'41/42','note':'战报列41分钟、单球报道列42分钟；保留分钟口径差异'},
   goal_timeline=[{'minute':'41/42','team':'乌兹别克斯坦女足','player':'达达博耶娃','score_home_away':'1:0','type':'战术角球后射门被扑，头球补射'},{'minute':70,'team':'中国女足','player':'张琳艳','score_home_away':'1:1','assist':'王妍雯','build_up':'王霜传中，王妍雯头球摆渡'}],
   shots={'home':5,'away':24},shots_on_target={'home':3,'away':10},
   substitutions=[{'team':'中国女足','minute':46,'in':['王霜','陈巧珠','李晴潼'],'out':['周心雨','王莹','乌日古木拉']},{'team':'中国女足','minute':64,'in':['袁丛'],'out':['刘靖']},{'team':'中国女足','minute':80,'in':['吴海燕'],'out':['司雨']}],
   starting_lineup_changes='王霜替补，门将由首轮肖梓彤更换为潘红艳；相比首轮多处调整。',
   goalkeeper_performance='乌兹进球前潘红艳扑出首射，对方补射得分；是否属于可避免的门将失误不能单凭文字战报定性。',
   abnormal_finishing_efficiency='24射10正仅进1球属描述事实，缺xG及机会质量分布，不能量化异常程度。',unavailable_fields=missing,
   fact_confirmation={'status':'已确认','source_url':urls['report'],'source_type':'可靠体育媒体全场战报','source_checked_at':stamp,'confirmed_at':stamp,'note':'小组赛常规时间结束1:1，与用户报告一致；网页顶部曾呈初始化0:0，以正文明确全场结果为据。'},
   official_settlement={'status':'待核实','source_url':None,'source_checked_at':None,'confirmed_at':None,'note':'未核实体彩官方结算；让球命中为90分钟事实口径计算。'})
 r.setdefault('hits',{}).update(hit_result=None,hit_handicap=True,hit_score=None,hit_total_goals=None)
 r.setdefault('review',{}).update(prediction_error_type=['预测时已经识别风险，但没有真正反映到最终结果'],review_summary='正式让球命中，分析客胜与独立比分失败。'+ ' '.join(findings),next_round_adjustment=next_steps,
  review_action={'action':'暂不调整','target_rule_ids':[],'candidate_rule':None,'evidence':list(urls.values()),'reason':'单场案例不足以新增通用规则；落实既有首发核验与独立零封判断要求。'},
  risk_identified_but_not_reflected=True,quality_note='本场未开售普通胜平负，分析方向失败单列；独立比分不混入两玩法分母。')
for a,b in zip(original['records'],h['records']):
 assert {k:v for k,v in a.items() if k not in ['final_result','hits','review']}=={k:v for k,v in b.items() if k not in ['final_result','hits','review']}
 if a.get('prediction_date')!='2026-09-17' or a.get('match_number')!='周四001':assert a==b
review={'scope':'2026-09-17周四001单场复盘','checked_at':stamp,'sources':urls,'findings':findings,'next_steps':next_steps,'missing':missing,'formal_effective_version':2,'formal_handicap':{'h':3,'selection':'让胜','actual':'让胜','hit':True},'analysis_result':{'selection':'负','actual':'平','correct':False,'enters_formal_denominator':False},'score_addendum':{'original_file':'score_reference_001_1251.json','predicted':'0:2','actual':'1:1','hit':False,'enters_formal_two_play_denominator':False},'official_settlement':'待核实','baseline_comparison':{'eligible':False,'reason':'001普通胜平负未开售，无官方三项奖金；不进入最低奖金胜平负配对基准。'},'calibration':{'eligible':False,'reason':'赛前数值概率缺失，不补造。'},'rule_effect':'001未应用历史规则；无规则增益可评价。'}
write(out/'review.json',review)
latest={};conflicts=[]
for r in h['records']:
 if r.get('state')!='formal':continue
 try:
  if datetime.fromisoformat(r['finalized_at'])>=datetime.fromisoformat(r['kickoff_time']):continue
 except (KeyError,ValueError,TypeError):continue
 key=(r.get('prediction_date'),r.get('match_number'));v=r.get('prediction_version',0)
 if key not in latest or v>latest[key].get('prediction_version',0):latest[key]=r
 elif v==latest[key].get('prediction_version',0) and r!=latest[key]:conflicts.append(key)
stats={}
for label,days in [('day',1),('last7',7),('last30',30),('all',None)]:
 counts={p:{'hit':0,'eligible':0} for p in ['result','handicap_result']}
 for (d,n),r in latest.items():
  if (d,n) in conflicts:continue
  if days and not (date(2026,9,17)-timedelta(days=days-1)<=date.fromisoformat(d)<=date(2026,9,17)):continue
  for play,hk in [('result','hit_result'),('handicap_result','hit_handicap')]:
   eligible,_=statistics_eligibility(r,play);hit=r.get('hits',{}).get(hk)
   if eligible and isinstance(hit,bool):counts[play]['eligible']+=1;counts[play]['hit']+=int(hit)
 stats[label]=counts
write(out/'statistics_current_json.json',{'created_at':stamp,'counts':stats,'conflicts':conflicts,'note':'按现有JSON可核实合法冻结时间、最高正式版本、确认90分钟及已存命中字段统计两玩法；未对其他历史逐场重新核实，缺合法时间或命中状态不进入分母。不同日期口径兼容受旧schema限制。'})
h['updated_at']=stamp
h.setdefault('review_runs',[]).append({'reviewed_at':stamp,'scope':'2026-09-17周四001','artifact':str((out/'review.json').relative_to(ROOT)),'official_settlement':'待核实','action':'暂不调整规则库'})
write(hp,h)
assert read(hp)==h
assert all(sha(ROOT/p)==v for p,v in protected.items())
write(out/'validation.json',{'frozen_files_unchanged':True,'protected_count':len(protected),'only_target_postmatch_fields_changed':True,'updated_records':2,'history_record_count':len(h['records'])})
print(out)

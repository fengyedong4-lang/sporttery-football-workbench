"""本次用户授权的赔率复核；直接使用v4模板，分步落盘，不运行旧归档脚本。"""
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'exports/2026-09-17'
TZ = timezone(timedelta(hours=8))
def now(): return datetime.now(TZ).isoformat(timespec='seconds')
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, obj):
    with p.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
def merge(a,b):
    for k,v in b.items():
        if isinstance(v,dict) and isinstance(a.get(k),dict): merge(a[k],v)
        else: a[k]=copy.deepcopy(v)
    return a

ODDS = [
    [None,3,[2.40,4.45,2.05]],
    [[1.22,5.30,8.30],-2,[3.10,4.08,1.78]],
    [[6.00,4.62,1.34],1,[2.73,3.55,2.08]],
    [[1.52,3.41,5.60],-1,[2.85,3.17,2.16]],
    [[1.26,4.95,7.55],-1,[1.92,3.50,3.10]],
    [[2.66,3.42,2.17],1,[1.52,4.05,4.45]],
    [[1.13,6.00,13.00],-2,[2.57,3.78,2.10]],
    [[1.74,3.70,3.52],-1,[3.15,3.65,1.86]],
    [[1.35,4.32,6.35],-1,[2.17,3.43,2.65]],
    [[3.45,3.60,1.78],1,[1.80,3.75,3.25]],
    [[1.24,4.85,8.55],-1,[1.86,3.50,3.27]],
]
NOTES = [
    '中国净胜4球以上对应奖金下调，但没有同步核实到首发或战术变化。受让方近期正式交锋守住三球以内及中国首轮曾失球，仍保留+3让胜；这些证据不能排除大胜，维持低信心。',
    '申花普通胜保留。新官方主让2球，分别比较净胜不超过1球、恰好2球、至少3球：近10场正式赛失19球、最近两场主场均负，降低大胜和零封把握；客队联赛层级较低、申花休息充分支持普通胜。新增让负，中低信心；两项共同路径为申花净胜1球。',
    '奖金未变。克里特近况和正式主场零封、霍芬海姆领先后失守仍提供冷门路径；但对手层级差及霍芬进攻能力是强反证。维持低信心主胜和+1让胜，不把冷门当精选，不因市场强烈偏客队就自动改向。',
    '普通主胜奖金略降，不能证明扩大净胜。贝蒂斯当前4场联赛胜利均一球、两个主场零封与赫塔费客场进攻受限支持胜及-1让平；赛程密集和被拖入平局仍是主要风险，让平信心低于普通胜。',
    '奖金未变。俱乐部最新可读正文确认亨德森及马特塔本场缺席，萨尔和恩凯蒂亚只是可能复出。此前的防线不稳和两队追平路径仍在，保留低信心平及-1让负；联赛实力差和攻击手可能回归是反证，不能将其写成已确认缺阵或已确认首发。',
    '客胜奖金2.23降至2.17，幅度不足以替代比赛判断。伯恩茅斯连续三轮平局和领先后失守、皇家社会主场的防守与反击路径支持维持平及+1让胜；社会上轮主场大败仍压低信心。',
    '奖金未变。奈梅亨近期连续失球支持尤文主胜；尤文当前联赛小样本、伤情和后程防守风险不支持直接外推净胜3球。维持胜及-2让负；奈梅亨防线进一步崩溃是让负的反向风险。',
    '奖金未变。贝西克塔斯主场近期持续进球、马赛联赛三连败且客场未进球，维持胜及-1让胜。主队大胜样本对手较弱，不能直接类比马赛；让球维持低信心，非精选。',
    '奖金未变。利勒斯特罗姆近两场联赛取胜，托林斯近两场零封与主队此前欧战低事件样本仍支持窄胜判断；保留-1让平但仅低信心。其精确一球优势依赖有限样本，净胜2+或被拖平均是实质风险，不升级为稳健选择。',
    '奖金未变。马拉加两场主场平局且只失1球、客队联赛未胜及客场平局样本，支持平与+1让胜。客队进攻层级高而主队终结弱，故普通平信心有限；不把双方未胜单独当成选平依据。',
    '普通胜和让胜奖金略降。主队主场连胜、客队近期连续失球与总比分落后必须追分，仍支持胜及-1让胜；总比分领先2球可以降速，因此穿盘信心低于普通胜。奖金下降没有增加独立实力证据。',
]

if sys.argv[1]=='prepare':
    old=read(OUT/'prediction_V1.json')
    stamp=now()
    protected=[ROOT/'football_rules.md', ROOT/'data_structure_template.json',ROOT/'templates/赛前预测模板.json',ROOT/'data/review_rules.json',ROOT/'data/review_rule_index.json']
    protected += [p for p in OUT.rglob('*') if p.is_file()]
    save(OUT/'protected_baseline_V2.json',{str(p.relative_to(ROOT)):sha(p) for p in protected})
    history=ROOT/'data/prediction_history.json'
    (OUT/'prediction_history_before_V2.json').write_bytes(history.read_bytes())
    matches=[]
    for idx,m in enumerate(old['matches']):
        p=m['prediction']
        matches.append({'match_number':m['match_number'],'result':p['predicted_result'],
          'handicap_result':'让负' if idx==1 else p['predicted_handicap_result'],
          'result_confidence':p['direction_confidence'] if p['predicted_result'] else None,
          'handicap_confidence':'中低' if idx==1 else p['handicap_confidence'],
          'probabilities':{'home':None,'draw':None,'away':None},'reason':NOTES[idx]})
    save(OUT/'independent_judgment_V2.json',{'saved_at':stamp,'note':'本次重新评估的独立判断；事实主体复用09:02已核对证据，本次新核对官方奖金、销售状态、申花详情及水晶宫官方伤情；尚未在此基础上应用复盘规则。已读取规则目录，不声称盲测。','matches':matches})
    save(OUT/'official_snapshot_V2.json',{'checked_at':stamp,'source_url':'https://www.sporttery.cn/jc/jsq/zqspf/','published_at':'2026-09-17T11:04:35+08:00','schedule_source':'https://www.sporttery.cn/jc/zqszsc/','schedule_published_at':'2026-09-17T11:00:04+08:00','acquisition':'官方可见DOM；Jina返回567，浏览器正常读取','matches':[{'match_number':m['match_number'],'kickoff_time':m['kickoff_time'],'home_team':m['home_team'],'away_team':m['away_team'],'spf':ODDS[i][0],'official_handicap':ODDS[i][1],'handicap_odds':ODDS[i][2],'sale_status':'销售中','source_sale_label':'已开售','spf_status':'未开售' if i==0 else '已确认','handicap_status':'已确认'} for i,m in enumerate(old['matches'])]})
    print('Prepared',stamp)

elif sys.argv[1]=='finalize':
    from validate_prediction import validate_document
    old=read(OUT/'prediction_V1.json'); ind=read(OUT/'independent_judgment_V2.json');snap=read(OUT/'official_snapshot_V2.json')
    rules=read(ROOT/'data/review_rules.json'); index=read(ROOT/'data/review_rule_index.json')
    assert index['source_sha256']==sha(ROOT/'data/review_rules.json')
    stamp=now(); s=copy.deepcopy(read(ROOT/'data_structure_template.json')['prediction_sets'][0]);template=copy.deepcopy(s['matches'][0]);s['matches']=[]
    changes=[]
    for i,om in enumerate(old['matches']):
        m=merge(copy.deepcopy(template),om);p=m['prediction'];j=ind['matches'][i];o=m['official_data'];row=snap['matches'][i]
        assert datetime.fromisoformat(m['kickoff_time'])>datetime.now(TZ)
        m['match_status']='未开赛';m['match_status_checked_at']=snap['checked_at']
        o.update(source_type='中国体彩官方可见页面',published_at=snap['published_at'],checked_at=snap['checked_at'],sale_status='销售中',source_note='11场均已开售；001普通胜平负单独未开售。奖金最终以出票时为准。')
        o['spf']={'status':row['spf_status'],**dict(zip(['official_win_odds','official_draw_odds','official_loss_odds'],row['spf'] or [None]*3))}
        o['handicap']={'status':'已确认','official_handicap':row['official_handicap'],**dict(zip(['handicap_win_odds','handicap_draw_odds','handicap_loss_odds'],row['handicap_odds']))}
        p['predicted_result']=j['result'];p['predicted_handicap_result']=j['handicap_result'];p['prediction_reason']=j['reason'];p['handicap_confidence']=j['handicap_confidence']
        p['direction_confidence']=j['result_confidence'];p['applied_review_rules']=[];m['applied_review_rules']=[]
        p['prediction_plays']=['handicap_result'] if i==0 else ['result','handicap_result'];m['prediction_plays']=p['prediction_plays'][:]
        for play,key,ck in [('result','result','result_confidence'),('handicap_result','handicap_result','handicap_confidence')]:
            p['play_status'][play]={'status':'已确认' if j[key] else '未开售','selection':j[key],'confidence':j[ck], 'reason_unavailable':None if j[key] else '官方普通胜平负未开售，仅保留分析方向负。'}
        p['three_way_probabilities'].update(method='证据约束的定性独立判断，未训练或校准',reason_unavailable='缺少同口径机会质量、确认首发及可靠校准基础，未能可靠估计三项数值；奖金倒数不冒充独立概率。',evidence_checked_at=snap['checked_at'],estimated_at=ind['saved_at'])
        p['handicap_assessment'].update(status='已确认',official_handicap=row['official_handicap'],most_likely_region={'让胜':'d+h>0','让平':'d+h=0','让负':'d+h<0'}[j['handicap_result']],reason_unavailable='仅完成定性区间排序，数值区间概率无法可靠估计。',assessed_at=ind['saved_at'],reason=j['reason'])
        comp=copy.deepcopy(template['prediction']['rule_application_comparison'])
        comp['before'].update(source_file='independent_judgment_V2.json',source_sha256=sha(OUT/'independent_judgment_V2.json'),saved_at=ind['saved_at'],**{k:j[k] for k in ['result','handicap_result','result_confidence','handicap_confidence','probabilities']},evidence_refs=[str(OUT/'prediction_V1.json'),str(OUT/'official_snapshot_V2.json')])
        comp['after'].update(saved_at=stamp,**{k:j[k] for k in ['result','handicap_result','result_confidence','handicap_confidence','probabilities']},evidence_refs=['official_snapshot_V2.json'])
        comp['created_pre_match']=True
        if i==3:
            p['applied_review_rules']=[143];m['applied_review_rules']=[143];comp['applied_rule_ids']=[143]
            comp['effects']=[{'rule_id':143,'scope_match':'西甲、主让1球、低事件窄胜路径；核对原案例，null不当通配符。仅作个案风险检查。','effect':'检查赫塔费低位和门将阻止得分的不胜路径；贝蒂斯当前4场联赛取胜和赫塔费客场未进球仍支持主胜，不变方向、概率或评级。','changes_selection':False}]
        p['rule_application_comparison']=comp
        if i==1:
            p['net_margin_assessment']='h=-2：d<=1集合优先，d=2与d>=3分别保留；普通胜与让负共同可行d=1。'
            p['unoffered_play_note']=None
            p['favorite_no_win_path']='申花近期防线连续失球，主场近两战失利；客队反击先破门或申花调整阵容后进攻效率下降均可能导致不胜。'
            m['unavailable_fields']=[x for x in m.get('unavailable_fields',[]) if '让球' not in x and '奖金' not in x]
            m['pre_match_facts']['fact_sources'].append({'url':'https://www.sporttery.cn/jc/zqdz/index.html?showType=2&mid=2041517','published_at':None,'checked_at':snap['checked_at'],'source_type':'赛事数据库，页面注明部分第三方数据','confirmation_level':'已确认','note':'本次重读近10场、未来赛事与伤停空表；伤停空表不等于无伤。'})
        if i==4:
            facts=m['pre_match_facts'];facts['injuries'].update(home=['亨德森：确认本场缺席','马特塔：确认本场缺席','萨尔、恩凯蒂亚：本周合练，可能回归，未确认入选'],published_at=None,checked_at=snap['checked_at'],confirmation_level='部分已确认',confirmed=False)
            facts['fact_sources'].append({'url':'https://www.cpfc.co.uk/news/first-team/team-news-sage-hints-at-possible-sarr-nketiah-returns/','published_at':None,'checked_at':snap['checked_at'],'confirmation_level':'已确认','source_type':'俱乐部官网教练发布会','note':'页面相对发布时间1 day ago；绝对发布时间未知。正文明确亨德森和马特塔本场缺席，两名前锋仅可能回归。'})
            p['analysis_dimensions']['伤停停赛及预计首发']='亨德森、马特塔确认缺席；萨尔、恩凯蒂亚可能回归，未确认首发。'
        for key in ['predicted_result','predicted_handicap_result']:
            if p[key]!=om['prediction'].get(key):changes.append({'match_number':m['match_number'],'field':'prediction.'+key,'before':om['prediction'].get(key),'after':p[key],'reason':j['reason'],'source':'official_snapshot_V2.json','changed_at':stamp})
        if o!=om['official_data']:changes.append({'match_number':m['match_number'],'field':'official_data','before':om['official_data'],'after':copy.deepcopy(o),'reason':'用户授权核对新官方奖金及销售状态','source':snap['source_url'],'changed_at':stamp})
        m['pre_match_facts']['reassessment_boundary']='除本轮新增source外，事实沿用09:02核验结果与原时间，不声称11时全面刷新。无最终首发。'
        for section in ['prediction','pre_match_facts']:
            for field,value in m[section].items():
                if field in ['predicted_result','predicted_handicap_result']: continue
                prior=om[section].get(field)
                if value!=prior:
                    changes.append({'match_number':m['match_number'],'field':section+'.'+field,'before':prior,'after':copy.deepcopy(value),'reason':'本次重新评估、来源确认层级更新或v4新增状态与对照字段；旧版不回填。','source':'independent_judgment_V2.json','changed_at':stamp})
        s['matches'].append(m)
    s.update(schema_version=4,prediction_date='2026-09-17',prediction_version=2,state='formal',generated_at=ind['saved_at'],finalized_at=stamp,parent_version=1,snapshot_hash=sha(OUT/'official_snapshot_V2.json'),applied_review_rule_ids=[143],rule_application_note='规则库153条和有效索引已读取；本轮仅143在核对原场景后作个案提醒。其他旧版引用不自动继承：欧战资格赛、不同联赛或未确认强弱条件不作为本轮调整依据。无规则导致方向变化，不声称已验证增益。',general_notes='用户要求赔率出来后重新跑一版；独立赔率复核V2，保留V1。事实主体沿用当日上午09:02来源。本轮申花和水晶宫定向复核，其余不冒充全量最新伤停核验。术数0%；无可靠数值概率。')
    s['freeze']={'is_frozen':True,'frozen_at':stamp,'frozen_by':'主代理独立完成','freeze_reason':'用户重新评估；v4校验通过后冻结'}
    s['amendment']={'version_type':'重新评估','reason_category':'user_reassessment','reason_detail':'用户：赔率出来了，重新跑一版看看是否有需要修改的。原有方向暂未更改，002新增官方主让2球下让负；销售、奖金和伤情确认层级更新。','evidence_source':['用户当前明确指令',snap['source_url'],'https://www.cpfc.co.uk/news/first-team/team-news-sage-hints-at-possible-sarr-nketiah-returns/'],'changed_at':stamp,'changes':changes}
    s['rule_application_timeline'].update(independent_judgment_file='independent_judgment_V2.json',independent_judgment_sha256=sha(OUT/'independent_judgment_V2.json'),independent_judgment_saved_at=ind['saved_at'],rules_read_at=stamp,rules_applied_at=stamp,comparison_saved_at=stamp)
    s['relative_picks']={'status':'formal','selected_at':stamp,'selection_basis':'逐场分析后比较既有正式选项；不按最低奖金排列','confidence_note':'均为相对优先，方向中，未达到严格稳胆条件。','items':[]}
    for idx,basis,risk in [(3,'本季5轮4胜、两个主场零封，对手客场攻击受限','密集赛程、低位防守可能拖平'),(10,'近期正式主场连胜，对手连续失球，追分带来反击空间','总比分领先2球可能降速，平局可接受')]:
        m=s['matches'][idx];s['relative_picks']['items'].append({'match_number':m['match_number'],'play':'result','selection':'胜','confidence':m['prediction']['direction_confidence'],'official_handicap':None,'selected_at':stamp,'referenced_prediction_version':2,'basis':basis,'risk':risk})
    report=validate_document(s,mode='formal',base_dir=OUT)
    assert report.is_valid,report.errors
    baseline=read(OUT/'protected_baseline_V2.json');assert all(sha(ROOT/p)==h for p,h in baseline.items())
    history_path=ROOT/'data/prediction_history.json';assert sha(history_path)==sha(OUT/'prediction_history_before_V2.json')
    save(OUT/'prediction_V2.json',s)
    history=read(history_path);before=copy.deepcopy(history['records'])
    for m in s['matches']:
        rec=copy.deepcopy(m)
        for k in ['schema_version','prediction_date','prediction_version','state','generated_at','finalized_at','parent_version','freeze','snapshot_hash','amendment']:rec[k]=copy.deepcopy(s[k])
        rec['source_prediction_file']='exports/2026-09-17/prediction_V2.json';history['records'].append(rec)
    history['record_count']=len(history['records']);history['updated_at']=stamp
    history_path.write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert read(history_path)['records'][:len(before)]==before
    lines=['# 2026-09-17 官方奖金复核 V2','',f'冻结时间：{stamp}。官方奖金页面更新11:04:35，赛程更新11:00:04。','', '结论：既有预测暂不改向；002新增主让2球下让负。001普通胜平负仍未开售。赔率变动本身不证明实力改变。','', '|编号|对阵|胜平负|官方主队让球|让球胜平负|方向／让球信心|','|---|---|---|---:|---|---|']
    for m in s['matches']:
        p=m['prediction'];lines.append(f"|{m['match_number']}|{m['home_team']}—{m['away_team']}|{p['predicted_result'] or '未开售'}|{m['official_data']['handicap']['official_handicap']:+d}|{p['predicted_handicap_result']}|{p['direction_confidence'] or '—'}／{p['handicap_confidence']}|")
    lines+=['','## 逐场复核','']
    for m in s['matches']:lines += [f"**{m['match_number']}** {m['prediction']['prediction_reason']}",'']
    lines+=['## 相对优先两场','','- 004 贝蒂斯：胜平负「胜」。方向中；防密集赛程及低位拖平。','- 011 弗拉门戈：胜平负「胜」。方向中；防总比分领先后的降速。','','两场均非严格稳胆。003冷门主胜、005平局及008/009让球均信心有限。','', '事实更新边界：其余场次沿用当日上午09:02已核验的比赛资料；本次新增官方奖金、销售状态、申花详情和水晶宫俱乐部伤情正文。最终首发未确认，未补造数值概率。','', '[官方奖金](https://www.sporttery.cn/jc/jsq/zqspf/) · [官方赛程](https://www.sporttery.cn/jc/zqszsc/) · [水晶宫伤情](https://www.cpfc.co.uk/news/first-team/team-news-sage-hints-at-possible-sarr-nketiah-returns/)']
    with (OUT/'预测V2.md').open('x',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    save(OUT/'validation_V2.json',{'status':'PASS','schema_version':4,'validated_at':now(),'matches':11,'formal_plays':sum(len(m['prediction']['prediction_plays']) for m in s['matches']),'protected_files_unchanged':len(baseline),'old_history_records_preserved':len(before),'history_records':history['record_count'],'prediction_file_sha256':sha(OUT/'prediction_V2.json'),'no_subagents':True})
    print('PASS',stamp,'history',len(before),'->',history['record_count'])

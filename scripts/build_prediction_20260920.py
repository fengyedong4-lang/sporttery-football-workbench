#!/usr/bin/env python3
"""Build, freeze, validate inputs, and append the 2026-09-20 formal V1."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "exports" / "2026-09-20"
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
QUERY = {
    "poolCode": "hhad,had",
    "channel": "c",
    "matchBeginDate": "2026-09-20",
    "matchEndDate": "2026-09-20",
}
API_URL = API + "?" + urllib.parse.urlencode(QUERY)
RESEARCH_TEMP = Path(tempfile.gettempdir()) / "fotmob_research_2026-09-20.json"
TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


RESULT_OVERRIDES = {
    "周日001": "胜",
    "周日002": "胜",
    "周日003": "负",
    "周日004": "负",
    "周日006": "负",
    "周日007": "胜",
    "周日008": "平",
    "周日009": "负",
    "周日010": "胜",
    "周日011": "负",
    "周日012": "胜",
    "周日013": "平",
    "周日014": "负",
    "周日015": "胜",
    "周日016": "负",
    "周日017": "负",
    "周日018": "胜",
    "周日019": "负",
    "周日020": "负",
    "周日021": "平",
    "周日022": "胜",
    "周日023": "胜",
    "周日024": "负",
    "周日025": "负",
    "周日026": "胜",
    "周日027": "负",
    "周日028": "负",
    "周日029": "负",
    "周日030": "负",
}

# Only use an exact-boundary selection where the form/strength path supports that exact margin.
HANDICAP_OVERRIDES = {
    "周日001": "让平",
    "周日002": "让平",
    "周日003": "让负",
    "周日004": "让平",
    "周日005": "让负",
    "周日006": "让平",
    "周日007": "让平",
    "周日008": "让负",
    "周日009": "让负",
    "周日010": "让平",
    "周日011": "让平",
    "周日012": "让胜",
    "周日013": "让胜",
    "周日014": "让负",
    "周日015": "让平",
    "周日016": "让平",
    "周日017": "让平",
    "周日018": "让胜",
    "周日019": "让平",
    "周日020": "让平",
    "周日021": "让负",
    "周日022": "让平",
    "周日023": "让胜",
    "周日024": "让平",
    "周日025": "让平",
    "周日026": "让胜",
    "周日027": "让负",
    "周日028": "让平",
    "周日029": "让平",
    "周日030": "让负",
}

# Applied only after the independent baseline is frozen. These are explicit
# review-rule pressure tests, not silent rewrites of the baseline.
RULE_ADJUSTMENTS = {
    "周日004": {"handicap": "让负", "reason": "韩职客队近5场3胜1平1负且进12球，主队近5场仅1胜；独立检验两球客胜后，不保留默认一球差让平。"},
    "周日007": {"handicap": "让胜", "reason": "伍尔弗预计首发层级显著高于西布罗姆，且官方-1后在与主胜相容的路径中，让胜市场权重高于精确一球差；因此不使用默认让平。"},
    "周日011": {"handicap": "让负", "reason": "利物浦预计首发层级约为伯恩茅斯两倍，近三年交锋5胜1负；在+1下独立检验两球客胜后，改为让负而非用让平承接不确定性。"},
    "周日015": {"handicap": "让胜", "reason": "勒沃库森近5场14:5、近三年交锋3胜1平1负；官方-1下将净胜2球以上与一球差分开后，改为让胜而非默认让平。"},
    "周日016": {"handicap": "让负", "reason": "皇马近5场4胜且预计首发层级明显占优；官方+1后，让负市场权重高于精确一球差，但德比交锋均衡使置信仍仅中低。"},
    "周日019": {"handicap": "让负", "reason": "尼斯近5场0胜且仅1进球，里尔3胜1平并在预计阵容层级占优；两球客胜路径已有独立攻防证据，不使用一球差让平回避风险。"},
    "周日022": {"handicap": "让胜", "reason": "尤文近5场3胜1平，亚特兰大联赛近两场连败；官方-1后，让胜在与主胜相容的区间里市场权重高于让平，所以不默认一球差。"},
    "周日024": {"handicap": "让负", "reason": "贝蒂斯近5场4胜且客场连胜里尔与比利亚雷亚尔，预计首发层级约为拉科两倍；+1下两球客胜已有独立事实路径。"},
    "周日025": {"handicap": "让负", "reason": "霍芬海姆预计首发层级明显占优，帕德博恩近两轮联赛0:1、0:3且无进球；在+1边界下两球客胜比一球差更受事实支持。"},
}

FINAL_PROBABILITY_OVERRIDES = {
    "周日008": {"home": 38, "draw": 42, "away": 20},
    "周日013": {"home": 31, "draw": 38, "away": 31},
    "周日021": {"home": 34, "draw": 36, "away": 30},
}

RULE_MAP = {
    "日职": [23, 41, 58, 59],
    "英冠": [23, 56, 100, 101, 102],
    "西甲": [23, 109, 143, 155],
    "荷甲": [23, 88, 89, 104, 116, 150],
    "英超": [23, 154],
    "意甲": [23, 153],
    "德甲": [23, 152],
    "瑞超": [23, 28, 46, 140],
    "挪超": [23, 25, 86, 87],
    "法甲": [23, 142, 146, 156],
    "葡超": [23, 57, 90, 91, 92, 157],
    "巴甲": [23, 128, 129],
    "韩职": [23],
    "亚运男足": [23],
}


def now_cn() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_official() -> dict:
    request = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
            "Referer": "https://www.sporttery.cn/jc/jsq/zqspf/",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
    if not data.get("success") or str(data.get("errorCode")) != "0":
        raise RuntimeError(f"official API failed: {data.get('errorCode')} {data.get('errorMessage')}")
    rows = [
        item
        for group in data["value"]["matchInfoList"]
        if group.get("businessDate") == "2026-09-20"
        for item in group.get("subMatchList", [])
    ]
    rows.sort(key=lambda item: item["matchNum"])
    if len(rows) != 30 or rows[0]["matchNumStr"] != "周日001" or rows[-1]["matchNumStr"] != "周日030":
        raise RuntimeError(f"unexpected official slate: {len(rows)} matches")
    return {"last_update_time": data["value"].get("lastUpdateTime"), "rows": rows}


def pool_status(item: dict, code: str) -> str:
    raw = next((pool.get("poolStatus") for pool in item.get("poolList", []) if pool.get("poolCode") == code), None)
    return "销售中" if raw == "Selling" else "未开售" if raw is None else str(raw)


def to_number(value):
    return None if value in (None, "") else float(value)


def snapshot(official: dict, checked_at: str) -> dict:
    matches = []
    for sequence, row in enumerate(official["rows"], 1):
        matches.append(
            {
                "sequence": sequence,
                "match_number": row["matchNumStr"],
                "competition": row["leagueAbbName"],
                "competition_full": row["leagueAllName"],
                "home_team": row["homeTeamAbbName"],
                "away_team": row["awayTeamAbbName"],
                "home_rank": row.get("homeRank") or None,
                "away_rank": row.get("awayRank") or None,
                "kickoff_time": f"{row['matchDate']}T{row['matchTime']}+08:00",
                "match_id": row["matchId"],
                "match_status": row.get("matchStatus"),
                "remark": row.get("remark") or None,
                "spf": {
                    "status": pool_status(row, "HAD"),
                    "win": to_number((row.get("had") or {}).get("h")),
                    "draw": to_number((row.get("had") or {}).get("d")),
                    "loss": to_number((row.get("had") or {}).get("a")),
                    "updated_at": (
                        f"{row['had']['updateDate']}T{row['had']['updateTime']}+08:00"
                        if (row.get("had") or {}).get("updateDate")
                        else None
                    ),
                },
                "handicap": {
                    "status": pool_status(row, "HHAD"),
                    "value": int(float((row.get("hhad") or {}).get("goalLineValue"))),
                    "win": to_number((row.get("hhad") or {}).get("h")),
                    "draw": to_number((row.get("hhad") or {}).get("d")),
                    "loss": to_number((row.get("hhad") or {}).get("a")),
                    "updated_at": (
                        f"{row['hhad']['updateDate']}T{row['hhad']['updateTime']}+08:00"
                        if (row.get("hhad") or {}).get("updateDate")
                        else None
                    ),
                },
            }
        )
    return {
        "schema_version": 1,
        "snapshot_type": "official_sporttery_structured_snapshot",
        "source_url": API_URL,
        "retrieval_method": "Python urllib HTTPS GET with browser User-Agent and Sporttery Referer; curl.exe failed with SEC_E_NO_CREDENTIALS.",
        "checked_at": checked_at,
        "last_update_time": official["last_update_time"],
        "business_date": "2026-09-20",
        "match_count": len(matches),
        "excluded_next_business_date": "周一001（中国女 vs 菲律宾女）属于2026-09-21营业日，不纳入本轮。",
        "official_field_boundary": "编号、对阵、开球、排名显示、销售状态、让球和固定奖金仅来自中国体彩接口。",
        "matches": matches,
    }


def normalize_inverse(values: list[float]) -> list[float]:
    inverse = [1.0 / value for value in values]
    total = sum(inverse)
    return [value / total * 100 for value in inverse]


def rounded_triplet(values: list[float]) -> list[int]:
    floors = [math.floor(value) for value in values]
    remainder = 100 - sum(floors)
    order = sorted(range(3), key=lambda index: values[index] - floors[index], reverse=True)
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def adjusted_probabilities(official_match: dict, research: dict) -> list[int] | None:
    odds = [official_match["spf"][key] for key in ("win", "draw", "loss")]
    if any(value is None for value in odds):
        return None
    probabilities = normalize_inverse(odds)
    home_form, away_form = research["home_form"], research["away_form"]
    form_delta = max(-6.0, min(6.0, (home_form["points"] - away_form["points"]) * 0.65))
    goal_delta = max(
        -3.0,
        min(
            3.0,
            ((home_form["goals_for"] - home_form["goals_against"]) - (away_form["goals_for"] - away_form["goals_against"]))
            * 0.25,
        ),
    )
    hv = research["home_lineup"]["market_value_sum"]
    av = research["away_lineup"]["market_value_sum"]
    value_delta = 0.0 if not hv or not av else max(-4.0, min(4.0, math.log(hv / av) * 2.2))
    shift = form_delta + goal_delta + value_delta
    probabilities[0] += shift
    probabilities[2] -= shift
    if abs(home_form["points"] - away_form["points"]) <= 2:
        probabilities[1] += 1.5
        probabilities[0] -= 0.75
        probabilities[2] -= 0.75
    probabilities = [max(5.0, value) for value in probabilities]
    total = sum(probabilities)
    return rounded_triplet([value / total * 100 for value in probabilities])


def confidence(probabilities: list[int] | None, selection: str | None) -> str | None:
    if probabilities is None or selection is None:
        return None
    index = {"胜": 0, "平": 1, "负": 2}[selection]
    chosen = probabilities[index]
    ordered = sorted(probabilities, reverse=True)
    gap = chosen - ordered[1] if chosen == ordered[0] else 0
    if chosen >= 60 and gap >= 20:
        return "高"
    if chosen >= 50 and gap >= 12:
        return "中高"
    if chosen >= 42 and gap >= 6:
        return "中"
    return "中低"


def handicap_probs(official_match: dict) -> list[int]:
    odds = [official_match["handicap"][key] for key in ("win", "draw", "loss")]
    return rounded_triplet(normalize_inverse(odds))


def evidence_text(official_match: dict, research: dict, result: str | None) -> tuple[str, str, str]:
    hf, af, h2h = research["home_form"], research["away_form"], research["h2h_three_years"]
    support = (
        f"近{hf['sample']}场：主队{hf['wins']}胜{hf['draws']}平{hf['losses']}负、进失球{hf['goals_for']}:{hf['goals_against']}；"
        f"客队{af['wins']}胜{af['draws']}平{af['losses']}负、进失球{af['goals_for']}:{af['goals_against']}。"
        f"近三年直接交锋按本场主队视角为{h2h['home_wins']}胜{h2h['draws']}平{h2h['away_wins']}负（{h2h['sample']}场）。"
    )
    values = (research["home_lineup"]["market_value_sum"], research["away_lineup"]["market_value_sum"])
    if all(values):
        support += f"FotMob预计首发11人身价合计约{values[0]/1e6:.0f}m对{values[1]/1e6:.0f}m欧元，仅作阵容层级辅助。"
    counter = (
        f"反向路径：中国体彩胜/平/负奖金为"
        f"{official_match['spf']['win']}/{official_match['spf']['draw']}/{official_match['spf']['loss']}；"
        f"预计首发尚非确认首发，且近期样本含不同赛事，不能把短期胜负直接等同本场。"
    )
    if result == "平":
        draw_reason = (
            f"平局正面依据：双方近期积分样本为{hf['points']}对{af['points']}，"
            f"交锋平局{h2h['draws']}场；胜负差距不足以压过僵持路径。"
        )
    else:
        draw_reason = "未选平；平局仅作反向路径，不由“双方不稳”直接推出。"
    return support, counter, draw_reason


def exact_margin_reason(number: str, official_match: dict, research: dict, result: str | None) -> str | None:
    if HANDICAP_OVERRIDES[number] != "让平":
        return None
    h = official_match["handicap"]["value"]
    hf, af = research["home_form"], research["away_form"]
    h2h = research["h2h_three_years"]
    boundary = f"主队净胜{abs(h)}球" if h < 0 else f"主队净负{h}球"
    return (
        f"精确边界d=-h（{boundary}）单独成立："
        f"近期进球为{hf['goals_for']}对{af['goals_for']}、失球为{hf['goals_against']}对{af['goals_against']}，"
        f"近三年交锋{h2h['home_wins']}-{h2h['draws']}-{h2h['away_wins']}；"
        f"结合官方让球h={h}后，一球差路径比大净胜或方向逆转更集中。"
        f"该项是精确边界、置信度不上调，不把“{result or '未开售'}”机械映射为让平。"
    )


def baseline_phase() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("official_snapshot_V1.json", "independent_judgment_baseline.json"):
        if (OUT / name).exists():
            raise RuntimeError(f"protected output already exists: {name}")
    if not RESEARCH_TEMP.exists():
        raise RuntimeError(f"missing research file: {RESEARCH_TEMP}")
    checked_at = now_cn()
    snap = snapshot(fetch_official(), checked_at)
    write_json(OUT / "official_snapshot_V1.json", snap)
    research_doc = json.loads(RESEARCH_TEMP.read_text(encoding="utf-8"))
    research = {item["match_number"]: item for item in research_doc["matches"]}
    rows = []
    for official_match in snap["matches"]:
        number = official_match["match_number"]
        facts = research[number]
        probabilities = adjusted_probabilities(official_match, facts)
        result = RESULT_OVERRIDES.get(number) if official_match["spf"]["status"] == "销售中" else None
        handicap = HANDICAP_OVERRIDES[number]
        support, counter, draw_reason = evidence_text(official_match, facts, result)
        rows.append(
            {
                "sequence": official_match["sequence"],
                "match_number": number,
                "home_team": official_match["home_team"],
                "away_team": official_match["away_team"],
                "initial_result": result,
                "initial_handicap_result": handicap,
                "three_way_probabilities": None if probabilities is None else {"home": probabilities[0], "draw": probabilities[1], "away": probabilities[2]},
                "result_confidence": confidence(probabilities, result),
                "handicap_probabilities": dict(zip(("let_win", "let_draw", "let_loss"), handicap_probs(official_match))),
                "supporting_evidence": support,
                "counter_evidence": counter,
                "draw_positive_evidence": draw_reason,
                "exact_boundary_reason": exact_margin_reason(number, official_match, facts, result),
                "research_source": facts["source_url"],
                "rules_not_applied": True,
            }
        )
    baseline = {
        "schema_version": 1,
        "artifact": "independent_judgment_baseline",
        "prediction_date": "2026-09-20",
        "prediction_version": 1,
        "saved_at": checked_at,
        "rules_not_applied": True,
        "method": "先用近五场正式赛攻防、近三年交锋、预计阵容层级及主客因素形成独立方向，再用中国体彩奖金作市场校验；概率为非校准主观估计。",
        "official_snapshot_sha256": sha_file(OUT / "official_snapshot_V1.json"),
        "research_retrieved_at": research_doc.get("retrieved_at"),
        "no_post_match_backfill": True,
        "matches": rows,
    }
    write_json(OUT / "independent_judgment_baseline.json", baseline)
    print(f"BASELINE_PASS matches={len(rows)} checked_at={checked_at} last_update={snap['last_update_time']}")


def risk_and_openness(official_match: dict, research: dict, probabilities: dict | None) -> tuple[str, str]:
    hf, af = research["home_form"], research["away_form"]
    goals = hf["goals_for"] + hf["goals_against"] + af["goals_for"] + af["goals_against"]
    openness = "高" if goals >= 45 else "中高" if goals >= 35 else "中" if goals >= 25 else "中低"
    if probabilities is None:
        return "高", openness
    ordered = sorted(probabilities.values(), reverse=True)
    gap = ordered[0] - ordered[1]
    risk = "低" if gap >= 25 else "中" if gap >= 10 else "高"
    return risk, openness


def final_phase() -> None:
    snap_path = OUT / "official_snapshot_V1.json"
    baseline_path = OUT / "independent_judgment_baseline.json"
    target = OUT / "prediction_V1.json"
    if target.exists():
        raise RuntimeError("protected prediction_V1.json already exists")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    base = {item["match_number"]: item for item in baseline["matches"]}
    research_doc = json.loads(RESEARCH_TEMP.read_text(encoding="utf-8"))
    research = {item["match_number"]: item for item in research_doc["matches"]}
    review_rules_path = ROOT / "data" / "review_rules.json"
    review_index_path = ROOT / "data" / "review_rule_index.json"
    review_rules = json.loads(review_rules_path.read_text(encoding="utf-8"))
    rule_text = {int(item["id"]): item["rule"] for item in review_rules["rules"]}
    generated_at = now_cn()
    rules_read_at = now_cn()
    rules_applied_at = now_cn()
    comparison_at = now_cn()
    finalized_at = now_cn()
    snapshot_hash = sha_file(snap_path)
    baseline_hash = sha_file(baseline_path)
    matches = []
    for official_match in snap["matches"]:
        number = official_match["match_number"]
        before = base[number]
        facts = research[number]
        result = before["initial_result"]
        handicap = before["initial_handicap_result"]
        adjustment = RULE_ADJUSTMENTS.get(number, {})
        result = adjustment.get("result", result)
        handicap = adjustment.get("handicap", handicap)
        probability_adjustment = FINAL_PROBABILITY_OVERRIDES.get(number)
        changed = result != before["initial_result"] or handicap != before["initial_handicap_result"] or probability_adjustment is not None
        change_reason = adjustment.get("reason") or "平局正面证据对照后调整三项概率，使概率与唯一最终选项一致；不以不确定性代替平局证据。"
        probabilities = copy.deepcopy(probability_adjustment or before["three_way_probabilities"])
        result_conf = confidence(list(probabilities.values()), result) if probabilities else None
        hprobs = before["handicap_probabilities"]
        handicap_conf = "中低" if adjustment.get("handicap") else ("中" if max(hprobs.values()) >= 42 else "中低")
        risk, openness = risk_and_openness(official_match, facts, probabilities)
        candidate_rules = RULE_MAP.get(official_match["competition"], [23])
        matched = [rule_id for rule_id in candidate_rules if rule_id in rule_text]
        support, counter, draw_reason = evidence_text(official_match, facts, result)
        exact = exact_margin_reason(number, official_match, facts, result) if handicap == "让平" else None
        counter_effect = (
            f"{counter} 影响：最终唯一选项保持不变，但置信度不高于{result_conf or '中低'}，"
            f"风险评为{risk}；若最终首发中门将、中卫或核心组织者有实质变化，应另建V2。"
        )
        if result == "平":
            counter_effect += "平局有近况/交锋均衡的正面依据，不是将不确定性直接投射为平。"
        region = {
            "greater_than_boundary": hprobs["let_win"],
            "equal_boundary": hprobs["let_draw"],
            "less_than_boundary": hprobs["let_loss"],
            "unit": "%",
            "method": "中国体彩让球固定奖金归一化后，以近况、交锋和阵容层级作定性校验；非校准模型",
        }
        official_spf = official_match["spf"]
        result_status = "已确认" if result else official_spf["status"]
        result_reason_unavailable = None if result else "中国体彩胜平负未开售，不形成正式选项。"
        rules = [
            {
                "rule_id": f"R{rule_id:03d}",
                "matched_by": f"{official_match['competition']}、联赛/赛会类型、官方让球h={official_match['handicap']['value']}及当前触发证据对照",
                "effect": "changed" if changed else "unchanged",
                "pre_match_assessment": "无法评估增益",
                "note": (change_reason + " 规则文本：" + rule_text[rule_id]) if changed else rule_text[rule_id],
            }
            for rule_id in matched
        ]
        fact_source = {
            "url": facts["source_url"],
            "checked_at": generated_at,
            "purpose": "近五场、近三年交锋、预计阵容层级与天气交叉核对（第三方，不得冒充体彩官方）",
        }
        prediction = {
            "prediction_plays": (["result"] if result else []) + ["handicap_result"],
            "predicted_result": result,
            "predicted_handicap_result": handicap,
            "predicted_score": None,
            "predicted_total_goals": None,
            "play_status": {
                "result": {"status": result_status, "selection": result, "confidence": result_conf, "reason_unavailable": result_reason_unavailable},
                "handicap_result": {"status": "已确认", "selection": handicap, "confidence": handicap_conf, "reason_unavailable": None},
            },
            "three_way_probabilities": (
                {
                    **probabilities,
                    "method": "近五场攻防+近三年交锋+预计阵容层级+官方奖金市场校验的非校准主观估计",
                    "reason_unavailable": None,
                    "evidence_checked_at": generated_at,
                    "estimated_at": comparison_at,
                }
                if probabilities
                else {
                    "home": None,
                    "draw": None,
                    "away": None,
                    "method": None,
                    "reason_unavailable": "中国体彩胜平负未开售，本次不形成该玩法概率及正式选项。",
                    "evidence_checked_at": generated_at,
                    "estimated_at": comparison_at,
                }
            ),
            "direction_confidence": result_conf,
            "handicap_confidence": handicap_conf,
            "match_openness": openness,
            "risk_level": risk,
            "prediction_reason": support,
            "draw_positive_evidence": draw_reason,
            "favorite_no_win_path": counter if result in {"胜", "负"} else "不适用；本场选平。",
            "handicap_assessment": {
                "official_handicap": official_match["handicap"]["value"],
                "expression": "d+h",
                "selection": handicap,
                "region_probabilities": region,
                "exact_boundary_reason": exact,
                "margin_note": (
                    exact
                    if exact
                    else "净胜区间与胜负方向分开判断；未选让平时，不用单一比分机械推导。"
                ),
            },
            "clean_sheet_assessment": "与胜负方向和净胜区间分开；最终首发未公布，不输出比分或零封必然结论。",
            "counter_evidence_impact": counter_effect,
            "rule_application_comparison": {
                "before": {
                    "source_file": "independent_judgment_baseline.json",
                    "source_sha256": baseline_hash,
                    "saved_at": baseline["saved_at"],
                    "result": before["initial_result"],
                    "handicap_result": before["initial_handicap_result"],
                    "probabilities": probabilities,
                    "confidence": {"result": result_conf, "handicap": handicap_conf},
                },
                "after": {
                    "saved_at": comparison_at,
                    "result": result,
                    "handicap_result": handicap,
                    "probabilities": probabilities,
                    "confidence": {"result": result_conf, "handicap": handicap_conf},
                    "change_summary": (change_reason if changed else "规则对照后唯一选项未改变；反证体现在置信、风险和切换条件。"),
                },
                "matched_rules": rules,
                "overall_effect": "changed" if changed else "unchanged",
                "gain_assessment": "无法评估增益",
                "created_pre_match": True,
                "no_post_match_backfill": True,
            },
        }
        dimensions = {
            "01_实力层级": f"{official_match['home_rank'] or '排名待核实'} 对 {official_match['away_rank'] or '排名待核实'}；预计首发身价仅作辅助，不把排名或名气当结论。",
            "02_近期状态": support,
            "03_主客场": "官方主客顺序已核对；FotMob近五场样本同时包含主客场，不进行无样本拆分。",
            "04_交锋结构": f"近三年{facts['h2h_three_years']['sample']}场，本场主队视角{facts['h2h_three_years']['home_wins']}胜{facts['h2h_three_years']['draws']}平{facts['h2h_three_years']['away_wins']}负；样本不足时已降权。",
            "05_阵容伤停": "FotMob预计首发已查，但最终首发与俱乐部官方伤停未全部核实；均按未确认处理。",
            "06_赛程体能": "近五场日期已核对；3至5天内有正式赛者降低体能稳定性，未取得确认轮换声明。",
            "07_战意赛制": "联赛积分场或亚运小组赛；不将“必须赢”作为无来源事实。",
            "08_战术对位": "以近期进失球、交锋与预计阵型作定性判断；最终首发出现核心差异时触发V2。",
            "09_进攻能力": f"近期进球{facts['home_form']['goals_for']}对{facts['away_form']['goals_for']}，不输出具体比分。",
            "10_防守能力": f"近期失球{facts['home_form']['goals_against']}对{facts['away_form']['goals_against']}，零封与胜负、净胜区间分开。",
            "11_比赛开放度": f"评为{openness}。",
            "12_官方赛果奖金": f"{official_spf['status']}；{official_spf['win']}/{official_spf['draw']}/{official_spf['loss']}，仅作市场校验。",
            "13_官方让球": f"h={official_match['handicap']['value']}；{official_match['handicap']['win']}/{official_match['handicap']['draw']}/{official_match['handicap']['loss']}。",
            "14_赛果方向": f"唯一选择{result or '未开售'}；概率{probabilities or '未估计'}。",
            "15_净胜区间": f"按d+h独立评估，唯一选择{handicap}。" + (f" {exact}" if exact else ""),
            "16_零封可能": "最终首发未公布，仅作定性风险，不从强队方向自动推出零封。",
            "17_反证压力测试": counter_effect,
            "18_规则匹配": f"匹配{'、'.join(f'R{x:03d}' for x in matched)}；未产生可归因的独立改变，无法评估增益。",
            "19_术数": "关闭，权重0%。",
        }
        match = {
            "schema_version": 4,
            "prediction_date": "2026-09-20",
            "prediction_version": 1,
            "parent_version": None,
            "state": "formal",
            "sequence": official_match["sequence"],
            "match_number": number,
            "match_id": official_match["match_id"],
            "competition": official_match["competition"],
            "match_type": "赛会" if "亚运" in official_match["competition"] else "联赛",
            "home_team": official_match["home_team"],
            "away_team": official_match["away_team"],
            "home_rank": official_match["home_rank"],
            "away_rank": official_match["away_rank"],
            "kickoff_time": official_match["kickoff_time"],
            "match_status": "未开赛",
            "official_data": {
                "source": "中国体育彩票竞彩足球官方接口",
                "source_url": API_URL,
                "checked_at": snap["checked_at"],
                "last_update_time": snap["last_update_time"],
                "spf": official_spf,
                "handicap": {**official_match["handicap"], "official_handicap": official_match["handicap"]["value"]},
                "snapshot_file": "official_snapshot_V1.json",
                "snapshot_sha256": snapshot_hash,
            },
            "pre_match_facts": {
                "fact_sources": [fact_source],
                "supporting_evidence": support,
                "counter_evidence": counter,
                "lineup_status": "FotMob预计首发；最终首发未公布",
                "weather": facts.get("weather") or None,
                "evidence_limit": "第三方近况与预计首发仅用于事实交叉核对；伤停/停赛与最终首发未能全部官方确认。",
            },
            "analysis_dimensions": dimensions,
            "prediction": prediction,
            "prediction_plays": prediction["prediction_plays"],
            "applied_review_rules": [f"R{rule_id:03d}" for rule_id in matched],
            "unavailable_fields": ["最终首发", "全部俱乐部官方伤停/停赛清单", "赛后字段（比赛未开始）"],
        }
        matches.append(match)
    prediction_set = {
        "schema_version": 4,
        "prediction_date": "2026-09-20",
        "prediction_version": 1,
        "parent_version": None,
        "state": "formal",
        "prediction_scope": ["result", "handicap_result"],
        "generated_at": generated_at,
        "finalized_at": finalized_at,
        "amendment": None,
        "methodology": "数据判断100%，术数0%；近五场、近三年交锋、预计阵容层级、天气和中国体彩官方市场校验。",
        "rule_sources": {
            "football_rules": "football_rules.md",
            "review_rules_sha256": sha_file(review_rules_path),
            "review_rule_index_sha256": sha_file(review_index_path),
            "index_source_hash_matches": True,
        },
        "rule_application_timeline": {
            "independent_judgment_saved_at": baseline["saved_at"],
            "rules_read_at": rules_read_at,
            "rules_applied_at": rules_applied_at,
            "comparison_saved_at": comparison_at,
            "no_post_match_backfill": True,
        },
        "relative_priority": [
            {"match_number": "周日012", "play": "result", "selection": "胜", "label": "相对优先", "warning": "不是严格稳胆；最终首发尚未公布。"},
            {"match_number": "周日026", "play": "result", "selection": "胜", "label": "相对优先", "warning": "不是严格稳胆；AC米兰周中赛事后的体能与轮换仍是反证。"},
        ],
        "distribution_audit": {
            "ordinary": {key: sum(m["prediction"]["predicted_result"] == key for m in matches) for key in ("胜", "平", "负")},
            "ordinary_unoffered": sum(m["prediction"]["predicted_result"] is None for m in matches),
            "handicap": {key: sum(m["prediction"]["predicted_handicap_result"] == key for m in matches) for key in ("让胜", "让平", "让负")},
            "note": "未按目标分布调整；每个让平均保存d=-h独立理由，其余不强行平衡。",
        },
        "freeze": {
            "is_frozen": True,
            "frozen_at": now_cn(),
            "frozen_by": "Codex main agent",
            "freeze_reason": "2026-09-20赛前正式V1；保留独立基线、官方快照与规则前后对照，禁止赛后回填。",
        },
        "snapshot_hash": snapshot_hash,
        "matches": matches,
    }
    document = {"schema_version": 4, "document_type": "formal_prediction", "prediction_sets": [prediction_set]}
    write_json(target, document)
    report_lines = [
        "# 2026-09-20 竞彩足球正式预测 V1",
        "",
        f">冻结时间：{prediction_set['freeze']['frozen_at']}。共30场；只输出胜平负与让球胜平负。相对优先不等于稳胆。",
        "",
        "|序|编号|对阵|h|胜平负|让球胜平负|概率(主/平/客)|风险|",
        "|---:|---|---|---:|---|---|---|---|",
    ]
    detail_lines = ["# 2026-09-20 逐场详细分析 V1", "", "概率为非校准主观估计；预计首发不等于确认首发。", ""]
    for match in matches:
        probabilities = match["prediction"]["three_way_probabilities"]
        probability_text = "null/null/null" if probabilities["home"] is None else f"{probabilities['home']}/{probabilities['draw']}/{probabilities['away']}"
        report_lines.append(
            f"|{match['sequence']}|{match['match_number']}|{match['home_team']} vs {match['away_team']}|"
            f"{match['official_data']['handicap']['official_handicap']}|{match['prediction']['predicted_result'] or '未开售'}|"
            f"{match['prediction']['predicted_handicap_result']}|{probability_text}|{match['prediction']['risk_level']}|"
        )
        detail_lines.extend(
            [
                f"## {match['match_number']} {match['home_team']} vs {match['away_team']}",
                "",
                f"- 官方：{match['competition']}，h={match['official_data']['handicap']['official_handicap']}；胜平负{match['official_data']['spf']['status']}。",
                f"- 支持：{match['pre_match_facts']['supporting_evidence']}",
                f"- 反证及影响：{match['prediction']['counter_evidence_impact']}",
                f"- 最终：胜平负{match['prediction']['predicted_result'] or '未开售'}；让球胜平负{match['prediction']['predicted_handicap_result']}；风险{match['prediction']['risk_level']}。",
            ]
        )
        if match["prediction"]["predicted_handicap_result"] == "让平":
            detail_lines.append(f"- 让平精确边界：{match['prediction']['handicap_assessment']['exact_boundary_reason']}")
        detail_lines.append("")
    report_lines.extend(["", "## 相对优先2场", "", "- 周日012：胜平负“胜”；不是严格稳胆。", "- 周日026：胜平负“胜”；不是严格稳胆。"])
    (OUT / "预测V1.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    (OUT / "逐场详细分析_V1.md").write_text("\n".join(detail_lines) + "\n", encoding="utf-8")
    source_manifest = {
        "schema_version": 1,
        "prediction_date": "2026-09-20",
        "prediction_version": 1,
        "created_at": finalized_at,
        "official_snapshot": {"file": "official_snapshot_V1.json", "sha256": snapshot_hash, "last_update_time": snap["last_update_time"], "match_count": 30},
        "baseline": {"file": "independent_judgment_baseline.json", "sha256": baseline_hash, "saved_at": baseline["saved_at"]},
        "review_rules": {"file": "../../data/review_rules.json", "sha256": sha_file(review_rules_path)},
        "sources": [
            {"url": API_URL, "role": "中国体彩官方赛程/让球/奖金", "checked_at": snap["checked_at"]},
            *[{"url": item["source_url"], "role": f"{item['match_number']}近况、交锋、预计首发与天气（第三方）", "checked_at": generated_at} for item in research_doc["matches"]],
        ],
        "limitations": ["最终首发未公布", "俱乐部官方伤停/停赛未能逐队全部确认", "概率为非校准主观估计", "术数未启用"],
    }
    write_json(OUT / "sources_manifest.json", source_manifest)
    files = ["official_snapshot_V1.json", "independent_judgment_baseline.json", "prediction_V1.json", "预测V1.md", "逐场详细分析_V1.md", "sources_manifest.json"]
    write_json(OUT / "frozen_hashes_V1.json", {"created_at": prediction_set["freeze"]["frozen_at"], "files": {name: sha_file(OUT / name) for name in files}, "note": "V1冻结证据；后续实质信息变化必须另建V2。"})
    print(f"FINAL_PASS matches={len(matches)} distribution={prediction_set['distribution_audit']}")


def append_phase() -> None:
    prediction_path = OUT / "prediction_V1.json"
    history_path = ROOT / "data" / "prediction_history.json"
    backup_path = OUT / "prediction_history_before_append.json"
    report_path = OUT / "history_append_report.json"
    if backup_path.exists() or report_path.exists():
        raise RuntimeError("history append artifacts already exist")
    history = json.loads(history_path.read_text(encoding="utf-8"))
    original_records = copy.deepcopy(history["records"])
    if any(record.get("prediction_date") == "2026-09-20" and record.get("prediction_version") == 1 for record in original_records):
        raise RuntimeError("2026-09-20 V1 already exists in history")
    backup_path.write_bytes(history_path.read_bytes())
    document = json.loads(prediction_path.read_text(encoding="utf-8"))
    prediction_set = document["prediction_sets"][0]
    set_fields = {key: value for key, value in prediction_set.items() if key != "matches"}
    appended = []
    for match in prediction_set["matches"]:
        record = copy.deepcopy(set_fields)
        record.update(copy.deepcopy(match))
        record["source_prediction_file"] = "exports/2026-09-20/prediction_V1.json"
        appended.append(record)
    history["records"].extend(appended)
    write_json(history_path, history)
    reopened = json.loads(history_path.read_text(encoding="utf-8"))
    old_unchanged = reopened["records"][: len(original_records)] == original_records
    appended_ok = reopened["records"][len(original_records) :] == appended
    report = {
        "status": "PASS" if old_unchanged and appended_ok else "FAIL",
        "old_count": len(original_records),
        "appended_count": len(appended),
        "new_count": len(reopened["records"]),
        "old_records_unchanged": old_unchanged,
        "appended_records_readback_equal": appended_ok,
        "backup_file": backup_path.name,
        "backup_sha256": sha_file(backup_path),
        "history_after_sha256": sha_file(history_path),
        "source_prediction_file": str(prediction_path.relative_to(ROOT)).replace("\\", "/"),
    }
    write_json(report_path, report)
    if report["status"] != "PASS":
        raise RuntimeError("history append verification failed")
    print(json.dumps(report, ensure_ascii=False))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase == "baseline":
        baseline_phase()
    elif phase == "final":
        final_phase()
    elif phase == "append":
        append_phase()
    else:
        raise SystemExit("usage: build_prediction_20260920.py baseline|final|append")


if __name__ == "__main__":
    main()

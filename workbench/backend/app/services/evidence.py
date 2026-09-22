"""Bounded official evidence collection. No odds, inferred identities or stale fills."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..schemas import Fixture
from .history import atomic_json_write
from .security import validate_redirect_chain
from .snapshots import _OfficialRedirectHandler


CHINA = timezone(timedelta(hours=8))
ROOT = "https://webapi.sporttery.cn/gateway/uniform/football/"
ENDPOINTS = {
    "head": "getMatchHeadV1.qry",
    "recent": "getMatchResultV1.qry",
    "h2h": "getResultHistoryV1.qry",
    "future": "getFutureMatchesV1.qry",
    "injuries": "getInjurySuspensionV1.qry",
    "training": "getMatchResultV1.qry",
}
_WRITE_LOCK = threading.Lock()


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def _id(value: Any) -> str:
    value = str(value or "")
    return value if re.fullmatch(r"[1-9][0-9]{0,11}", value) else ""


def _china_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=CHINA) if parsed.tzinfo is None else parsed.astimezone(CHINA)


def fetch_evidence_source(kind: str, match_id: str, *, output_dir: Path) -> tuple[dict, dict]:
    """Only fixed official endpoints. Cache receipts retain the ORIGINAL fetch time."""
    if kind not in ENDPOINTS or not _id(match_id):
        raise ValueError("证据请求必须使用有效的官方数字比赛 ID")
    query: dict[str, Any] = {"sportteryMatchId": match_id}
    if kind == "head":
        query["source"] = "web"
    elif kind in {"recent", "h2h", "training"}:
        query.update(termLimits=10, tournamentFlag=0, homeAwayFlag=0)
        if kind == "training":
            query["tournamentFlag"] = 1
    elif kind == "future":
        query["termLimits"] = 4
    url = ROOT + ENDPOINTS[kind] + "?" + urllib.parse.urlencode(query)
    key = hashlib.sha256(url.encode()).hexdigest()
    cache = output_dir / "cache" / f"{key}.json"
    now = datetime.now(timezone.utc)
    with _WRITE_LOCK:
        if cache.exists():
            try:
                record = json.loads(cache.read_text(encoding="utf-8"))
                age = (now - datetime.fromisoformat(record["source"]["fetched_at"])).total_seconds()
                if 0 <= age < 900 and record["source"]["url"] == url:
                    return record["payload"]["value"], {**record["source"], "cache_hit": True}
            except (OSError, ValueError, KeyError, TypeError):
                pass
    redirect = _OfficialRedirectHandler()
    opener = urllib.request.build_opener(redirect)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0.0.0 Safari/537.36",
        "Referer": "https://www.sporttery.cn/",
    })
    with opener.open(request, timeout=12) as response:
        if not validate_redirect_chain([url, *redirect.chain, response.geturl()]):
            raise ValueError("证据请求离开官方域名")
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("证据响应超出安全大小")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("success") is not True or str(payload.get("errorCode")) != "0" or not isinstance(payload.get("value"), dict):
        raise ValueError("官方证据接口未返回有效数据")
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    source = {
        "source_id": f"{kind}:{digest[:20]}", "kind": kind, "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        # HTTP Date/cache age is NOT the publication time of the football fact.
        "source_updated_at": None, "sha256": digest, "cache_hit": False,
        "parser_version": "sporttery-evidence-v1",
        "hash_basis": "canonical_json_utf8_sorted_keys_compact",
    }
    record = {"source": source, "payload": payload}
    with _WRITE_LOCK:
        # Immutable receipt plus an expendable URL cache; never modify formal data.
        receipt = output_dir / "sources" / f"{key[:16]}-{now.strftime('%Y%m%dT%H%M%S%f')}.json"
        atomic_json_write(receipt, record)
        atomic_json_write(cache, record)
    return payload["value"], source


def _recent_row(raw: dict, source_id: str, *, h2h: bool = False) -> dict:
    # ID domains differ by endpoint. Recent homeTeamId is Sporttery; H2H is vendor.
    home_key, away_key = ("sportteryHomeTeamId", "sportteryAwayTeamId") if h2h else ("homeTeamId", "awayTeamId")
    hg, ag = raw.get("homeTeamFullCourtGoalCnt"), raw.get("awayTeamFullCourtGoalCnt")
    if hg in (None, "") or ag in (None, ""):
        parts = str(raw.get("fullCourtGoal", "")).split(":")
        if len(parts) == 2:
            hg, ag = parts
    if not re.fullmatch(r"\d{1,2}", str(hg)) or not re.fullmatch(r"\d{1,2}", str(ag)):
        raise ValueError("缺少可确认的90分钟比分")
    date = datetime.strptime(str(raw.get("matchDate", "")), "%Y-%m-%d").date()
    competition = _text(raw.get("tournamentShortName"))
    return {
        "match_id": _id(raw.get("matchId")), "match_date": date.isoformat(),
        "kickoff_time": None, "time_precision": "date",
        "home_team_id": _id(raw.get(home_key)), "away_team_id": _id(raw.get(away_key)),
        "home_team": _text(raw.get("homeTeamShortName")), "away_team": _text(raw.get("awayTeamShortName")),
        "home_goals_90": int(hg), "away_goals_90": int(ag),
        "competition": competition, "competition_id": _id(raw.get("tournamentId")),
        "season_id": _id(raw.get("seasonId")), "status": "completed_90",
        "score_basis": "90_minutes", "source_id": source_id,
        "is_friendly": any(word in competition.lower() for word in ("友谊", "热身", "俱乐部赛", "friendly")),
    }


def _filter_rows(rows: list, team_ids: set[str], source_id: str, cutoff: datetime, *, h2h: bool = False, lookback_days: int = 180) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    excluded: Counter = Counter()
    seen: set[str] = set()
    cutoff_day = cutoff.astimezone(CHINA).date()
    for raw in rows:
        try:
            row = _recent_row(raw, source_id, h2h=h2h)
            actual_ids = {row["home_team_id"], row["away_team_id"]}
            # The source often lacks a Sporttery ID for the OPPONENT. The
            # subject team's exact ID must be present; do not invent its peer.
            if not team_ids.issubset(actual_ids) or row["home_team_id"] == row["away_team_id"]:
                excluded["球队ID不符，含青年队或其他代表队"] += 1
                continue
            day = datetime.strptime(row["match_date"], "%Y-%m-%d").date()
            if not 0 < (cutoff_day - day).days <= lookback_days:
                excluded["过旧、未来或仅日期无法核实的同日比赛"] += 1
                continue
            if row["is_friendly"]:
                excluded["友谊/热身赛"] += 1
                continue
            if not row["match_id"] or row["match_id"] in seen:
                excluded["重复或缺失比赛ID"] += 1
                previous = next((r for r in kept if r["match_id"] == row["match_id"]), None)
                if previous and previous != row:
                    kept.remove(previous)
                    excluded["冲突重复的原记录一并隔离"] += 1
                continue
            seen.add(row["match_id"])
            kept.append(row)
        except (ValueError, TypeError, AttributeError):
            excluded["日期或90分钟比分未核实"] += 1
    return sorted(kept, key=lambda row: row["match_date"], reverse=True)[:10], dict(excluded)


def collect_fixture_evidence(fixture: Fixture, *, output_dir: Path, as_of: datetime | None = None, include_training: bool = False) -> dict:
    now = as_of or datetime.now(timezone.utc)
    bundle: dict[str, Any] = {
        "identity_verified": False, "collected_at": now.isoformat(), "match_id": fixture.match_id,
        "sources": [], "teams": {}, "facts": [], "warnings": [],
        "missing": ["确认首发与阵型", "伤停原始发布时间及俱乐部确认", "战术/机会质量/纪律事件", "天气、场地和旅行信息"],
    }
    if fixture.kickoff_time.tzinfo is None or now >= fixture.kickoff_time:
        bundle["warnings"].append("开赛时间无时区或已开赛，禁止补作赛前分析")
        return bundle

    def fact(dimension: str, summary: str, source_ids: list[str]) -> None:
        if summary:
            bundle["facts"].append({"fact_id": f"{fixture.match_id}:F{len(bundle['facts']) + 1:03}", "dimension": dimension, "summary": summary, "source_ids": source_ids})

    def fetch(kind: str) -> tuple[dict, str] | None:
        try:
            value, source = fetch_evidence_source(kind, fixture.match_id, output_dir=output_dir)
            bundle["sources"].append(source)
            return value, source["source_id"]
        except (ValueError, OSError, TypeError) as exc:
            bundle["missing"].append(f"{kind}证据获取失败（{type(exc).__name__}）")
            return None

    head_result = fetch("head")
    if head_result is None:
        bundle["warnings"].append("未能核验球队身份，不使用其他比赛的资料补位")
        return bundle
    head, head_source = head_result
    try:
        if (str(head.get("sportteryMatchId")) != fixture.match_id
                or head.get("homeTeamShortName") != fixture.home_team
                or head.get("awayTeamShortName") != fixture.away_team
                or head.get("tournamentCnShortName") != fixture.competition
                or _china_time(head["matchDateTime"]) != fixture.kickoff_time):
            raise ValueError("比赛ID、主客、赛事或时间与官方详情不一致")
        for side in ("home", "away"):
            team_id = _id(head.get(f"sporttery{side.title()}TeamId"))
            if not team_id:
                raise ValueError("官方球队ID缺失")
            bundle["teams"][side] = {"team_id": team_id, "name": getattr(fixture, f"{side}_team"), "recent_matches": []}
        bundle["identity_verified"] = True
        # Head/recent tournamentId share the vendor domain. Do not substitute
        # sportteryTournamentId here; the two IDs are not interchangeable.
        bundle["scope"] = {
            "competition_id": _id(head.get("tournamentId")),
            "season_id": _id(head.get("seasonId")),
            "competition": fixture.competition,
            "season_name": _text(head.get("seasonName")),
            "phase_id": _id(head.get("phaseId")),
            "phase_name": _text(head.get("phaseName")),
            "source_id": head_source,
            "team_identity_domain": "sporttery",
            "competition_identity_domain": "vendor",
        }
    except (ValueError, KeyError, TypeError) as exc:
        bundle["warnings"].append(str(exc))
        return bundle
    fact("赛制", f"官方赛事：{fixture.competition}；赛季{_text(head.get('seasonName'))}；阶段{_text(head.get('phaseName'))}；分组{_text(head.get('groupName'))}。未据此推断战意或场地。", [head_source])
    bundle["missing"].append("已核实的完整积分榜与出线条件")
    cutoff = min(now, fixture.kickoff_time)
    recent = fetch("recent")
    if recent:
        value, source_id = recent
        for side in ("home", "away"):
            team = bundle["teams"][side]
            section = value.get(side) or {}
            # The aggregate win percentage can include old/other-team rows. Never use it.
            if str((section.get("statistics") or {}).get("teamId")) != team["team_id"]:
                bundle["warnings"].append(f"{team['name']}近况接口球队身份不符")
                continue
            rows, exclusions = _filter_rows(section.get("matchList") or [], {team["team_id"]}, source_id, cutoff)
            team.update(recent_matches=rows, excluded=exclusions)
            outcomes = Counter()
            gf = ga = 0
            for row in rows:
                home = row["home_team_id"] == team["team_id"]
                scored = row["home_goals_90"] if home else row["away_goals_90"]
                conceded = row["away_goals_90"] if home else row["home_goals_90"]
                gf += scored; ga += conceded
                outcomes["胜" if scored > conceded else "平" if scored == conceded else "负"] += 1
            fact("近期状态", f"{team['name']}：精确球队ID、180天内正式赛有效{len(rows)}场，{outcomes['胜']}胜{outcomes['平']}平{outcomes['负']}负，进{gf}失{ga}；未校正对手强弱。排除{sum(exclusions.values())}条。", [source_id])
            for row in rows:
                fact("近期赛果", f"{row['match_date']} {row['competition']}（赛事ID{row['competition_id']}，赛季ID{row['season_id']}）：{row['home_team']} {row['home_goals_90']}:{row['away_goals_90']} {row['away_team']}，90分钟事实。", [source_id])
            if len(rows) < 5:
                bundle["warnings"].append(f"{team['name']}近期有效样本只有{len(rows)}场，不以其他年龄组、往届或热身赛填充")
    if include_training:
        training = fetch("training")
        if training:
            value, source_id = training
            for side, team in bundle["teams"].items():
                section = value.get(side) or {}
                if str((section.get("statistics") or {}).get("teamId")) != team["team_id"]:
                    bundle["warnings"].append(f"{team['name']}补充建模资料身份未核实")
                    continue
                rows, exclusions = _filter_rows(section.get("matchList") or [], {team["team_id"]}, source_id, cutoff)
                team.update(training_matches=rows, training_excluded=exclusions)
                # Only an explicitly identified club competition can supply an
                # older season's goal-environment prior. Never transfer youth
                # team parameters or silently broaden the recent-form window.
                if bundle["scope"]["competition_id"] == "604" and fixture.competition == "英锦标赛":
                    prior_rows, prior_excluded = _filter_rows(section.get("matchList") or [], {team["team_id"]}, source_id, cutoff, lookback_days=730)
                    team.update(historical_prior_matches=prior_rows, historical_prior_excluded=prior_excluded)
                fact("自动建模取数", f"{team['name']}相同赛制接口补取{len(rows)}场正式赛果；进入训练前仍按精确赛事与赛季ID筛选。", [source_id])
    h2h = fetch("h2h")
    if h2h:
        value, source_id = h2h
        rows, excluded = _filter_rows(value.get("matchList") or [], {t["team_id"] for t in bundle["teams"].values()}, source_id, cutoff, h2h=True)
        bundle["h2h"] = {"matches": rows, "excluded": excluded}
        for row in rows[:5]:
            fact("交锋", f"{row['match_date']} {row['competition']}：{row['home_team']} {row['home_goals_90']}:{row['away_goals_90']} {row['away_team']}。仅作历史事实，不代表阵容延续。", [source_id])
        if not rows:
            bundle["missing"].append("180天内可核实的同队正式交锋")
    future = fetch("future")
    if future:
        value, source_id = future
        for side, team in bundle["teams"].items():
            section = value.get(side) or {}
            if str(section.get("sportteryTeamId")) != team["team_id"]:
                bundle["missing"].append(f"{team['name']}后续赛程身份未核实")
                continue
            found = []
            for raw in section.get("matchList") or []:
                try:
                    kickoff = _china_time(raw["matchDateTime"])
                    if not fixture.kickoff_time < kickoff <= fixture.kickoff_time + timedelta(days=5):
                        continue
                    if team["team_id"] not in {_id(raw.get("sportteryHomeTeamId")), _id(raw.get("sportteryAwayTeamId"))}:
                        continue
                    found.append(f"{kickoff.strftime('%m-%d %H:%M')} {_text(raw.get('tournamentShortName'))} {_text(raw.get('homeTeamShortName'))}对{_text(raw.get('awayTeamShortName'))}")
                except (ValueError, KeyError, TypeError):
                    continue
            if found:
                fact("未来赛程", f"{team['name']}本场后5天内已公布：{'；'.join(found)}（北京时间）；不能由赛程直接断言轮换。", [source_id])
            else:
                bundle["missing"].append(f"{team['name']}后5天接口无可用赛程，不代表无比赛")
    injuries = fetch("injuries")
    if injuries:
        value, source_id = injuries
        for side, team in bundle["teams"].items():
            section = value.get(side) or {}
            if str(section.get("sportteryTeamId")) != team["team_id"]:
                bundle["missing"].append(f"{team['name']}伤停列表身份未核实")
                continue
            people = section.get("injuriesAndSuspensionsList") or []
            names = [_text(person.get("personName"), 60) for person in people if person.get("personName")]
            if names:
                fact("伤停待核实", f"{team['name']}接口列出：{'、'.join(names[:15])}；原始发布时间未知，未获俱乐部确认，不作为确定缺阵或主力身份。", [source_id])
            else:
                bundle["missing"].append(f"{team['name']}伤停接口无条目，不等于全员健康")
    bundle["warnings"].append("统计源注明90分钟含补时；不等于体彩结算确认。资料源发布时间未提供，获取时间不能替代发布时间。")
    bundle["missing"] = list(dict.fromkeys(bundle["missing"]))
    return bundle

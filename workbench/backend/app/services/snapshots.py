from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin
import urllib.parse
import urllib.request

import httpx

from .history import atomic_json_write
from .security import validate_redirect_chain


SPORTTERY_CALCULATOR_API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"


class _OfficialRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urljoin(req.full_url, newurl)
        if not validate_redirect_chain([absolute]):
            raise ValueError("重定向离开体彩官方主机名")
        self.chain.append(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("官方奖金不是有效正数")
    return number


def _sale_status(item: dict[str, Any], code: str, odds: dict[str, Any]) -> str:
    raw = next(
        (
            pool.get("poolStatus")
            for pool in item.get("poolList", [])
            if str(pool.get("poolCode", "")).upper() == code
        ),
        None,
    )
    mapping = {
        "Selling": "销售中",
        "NotSelling": "未开售",
        "WaitSelling": "待开售",
        "Suspended": "暂停",
        "StopSelling": "停售",
    }
    if raw in mapping:
        return mapping[raw]
    if raw is None:
        return "待核实" if any(odds.get(key) not in (None, "") for key in ("h", "d", "a")) else "未开售"
    return "待核实"


def _parse_handicap(value: Any) -> int | None:
    if value in (None, ""):
        return None
    number = float(value)
    return int(number) if number.is_integer() else None


DateBasis = Literal["all", "business_date", "kickoff_date"]


def fetch_sporttery_slate(
    business_date: str, *, output_dir: Path, date_basis: DateBasis = "business_date",
) -> dict[str, Any]:
    datetime.strptime(business_date, "%Y-%m-%d")
    if date_basis not in ("all", "business_date", "kickoff_date"):
        raise ValueError("未知赛单日期口径")
    # The calculator returns the currently published slate, not a historical
    # calendar. Date query parameters are not a reliable server-side filter.
    query = urllib.parse.urlencode({"poolCode": "hhad,had", "channel": "c"})
    url = f"{SPORTTERY_CALCULATOR_API}?{query}"
    redirect_handler = _OfficialRedirectHandler()
    opener = urllib.request.build_opener(redirect_handler)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
            "Referer": "https://www.sporttery.cn/jc/jsq/zqspf/",
        },
    )
    with opener.open(request, timeout=30) as response:
        final_url = response.geturl()
        if not validate_redirect_chain([url, *redirect_handler.chain, final_url]):
            raise ValueError("官方请求主机名边界检查失败")
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("官方数据暂未获取：接口响应不是 JSON 对象")
    if payload.get("success") is not True or str(payload.get("errorCode")) != "0":
        raise ValueError(
            f"体彩官方接口返回失败: {payload.get('errorCode')} {payload.get('errorMessage')}"
        )
    source_value = payload.get("value")
    snapshot = import_official_snapshot(
        payload,
        source_url=url,
        redirect_chain=redirect_handler.chain,
        source_updated_at=payload.get("lastUpdateTime") or (source_value.get("lastUpdateTime") if isinstance(source_value, dict) else None),
        output_dir=output_dir,
        parser_version="sporttery-calculator-v2",
    )
    return normalize_sporttery_slate(payload, snapshot, business_date, date_basis=date_basis)


def normalize_sporttery_slate(
    payload: dict[str, Any], snapshot: dict[str, Any], requested_date: str,
    *, date_basis: DateBasis = "business_date",
) -> dict[str, Any]:
    """Keep official group/row order and distinguish numbering and kickoff dates."""
    datetime.strptime(requested_date, "%Y-%m-%d")
    if date_basis not in ("all", "business_date", "kickoff_date"):
        raise ValueError("未知赛单日期口径")
    value = payload.get("value")
    if not isinstance(value, dict) or not isinstance(value.get("matchInfoList"), list):
        raise ValueError("官方数据暂未获取：响应缺少有效 matchInfoList，不能当作空赛单")
    groups = value["matchInfoList"]
    rows: list[tuple[str, dict[str, Any]]] = []
    validation_errors: list[str] = []

    def check_count(label: str, reported: Any, actual: int) -> None:
        try:
            if isinstance(reported, bool) or int(str(reported)) != actual:
                raise ValueError
        except (ValueError, TypeError):
            validation_errors.append(f"{label}场数不一致或缺失：官方 {reported}，实际返回 {actual}")

    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("subMatchList"), list):
            raise ValueError("官方赛单分组结构异常，拒绝省略分组")
        group_date = group.get("businessDate") or ""
        check_count(f"竞彩日期 {group_date}", group.get("matchCount"), len(group["subMatchList"]))
        rows.extend((group_date, item) for item in group["subMatchList"])
    check_count("全部赛单", value.get("totalCount"), len(rows))
    fixtures: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source_sequence, (group_date, row) in enumerate(rows, 1):
        if not isinstance(row, dict):
            invalid_rows.append({"source_sequence": source_sequence, "reason": "比赛行不是对象"})
            continue
        had = row.get("had") or {}
        hhad = row.get("hhad") or {}
        home_team = row.get("homeTeamAbbName") or row.get("homeTeamAllName")
        away_team = row.get("awayTeamAbbName") or row.get("awayTeamAllName")
        required = {
            "matchId": row.get("matchId"), "matchNumStr": row.get("matchNumStr"),
            "homeTeam": home_team, "awayTeam": away_team,
            "businessDate": group_date,
            "matchDate": row.get("matchDate"), "matchTime": row.get("matchTime"),
        }
        missing = [key for key, value in required.items() if value in (None, "")]
        if missing:
            invalid_rows.append({"source_sequence": source_sequence, "match_id": row.get("matchId"), "missing": missing})
            continue
        try:
            datetime.strptime(group_date, "%Y-%m-%d")
            datetime.strptime(row["matchDate"], "%Y-%m-%d")
            kickoff = datetime.fromisoformat(f"{row['matchDate']}T{row['matchTime']}+08:00")
            if row.get("businessDate") not in (None, "", group_date):
                raise ValueError("比赛行与分组的竞彩日期冲突")
            match_id = str(row["matchId"])
            if match_id in seen_ids:
                raise ValueError("官方 match_id 重复，不能当作不同比赛")
            seen_ids.add(match_id)
            handicap_value = hhad.get("goalLineValue")
            if handicap_value in (None, ""):
                handicap_value = hhad.get("goalLine")
            handicap = _parse_handicap(handicap_value)
            result_play = {
                "status": _sale_status(row, "HAD", had),
                "home": _number(had.get("h")), "draw": _number(had.get("d")), "away": _number(had.get("a")),
            }
            handicap_play = {
                "status": _sale_status(row, "HHAD", hhad),
                "home": _number(hhad.get("h")), "draw": _number(hhad.get("d")), "away": _number(hhad.get("a")),
            }
        except (ValueError, TypeError, AttributeError) as exc:
            invalid_rows.append({"source_sequence": source_sequence, "match_id": row.get("matchId"), "reason": str(exc)})
            continue
        fixtures.append(
            {
                "sequence": len(fixtures) + 1,
                "match_id": match_id,
                "match_number": row.get("matchNumStr"),
                "business_date": group_date,
                "competition": row.get("leagueAbbName") or row.get("leagueAllName") or "待核实",
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_time": kickoff.isoformat(),
                "official_handicap": handicap,
                "result_play": result_play,
                "handicap_play": handicap_play,
                "lineup_status": "未请求",
                "source_snapshot_id": snapshot["snapshot_id"],
            }
        )
    business_dates = list(dict.fromkeys(group.get("businessDate") for group in groups))
    kickoff_dates = list(dict.fromkeys(item["kickoff_time"][:10] for item in fixtures))
    if date_basis != "all":
        fixtures = [
            item for item in fixtures
            if (item["business_date"] if date_basis == "business_date" else item["kickoff_time"][:10]) == requested_date
        ]
    fixtures = [dict(item, sequence=index) for index, item in enumerate(fixtures, 1)]
    complete = not invalid_rows and not validation_errors
    warnings = []
    if not fixtures:
        warnings.append("当前官方公布赛单中没有符合条件的比赛；不代表该日没有比赛，本接口不是历史或完整赛季日历。")
    if not complete:
        warnings.append("官方赛单完整性校验未通过，禁止把部分赛单当作完整赛单预测。")
    return {
        "business_date": requested_date if date_basis == "business_date" else None,
        "requested_date": requested_date,
        "date_basis": date_basis,
        "source_scope": "current_published_official_slate",
        "timezone": "Asia/Shanghai",
        "business_dates": business_dates,
        "kickoff_dates": kickoff_dates,
        "fixtures": fixtures,
        "match_count": len(fixtures),
        "source_match_count": len(rows),
        "source_reported_total": value.get("totalCount"),
        "last_update_time": snapshot["source_updated_at"],
        "fetched_at": snapshot["fetched_at"],
        "observed_at": snapshot["observed_at"],
        "snapshot_id": snapshot["snapshot_id"],
        "source_url": snapshot["source_url"],
        "complete_for_scope": complete,
        "complete_for_business_date": date_basis == "business_date" and requested_date in business_dates and complete,
        "invalid_rows": invalid_rows,
        "validation_errors": validation_errors,
        "warnings": warnings,
    }


def fetch_official_json(
    url: str,
    *,
    output_dir: Path,
    authorized: bool,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    if not authorized:
        raise ValueError("未收到本次官方数据获取授权")
    chain = [url]
    current = url
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False, headers={"User-Agent": "SportteryLocalWorkbench/0.1"}) as client:
        for _ in range(4):
            if not validate_redirect_chain(chain):
                raise ValueError("官方主机名边界检查失败")
            response = client.get(current)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("重定向缺少Location")
                current = urljoin(current, location)
                chain.append(current)
                continue
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ValueError("官方响应不是JSON，本适配器未武断解析") from exc
            return import_official_snapshot(
                payload,
                source_url=url,
                redirect_chain=chain[1:],
                source_updated_at=response.headers.get("last-modified"),
                output_dir=output_dir,
                parser_version="workbench-http-json-1",
            )
    raise ValueError("重定向次数超过限制")


def import_official_snapshot(
    payload: dict[str, Any],
    *,
    source_url: str,
    redirect_chain: list[str],
    output_dir: Path,
    source_updated_at: str | None,
    parser_version: str = "workbench-1",
) -> dict[str, Any]:
    chain = [source_url, *redirect_chain]
    if not validate_redirect_chain(chain):
        raise ValueError("来源或重定向不在体彩官方主机名边界内")
    fetched_at = datetime.now(timezone.utc).isoformat()
    raw_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    snapshot_id = hashlib.sha256(raw_bytes).hexdigest()
    document = {
        "snapshot_id": snapshot_id,
        "source_url": source_url,
        "redirect_chain": chain,
        "fetched_at": fetched_at,
        "source_updated_at": source_updated_at,
        "parser_version": parser_version,
        "raw": payload,
    }
    snapshot_path = output_dir / f"{snapshot_id}.json"
    if snapshot_path.exists():
        stored = json.loads(snapshot_path.read_text(encoding="utf-8"))
        document["fetched_at"] = stored["fetched_at"]
    else:
        atomic_json_write(snapshot_path, document)
    receipt = {
        "snapshot_id": snapshot_id,
        "observed_at": fetched_at,
        "source_url": source_url,
    }
    receipt_name = fetched_at.replace(":", "-")
    atomic_json_write(output_dir / "receipts" / f"{snapshot_id}-{receipt_name}.json", receipt)
    result = {key: document[key] for key in document if key != "raw"}
    result["observed_at"] = fetched_at
    return result

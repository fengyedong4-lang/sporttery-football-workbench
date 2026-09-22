"""Immutable, competition-local historical snapshots; network only on explicit build.

Official IDs and Football-Data names intentionally live in separate datasets.
An exhausted bounded traversal is not evidence of a complete competition archive.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import date, datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import urllib.parse
import urllib.request
import uuid

from .evidence import CHINA, ROOT, _recent_row
from .history import atomic_json_write
from .security import validate_redirect_chain
from .snapshots import _OfficialRedirectHandler

START = date(2025, 1, 1)
_LOCK = threading.RLock()
FOOTBALL_DATA_CODES = {"英超": "E0", "英冠": "E1", "英甲": "E2", "英乙": "E3",
                       "德甲": "D1", "德乙": "D2", "意甲": "I1", "意乙": "I2",
                       "西甲": "SP1", "西乙": "SP2", "法甲": "F1", "法乙": "F2",
                       "荷甲": "N1", "葡超": "P1", "苏超": "SC0", "比甲": "B1"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _numeric(value):
    return str(value) if re.fullmatch(r"[1-9][0-9]{0,11}", str(value or "")) else ""


def _clock(cutoff):
    value = cutoff or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("历史样本截止时间必须带时区")
    return value


def _official_row(raw, source_id):
    row = _recent_row(raw, source_id)
    # A uniform ID is never asserted to be a Sporttery ID. Use it only as an
    # independent training entity when an official numeric Sporttery ID is absent.
    for side in ("home", "away"):
        if not row[f"{side}_team_id"]:
            uniform = _numeric(raw.get(f"uniform{side.title()}TeamId"))
            if uniform:
                row[f"{side}_team_id"] = f"uniform:{uniform}"
    row["team_identity_basis"] = "sporttery_id_else_separate_uniform_namespace"
    row["sporttery_match_id"] = _numeric(raw.get("sportteryMatchId"))
    return row


def _signature(row):
    return tuple(row.get(k) for k in ("match_id", "match_date", "home_team_id", "away_team_id",
                                     "home_goals_90", "away_goals_90", "competition_id", "season_id"))


def _select(rows, competition_id, cutoff):
    excluded, candidates = Counter(), defaultdict(list)
    for row in rows:
        try:
            if row.get("competition_id") != competition_id:
                excluded["other_competition"] += 1
                continue
            day = date.fromisoformat(row["match_date"])
            if not START <= day < cutoff.astimezone(CHINA).date():
                excluded["outside_2025_to_cutoff_or_same_day"] += 1
                continue
            if (row.get("status") != "completed_90" or row.get("score_basis") != "90_minutes"
                    or row.get("is_friendly") is not False):
                excluded["unverified_90_or_friendly"] += 1
                continue
            if (not row.get("match_id") or not row.get("home_team_id") or not row.get("away_team_id")
                    or row["home_team_id"] == row["away_team_id"] or not row.get("season_id")):
                excluded["incomplete_identity"] += 1
                continue
            if not all(type(row.get(k)) is int and 0 <= row[k] <= 99 for k in ("home_goals_90", "away_goals_90")):
                excluded["invalid_score"] += 1
                continue
            candidates[row["match_id"]].append(row)
        except (ValueError, KeyError, TypeError):
            excluded["malformed"] += 1
    accepted = []
    for versions in candidates.values():
        if len({_signature(r) for r in versions}) != 1:
            excluded["conflicting_match"] += len(versions)
        else:
            accepted.append(versions[0])
            excluded["duplicate_observation"] += len(versions) - 1
    return sorted(accepted, key=lambda r: (r["match_date"], r["match_id"])), dict(excluded)


def _read_receipt(path, cutoff):
    record = json.loads(path.read_text(encoding="utf8"))
    source = record["source"]
    stamp = datetime.fromisoformat(source["fetched_at"])
    if (stamp.tzinfo is None or stamp > cutoff or digest(record["payload"]) != source["sha256"]
            or not validate_redirect_chain([source["url"]])):
        raise ValueError("历史来源校验失败")
    if source.get("kind") not in {"recent", "training", "historical_training"}:
        return None
    return record


def _fetch_official(match_id, receipts_dir):
    if not _numeric(match_id):
        raise ValueError("不是体彩比赛ID")
    url = ROOT + "getMatchResultV1.qry?" + urllib.parse.urlencode({
        "sportteryMatchId": match_id, "termLimits": 100, "tournamentFlag": 1, "homeAwayFlag": 0})
    redirect = _OfficialRedirectHandler()
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.sporttery.cn/"})
    with urllib.request.build_opener(redirect).open(request, timeout=12) as response:
        if not validate_redirect_chain([url, *redirect.chain, response.geturl()]):
            raise ValueError("历史来源离开官方域")
        body = response.read(4_000_001)
    if len(body) > 4_000_000:
        raise ValueError("历史来源响应过大")
    payload = json.loads(body)
    if payload.get("success") is not True or str(payload.get("errorCode")) != "0" or not isinstance(payload.get("value"), dict):
        raise ValueError("官方历史接口无有效数据")
    sha = digest(payload)
    source = {"source_id": "historical_training:" + sha[:24], "kind": "historical_training", "url": url,
              "sha256": sha, "fetched_at": datetime.now(timezone.utc).isoformat(),
              "hash_basis": "canonical_json_utf8_sorted_keys_compact", "parser_version": "history-2025-v1"}
    path = receipts_dir / (uuid.uuid4().hex + ".json")
    atomic_json_write(path, {"source": source, "payload": payload})
    return {"source": source, "payload": payload}, path


def _cache_load(folder, cutoff):
    pointer = folder / "latest.json"
    if not pointer.exists():
        return None
    index = json.loads(pointer.read_text(encoding="utf8"))
    name = index["version"]
    if not re.fullmatch(r"[0-9a-f]{32}", name):
        raise ValueError("无效历史缓存版本")
    doc = json.loads((folder / "versions" / f"{name}.json").read_text(encoding="utf8"))
    if digest(doc) != index["sha256"]:
        raise ValueError("历史缓存完整性校验失败，须显式重建")
    stamp = datetime.fromisoformat(doc["built_at"])
    if stamp > cutoff:
        raise ValueError("缓存构建时间晚于预测截止时间")
    for source in doc["sources"]:
        path = Path(source["receipt_path"])
        if source.get("provider") in {"football_data", "manifest_source"}:
            if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("第三方原始历史缓存校验失败")
        else:
            record = _read_receipt(path, cutoff)
            if record is None or record["source"]["source_id"] != source["source_id"]:
                raise ValueError("官方原始历史缓存校验失败")
    return {**doc, "cache_hit": True}


def _save(folder, document):
    document = {**document, "version": uuid.uuid4().hex}
    atomic_json_write(folder / "versions" / f"{document['version']}.json", document)
    atomic_json_write(folder / "latest.json", {"version": document["version"], "sha256": digest(document)})
    return {**document, "cache_hit": False}


def ensure_historical_samples(scope, *, evidence_dir: Path, bundles=(), rebuild=False,
                              allow_download=False, cutoff=None, max_requests=24):
    """Return one immutable official competition cache; no refresh on cache hit.

    Sources are parsed again from immutable receipts, not trusted bundle rows.
    Missing/failed initial collections are cached too; retry requires rebuild=True.
    """
    strict_cutoff = cutoff is not None
    cutoff = _clock(cutoff)
    cid = _numeric(scope.get("competition_id"))
    if not cid:
        raise ValueError("未确认官方赛事ID")
    folder = evidence_dir.parent / "historical_samples" / f"sporttery-{cid}"
    with _LOCK:
        if not strict_cutoff:
            cutoff = datetime.now(timezone.utc)
        if not rebuild:
            cached = _cache_load(folder, cutoff)
            if cached is not None:
                return cached
        if strict_cutoff and cutoff < datetime.now(timezone.utc):
            raise ValueError("历史截止时间只允许读取当时已构建缓存，不能事后采集回填")
        records, sources, raw_rows, errors, queue, visited = [], {}, [], [], deque(), set()
        for bundle in bundles:
            if bundle.get("identity_verified") and bundle.get("scope", {}).get("competition_id") == cid and _numeric(bundle.get("match_id")):
                queue.append(str(bundle["match_id"]))
        for directory in (evidence_dir / "sources", folder / "sources"):
            for path in directory.glob("*.json"):
                try:
                    record = _read_receipt(path, cutoff)
                    if record:
                        records.append((record, path))
                except (OSError, ValueError, KeyError, TypeError):
                    errors.append(f"来源校验失败:{path.name}")

        def consume(record, path):
            source = record["source"]
            relevant = False
            for side in ("home", "away"):
                for raw in (record["payload"]["value"].get(side) or {}).get("matchList", []):
                    if str(raw.get("tournamentId")) != cid:
                        continue
                    try:
                        row = _official_row(raw, source["source_id"])
                        raw_rows.append(row)
                        relevant = True
                        if START <= date.fromisoformat(row["match_date"]) < cutoff.astimezone(CHINA).date() and row["sporttery_match_id"]:
                            queue.append(row["sporttery_match_id"])
                    except (ValueError, TypeError, KeyError):
                        errors.append("原始行缺少日期或90分钟比分")
            if relevant:
                sources[source["source_id"]] = {**source, "receipt_path": str(path.resolve())}

        for record, path in records:
            consume(record, path)
        requests = 0
        while allow_download and queue and requests < max_requests:
            mid = queue.popleft()
            if mid in visited:
                continue
            visited.add(mid)
            requests += 1
            try:
                record, path = _fetch_official(mid, folder / "sources")
                if strict_cutoff and datetime.fromisoformat(record["source"]["fetched_at"]) > cutoff:
                    raise ValueError("新获取来源晚于指定历史截止时间，不可作当时已知证据")
                consume(record, path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"体彩比赛{mid}历史采集失败:{type(exc).__name__}")
        rows, excluded = _select(raw_rows, cid, cutoff)
        used = {r["source_id"] for r in rows}
        return _save(folder, {"schema_version": 1, "provider": "sporttery", "scope": {"competition_id": cid},
            "built_at": datetime.now(timezone.utc).isoformat(), "requested_start": START.isoformat(),
            "requested_end": cutoff.astimezone(CHINA).date().isoformat(), "rows": rows, "rows_sha256": digest(rows),
            "sources": [s for sid, s in sources.items() if sid in used], "excluded": excluded,
            "source_errors": errors, "network_requests": requests, "historical_market_rows": [],
            "coverage": {"status": "partial" if rows else "unavailable", "complete": False,
                         "match_count": len(rows), "first_match": rows[0]["match_date"] if rows else None,
                         "last_match": rows[-1]["match_date"] if rows else None,
                         "seasons": sorted({r["season_id"] for r in rows}),
                         "request_limit_reached": bool(queue and requests >= max_requests),
                         "reason": "球队历史图有界扩展，未取得赛事全量赛程对账；同日只有日期的赛果排除"}})


def ensure_football_data_history(competition, *, runtime_dir: Path, rebuild=False, allow_download=False, cutoff=None):
    """Build a separate Football-Data name-domain cache, including closing odds.

    This does NOT resolve third-party names to official numeric team IDs.
    """
    strict_cutoff = cutoff is not None
    cutoff = _clock(cutoff)
    code = FOOTBALL_DATA_CODES.get(competition)
    if not code:
        raise ValueError("该赛事未配置Football-Data来源")
    folder = runtime_dir / "historical_samples" / f"football-data-{code}"
    with _LOCK:
        if not strict_cutoff:
            cutoff = datetime.now(timezone.utc)
        if not rebuild:
            cached = _cache_load(folder, cutoff)
            if cached is not None:
                return cached
        if strict_cutoff and cutoff < datetime.now(timezone.utc):
            raise ValueError("历史截止时间只允许读取当时已构建缓存，不能事后采集回填")
        from .datasets import _download
        rows, markets, sources, errors = [], [], [], []
        seasons = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2024, cutoff.year + 1)]
        for season in seasons:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            try:
                known = sorted((folder / "sources").glob(f"{season}-*.csv"))
                body, downloaded = None, False
                if allow_download:
                    try:
                        body = _download(url)
                        if strict_cutoff and datetime.now(timezone.utc) > cutoff:
                            raise ValueError("新获取来源晚于历史截止时间")
                        downloaded = True
                    except (OSError, ValueError) as exc:
                        body = None
                        errors.append(f"{season}:下载失败:{type(exc).__name__}")
                if body is None and known:
                    body = known[-1].read_bytes()
                if body is None:
                    # Existing verified source files can seed the build without network.
                    manifest_path = runtime_dir / "imports" / "season-2025-2026-manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf8")) if manifest_path.exists() else {}
                    for item in manifest.get("datasets", []):
                        if item.get("url") == url:
                            candidate = (runtime_dir / item["raw_path"]).read_bytes()
                            if hashlib.sha256(candidate).hexdigest() == item["source_sha256"]:
                                body = candidate
                                break
                if body is None:
                    errors.append(f"{season}:未下载且无可信本地原始缓存")
                    continue
                sha = hashlib.sha256(body).hexdigest()
                path = folder / "sources" / f"{season}-{sha}.csv"
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(body)
                sid = f"football_data:{sha[:24]}"
                source = {"source_id": sid, "provider": "football_data", "url": url, "sha256": sha,
                          "receipt_path": str(path.resolve()),
                          "fetched_at": datetime.now(timezone.utc).isoformat() if downloaded else None,
                          "cache_imported_at": cutoff.isoformat()}
                sources.append(source)
                parsed = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
                if not parsed or not {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}.issubset(parsed[0]):
                    raise ValueError("CSV字段无效")
                for raw in parsed:
                    if raw.get("FTR") not in {"H", "D", "A"}:
                        continue
                    day = next((datetime.strptime(raw["Date"], fmt).date() for fmt in ("%d/%m/%Y", "%d/%m/%y")
                                if re.fullmatch(r"\d{2}/\d{2}/" + (r"\d{4}" if fmt.endswith("%Y") else r"\d{2}"), raw["Date"])), None)
                    if day is None or not START <= day < cutoff.astimezone(CHINA).date():
                        continue
                    home, away = raw["HomeTeam"].strip(), raw["AwayTeam"].strip()
                    mid = "fd:" + digest([code, day.isoformat(), home, away])[:24]
                    row = {"match_id": mid, "match_date": day.isoformat(), "home_team_id": f"fd:{code}:{home}",
                           "away_team_id": f"fd:{code}:{away}", "home_team": home, "away_team": away,
                           "home_goals_90": int(raw["FTHG"]), "away_goals_90": int(raw["FTAG"]),
                           "competition": competition, "competition_id": f"fd:{code}", "season_id": season,
                           "status": "completed_90", "score_basis": "90_minutes", "is_friendly": False, "source_id": sid}
                    if raw["FTR"] != ("H" if row["home_goals_90"] > row["away_goals_90"] else "A" if row["home_goals_90"] < row["away_goals_90"] else "D"):
                        raise ValueError("赛果与90分钟比分冲突")
                    rows.append(row)
                    for prefix in ("B365C", "PC"):
                        fields = [prefix + k for k in ("H", "D", "A")]
                        try:
                            prices = [float(raw[k]) for k in fields]
                            if all(1 < v < 1000 for v in prices):
                                markets.append({**row, "odds": dict(zip(("home", "draw", "away"), prices)),
                                    "odds_fields": fields, "odds_type": "closing_1x2", "closing_explicit": True,
                                    "closing_verified": True, "source_verified": True,
                                    "actual_result": {"H": "home", "D": "draw", "A": "away"}[raw["FTR"]],
                                    "observed_at": None, "kickoff_time": None, "time_precision": "date",
                                    "source_url": url, "source_sha256": sha,
                                    "odds_timestamp": None, "odds_timestamp_status": "provider_closing_label_no_exact_timestamp",
                                    "official_sporttery": False})
                                break
                        except (ValueError, TypeError, KeyError):
                            pass
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{season}:{type(exc).__name__}:{str(exc)[:120]}")
        rows, excluded = _select(rows, f"fd:{code}", cutoff)
        accepted = {r["match_id"] for r in rows}
        markets = list({m["match_id"]: m for m in markets if m["match_id"] in accepted}.values())
        return _save(folder, {"schema_version": 1, "provider": "football_data", "competition": competition,
            "scope": {"competition_id": f"fd:{code}"}, "built_at": datetime.now(timezone.utc).isoformat(),
            "requested_start": START.isoformat(), "requested_end": cutoff.astimezone(CHINA).date().isoformat(),
            "rows": rows, "rows_sha256": digest(rows), "historical_market_rows": markets,
            "sources": sources, "source_errors": errors, "excluded": excluded,
            "coverage": {"status": "partial" if rows else "unavailable", "complete": False, "match_count": len(rows),
                         "first_match": rows[0]["match_date"] if rows else None, "last_match": rows[-1]["match_date"] if rows else None,
                         "seasons": sorted({r["season_id"] for r in rows}),
                         "reason": "来源实际返回的完赛行；未与官方全量赛程对账，不宣称截至今日无遗漏"}})


def ensure_manifest_history(competition, *, runtime_dir: Path, rebuild=False, allow_download=False, cutoff=None):
    """Reuse hash-verified existing raw datasets, expanding the 2025 time window.

    UCL qualification is a different scope and is never mixed with main draw.
    J1 2026 special stages retain stage labels; no false normal-season label.
    """
    from dataclasses import replace
    from .datasets import DatasetSpec, SEASON_DATASETS, PARSERS, _download
    if competition not in {"欧冠", "日职", "世界杯"}:
        raise ValueError("无已确认的赛事历史来源适配器")
    strict_cutoff = cutoff is not None
    cutoff = _clock(cutoff)
    key = {"欧冠": "ucl", "日职": "j1", "世界杯": "worldcup"}[competition]
    folder = runtime_dir / "historical_samples" / f"manifest-{key}"
    with _LOCK:
        if not strict_cutoff:
            cutoff = datetime.now(timezone.utc)
        if not rebuild:
            cached = _cache_load(folder, cutoff)
            if cached is not None:
                return cached
        if strict_cutoff and cutoff < datetime.now(timezone.utc):
            raise ValueError("历史截止时间只允许读取当时已构建缓存，不能事后采集回填")
        manifest_path = runtime_dir / "imports" / "season-2025-2026-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf8")) if manifest_path.exists() else {}
        entries = {d["url"]: d for d in manifest.get("datasets", [])}
        specs = [s for s in SEASON_DATASETS if (s.family or s.competition) == competition and s.phase != "qualifying"]
        if competition == "欧冠":
            specs.insert(0, DatasetSpec("ucl-main-2024-2025", "欧冠", "2024/25",
                "https://fixturedownload.com/feed/json/champions-league-2024", "fixture_download", 189,
                "FixtureDownload；与OpenFootball加时标记作保守排除", phase="main", family="欧冠"))
            specs.append(replace(specs[-1], key="ucl-main-2026-2027", season="2026/27", expected_raw_matches=0,
                                 url="https://fixturedownload.com/feed/json/champions-league-2026"))
        if competition == "日职":
            specs.append(DatasetSpec("j1-2026-2027", "日职", "2026/27",
                "https://raw.githubusercontent.com/mokekuma-git/JLeague_Matches-Bar_Graph/main/docs/csv/26-27_allmatch_result-J1.csv",
                "jleague", 0, "CC-BY-4.0；仓库公开赛果", phase="regular", family="日职"))
        rows, sources, errors, parser_exclusions, partitions = [], [], [], [], []

        def acquire(url, source_key, suffix):
            body, downloaded = None, False
            if allow_download:
                try:
                    body = _download(url)
                    if strict_cutoff and datetime.now(timezone.utc) > cutoff:
                        raise ValueError("新获取来源晚于历史截止时间")
                    downloaded = True
                except (OSError, ValueError) as exc:
                    body = None
                    errors.append(f"{source_key}:下载失败:{type(exc).__name__}")
            if body is None:
                known = sorted((folder / "sources").glob(f"{source_key}-*{suffix}"))
                if known:
                    body = known[-1].read_bytes()
                elif url in entries:
                    item = entries[url]
                    candidate = (runtime_dir / item["raw_path"]).read_bytes()
                    if hashlib.sha256(candidate).hexdigest() != item["source_sha256"]:
                        raise ValueError("原manifest来源哈希不符")
                    body = candidate
            if body is None:
                raise ValueError("未取得该分区原始来源")
            sha = hashlib.sha256(body).hexdigest()
            path = folder / "sources" / f"{source_key}-{sha}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(body)
            source = {"provider": "manifest_source", "source_id": "manifest:" + sha[:24], "url": url,
                      "sha256": sha, "receipt_path": str(path.resolve()),
                      "fetched_at": datetime.now(timezone.utc).isoformat() if downloaded else None,
                      "cache_imported_at": cutoff.isoformat()}
            sources.append(source)
            return body, source

        for spec in specs:
            try:
                body, source = acquire(spec.url, spec.key, ".csv" if spec.adapter == "jleague" else ".json")
                matches, raw_count, exclusions = PARSERS[spec.adapter](spec, body)
                blocked_dates = set()
                if spec.key == "ucl-main-2024-2025":
                    # FixtureDownload has no separate regulation-time field.
                    # Reject every match on an extra-time date rather than
                    # guessing a cross-provider team-name correspondence.
                    text_body, checker = acquire(
                        "https://raw.githubusercontent.com/openfootball/champions-league/master/2024-25/cl.txt",
                        "ucl-2024-25-extra-time-check", ".txt")
                    current, year = None, 2024
                    for line in text_body.decode("utf8").splitlines():
                        line = line.strip()
                        match = re.match(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2})(?: (\d{4}))?$", line)
                        if match:
                            year = int(match[3] or year)
                            current = datetime.strptime(f"{match[1]} {match[2]} {year}", "%b %d %Y").date()
                        if "a.e.t." in line or "pen." in line:
                            if current is None:
                                raise ValueError("加时日期无法解析")
                            blocked_dates.add(current)
                accepted = 0
                for match in matches:
                    if match.kickoff_date in blocked_dates:
                        exclusions.append(f"{match.kickoff_date}:该日存在加时赛，无法精确跨源映射，保守排除")
                        continue
                    if not START <= match.kickoff_date < cutoff.astimezone(CHINA).date():
                        continue
                    prefix = f"ext:{key}:"
                    rows.append({"match_id": prefix + digest([match.kickoff_date.isoformat(), match.home_team, match.away_team])[:24],
                        "match_date": match.kickoff_date.isoformat(), "home_team_id": prefix + match.home_team,
                        "away_team_id": prefix + match.away_team, "home_team": match.home_team, "away_team": match.away_team,
                        "home_goals_90": match.home_goals, "away_goals_90": match.away_goals,
                        "competition": competition, "competition_id": f"ext:{key}", "season_id": spec.season,
                        "phase": spec.phase, "neutral": spec.neutral, "status": "completed_90",
                        "score_basis": "90_minutes", "is_friendly": False, "source_id": source["source_id"]})
                    accepted += 1
                parser_exclusions.extend(f"{spec.key}:{e}" for e in exclusions)
                partitions.append({"key": spec.key, "season": spec.season, "phase": spec.phase,
                                   "raw_matches": raw_count, "accepted": accepted, "excluded": len(exclusions)})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{spec.key}:{type(exc).__name__}:{str(exc)[:120]}")
        rows, excluded = _select(rows, f"ext:{key}", cutoff)
        return _save(folder, {"schema_version": 1, "provider": "manifest", "competition": competition,
            "scope": {"competition_id": f"ext:{key}"}, "built_at": datetime.now(timezone.utc).isoformat(),
            "requested_start": START.isoformat(), "requested_end": cutoff.astimezone(CHINA).date().isoformat(),
            "rows": rows, "rows_sha256": digest(rows), "sources": sources, "source_errors": errors,
            "historical_market_rows": [], "excluded": excluded, "parser_exclusions": parser_exclusions,
            "partitions": partitions, "coverage": {"status": "partial" if rows else "unavailable", "complete": False,
                "match_count": len(rows), "first_match": rows[0]["match_date"] if rows else None,
                "last_match": rows[-1]["match_date"] if rows else None, "seasons": sorted({r["season_id"] for r in rows}),
                "reason": "世界杯本赛2025无届次；欧冠资格赛独立不混入；日职2026特殊分区保留标签。实际来源部分覆盖，未与官方全量对账"}})

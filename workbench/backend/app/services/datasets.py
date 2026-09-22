from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .history import atomic_json_write
from .model import GoalModel, TrainingMatch, export_training_csv


ALLOWED_SOURCE_HOSTS = {
    "www.football-data.co.uk",
    "football-data.co.uk",
    "fixturedownload.com",
    "raw.githubusercontent.com",
}


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    competition: str
    season: str
    url: str
    adapter: str
    expected_raw_matches: int
    license_note: str
    neutral: bool = False
    phase: str = "regular"
    family: str | None = None


SEASON_DATASETS = (
    DatasetSpec("epl-2025-2026", "英超", "2025/26", "https://www.football-data.co.uk/mmz4281/2526/E0.csv", "football_data", 380, "Football-Data 免费数据；重要用途需复核站方条款"),
    DatasetSpec("league-one-2025-2026", "英甲", "2025/26", "https://www.football-data.co.uk/mmz4281/2526/E2.csv", "football_data", 552, "Football-Data 免费数据；重要用途需复核站方条款"),
    DatasetSpec("bundesliga-2025-2026", "德甲", "2025/26", "https://www.football-data.co.uk/mmz4281/2526/D1.csv", "football_data", 306, "Football-Data 免费数据；重要用途需复核站方条款"),
    DatasetSpec("bundesliga2-2025-2026", "德乙", "2025/26", "https://www.football-data.co.uk/mmz4281/2526/D2.csv", "football_data", 306, "Football-Data 免费数据；重要用途需复核站方条款"),
    DatasetSpec("serie-a-2025-2026", "意甲", "2025/26", "https://www.football-data.co.uk/mmz4281/2526/I1.csv", "football_data", 380, "Football-Data 免费数据；重要用途需复核站方条款"),
    DatasetSpec("ucl-main-2025-2026", "欧冠", "2025/26", "https://fixturedownload.com/feed/json/champions-league-2025", "fixture_download", 189, "FixtureDownload 每日更新公开 feed；联赛阶段及淘汰赛", phase="main", family="欧冠"),
    DatasetSpec("ucl-qualifying-2025-2026", "欧冠资格赛", "2025/26", "https://raw.githubusercontent.com/openfootball/champions-league/master/2025-26/clq.txt", "openfootball_txt", 92, "OpenFootball CC0-1.0；欧冠资格赛", phase="qualifying", family="欧冠"),
    DatasetSpec("j1-2025", "日职", "2025", "https://raw.githubusercontent.com/mokekuma-git/JLeague_Matches-Bar_Graph/main/docs/csv/2025_allmatch_result-J1.csv", "jleague", 380, "CC-BY-4.0；来源仓库声明整理自 J.League 公开赛果"),
    DatasetSpec("j1-east-2026", "日职东区", "2026", "https://raw.githubusercontent.com/mokekuma-git/JLeague_Matches-Bar_Graph/main/docs/csv/2026East_allmatch_result-J1.csv", "jleague", 90, "CC-BY-4.0；2026 J1 特殊赛制东区", phase="east", family="日职"),
    DatasetSpec("j1-west-2026", "日职西区", "2026", "https://raw.githubusercontent.com/mokekuma-git/JLeague_Matches-Bar_Graph/main/docs/csv/2026West_allmatch_result-J1.csv", "jleague", 90, "CC-BY-4.0；2026 J1 特殊赛制西区", phase="west", family="日职"),
    DatasetSpec("j1-playoff-2026", "日职季后赛", "2026", "https://raw.githubusercontent.com/mokekuma-git/JLeague_Matches-Bar_Graph/main/docs/csv/2026_allmatch_result-J1PO.csv", "jleague", 20, "CC-BY-4.0；2026 J1 排名决定赛", phase="playoff", family="日职"),
    DatasetSpec("world-cup-2026", "世界杯", "2026", "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json", "openfootball_worldcup", 104, "OpenFootball 公共领域/CC0 风格开放数据", neutral=True, phase="tournament"),
)


def _download(url: str, *, timeout: int = 30, max_bytes: int = 5_000_000) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_SOURCE_HOSTS:
        raise ValueError("训练数据来源不在允许的 HTTPS 主机列表")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SportteryLocalWorkbench/0.2 (+local research dataset sync)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or (final.hostname or "").lower() not in ALLOWED_SOURCE_HOSTS:
            raise ValueError("训练数据重定向离开允许主机")
        body = response.read(max_bytes + 1)
    if not body or len(body) > max_bytes:
        raise ValueError("训练数据响应为空或超过大小上限")
    return body


def _match_id(competition: str, match_date: str, home: str, away: str) -> str:
    return hashlib.sha256(f"{competition}|{match_date}|{home}|{away}".encode("utf-8")).hexdigest()[:24]


def _training_match(
    spec: DatasetSpec,
    match_date: str,
    home: str,
    away: str,
    home_goals: Any,
    away_goals: Any,
    *,
    neutral: bool | None = None,
) -> TrainingMatch:
    parsed = datetime.strptime(match_date, "%Y-%m-%d").date()
    home = str(home).strip()
    away = str(away).strip()
    if not home or not away or home == away:
        raise ValueError("球队字段无效")
    return TrainingMatch(
        match_id=_match_id(spec.competition, parsed.isoformat(), home, away),
        competition=spec.competition,
        kickoff_date=parsed,
        home_team=home,
        away_team=away,
        home_goals=int(home_goals),
        away_goals=int(away_goals),
        neutral=spec.neutral if neutral is None else neutral,
        source=spec.url,
    )


def _parse_football_data(spec: DatasetSpec, body: bytes) -> tuple[list[TrainingMatch], int, list[str]]:
    text = body.decode("utf-8-sig", errors="strict")
    rows = list(csv.DictReader(io.StringIO(text)))
    required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Football-Data 字段结构不符合预期")
    matches: list[TrainingMatch] = []
    excluded: list[str] = []
    for index, row in enumerate(rows, 2):
        if row.get("FTR") not in {"H", "D", "A"}:
            excluded.append(f"row:{index}:未完赛或赛果无效")
            continue
        parsed = None
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                parsed = datetime.strptime(row["Date"], fmt).date().isoformat()
                break
            except ValueError:
                pass
        if parsed is None:
            raise ValueError(f"Football-Data 日期无法解析: row {index}")
        matches.append(_training_match(spec, parsed, row["HomeTeam"], row["AwayTeam"], row["FTHG"], row["FTAG"]))
    return matches, len(rows), excluded


def _parse_fixture_download(spec: DatasetSpec, body: bytes) -> tuple[list[TrainingMatch], int, list[str]]:
    rows = json.loads(body.decode("utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("FixtureDownload 响应不是比赛数组")
    matches: list[TrainingMatch] = []
    excluded: list[str] = []
    # OpenFootball 同赛季记录明确标出以下比赛进入加时/点球；feed 未提供90分钟拆分。
    ambiguous_90 = {
        ("Juventus", "Galatasaray"),
        ("Sporting CP", "Bodø/Glimt"),
        ("Paris", "Arsenal"),
    }
    for row in rows:
        home, away = row.get("HomeTeam"), row.get("AwayTeam")
        if row.get("HomeTeamScore") is None or row.get("AwayTeamScore") is None:
            excluded.append(f"match:{row.get('MatchNumber')}:未完赛")
            continue
        if (home, away) in ambiguous_90:
            excluded.append(f"match:{row.get('MatchNumber')}:加时或点球，feed未拆分90分钟")
            continue
        raw_date = str(row.get("DateUtc", ""))[:10]
        matches.append(_training_match(spec, raw_date, home, away, row["HomeTeamScore"], row["AwayTeamScore"]))
    return matches, len(rows), excluded


def _parse_jleague(spec: DatasetSpec, body: bytes) -> tuple[list[TrainingMatch], int, list[str]]:
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    required = {"match_date", "home_team", "home_goal", "away_goal", "away_team", "status"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("J1 CSV字段结构不符合预期")
    matches: list[TrainingMatch] = []
    excluded: list[str] = []
    for index, row in enumerate(rows, 2):
        if not str(row.get("status", "")).startswith("試合終了"):
            excluded.append(f"row:{index}:非完赛")
            continue
        parsed = datetime.strptime(row["match_date"], "%Y/%m/%d").date().isoformat()
        matches.append(_training_match(spec, parsed, row["home_team"], row["away_team"], row["home_goal"], row["away_goal"]))
    return matches, len(rows), excluded


def _parse_openfootball_txt(spec: DatasetSpec, body: bytes) -> tuple[list[TrainingMatch], int, list[str]]:
    text = body.decode("utf-8-sig")
    current_date: str | None = None
    year = 2025
    matches: list[TrainingMatch] = []
    excluded: list[str] = []
    raw_count = 0
    date_pattern = re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2})(?: (\d{4}))?$")
    match_pattern = re.compile(r"^(?:\d{2}:\d{2}\s+)?(.+?)\s+v\s+(.+?)\s+(\d+)-(\d+)(?:\s+\(.*\))?$")
    country_suffix = re.compile(r"\s+\([A-Z]{3}\)$")
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        date_match = date_pattern.match(line)
        if date_match:
            if date_match.group(3):
                year = int(date_match.group(3))
            current_date = datetime.strptime(
                f"{date_match.group(1)} {date_match.group(2)} {year}", "%b %d %Y"
            ).date().isoformat()
            continue
        if " v " not in line or not re.search(r"\d+-\d+", line):
            continue
        raw_count += 1
        if "a.e.t." in line or "pen." in line:
            excluded.append(f"line:{line_number}:加时或点球，90分钟比分未独立结构化")
            continue
        parsed = match_pattern.match(line)
        if parsed is None or current_date is None:
            excluded.append(f"line:{line_number}:格式无法可靠解析")
            continue
        home = country_suffix.sub("", parsed.group(1)).strip()
        away = country_suffix.sub("", parsed.group(2)).strip()
        matches.append(_training_match(spec, current_date, home, away, parsed.group(3), parsed.group(4)))
    return matches, raw_count, excluded


def _parse_openfootball_worldcup(spec: DatasetSpec, body: bytes) -> tuple[list[TrainingMatch], int, list[str]]:
    document = json.loads(body.decode("utf-8-sig"))
    rows = document.get("matches") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("世界杯 JSON 缺少 matches")
    matches: list[TrainingMatch] = []
    excluded: list[str] = []
    for index, row in enumerate(rows, 1):
        score = row.get("score") or {}
        full_time = score.get("ft")
        if not isinstance(full_time, list) or len(full_time) != 2:
            excluded.append(f"match:{index}:缺少90分钟ft")
            continue
        matches.append(_training_match(spec, row["date"], row["team1"], row["team2"], full_time[0], full_time[1], neutral=True))
    return matches, len(rows), excluded


PARSERS: dict[str, Callable[[DatasetSpec, bytes], tuple[list[TrainingMatch], int, list[str]]]] = {
    "football_data": _parse_football_data,
    "fixture_download": _parse_fixture_download,
    "jleague": _parse_jleague,
    "openfootball_worldcup": _parse_openfootball_worldcup,
    "openfootball_txt": _parse_openfootball_txt,
}


def _validate_matches(spec: DatasetSpec, matches: list[TrainingMatch]) -> None:
    ids = [match.match_id for match in matches]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{spec.competition} 出现重复比赛")
    if any(match.competition != spec.competition for match in matches):
        raise ValueError("赛事标签串联")
    if any(min(match.home_goals, match.away_goals) < 0 for match in matches):
        raise ValueError("出现负进球数")
    if len(matches) < 8:
        raise ValueError(f"{spec.competition} 可用90分钟样本不足")


def sync_season_datasets(runtime_dir: Path, *, specs: tuple[DatasetSpec, ...] = SEASON_DATASETS) -> dict[str, Any]:
    root = runtime_dir / "imports"
    raw_dir = root / "raw" / "2025-2026"
    clean_dir = root / "clean" / "2025-2026"
    raw_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    total = 0
    for spec in specs:
        source_status = "downloaded"
        try:
            body = _download(spec.url)
        except OSError:
            cached = sorted(raw_dir.glob(f"{spec.key}-*"), key=lambda path: path.stat().st_mtime, reverse=True)
            if not cached:
                raise
            body = cached[0].read_bytes()
            source_status = "cached_after_network_error"
        digest = hashlib.sha256(body).hexdigest()
        suffix = ".json" if spec.adapter in {"fixture_download", "openfootball_worldcup"} else ".txt" if spec.adapter == "openfootball_txt" else ".csv"
        raw_path = raw_dir / f"{spec.key}-{digest[:12]}{suffix}"
        if not raw_path.exists():
            raw_path.write_bytes(body)
        matches, raw_count, excluded = PARSERS[spec.adapter](spec, body)
        if raw_count != spec.expected_raw_matches:
            raise ValueError(f"{spec.competition} 原始场数异常: {raw_count} != {spec.expected_raw_matches}")
        _validate_matches(spec, matches)
        clean_path = clean_dir / f"{spec.key}-{digest[:12]}-normalized.csv"
        if not clean_path.exists():
            export_training_csv(matches, clean_path)
        teams = {team for match in matches for team in (match.home_team, match.away_team)}
        item = {
            **asdict(spec),
            "family": spec.family or spec.competition,
            "source_sha256": digest,
            "source_status": source_status,
            "raw_bytes": len(body),
            "raw_match_count": raw_count,
            "training_match_count": len(matches),
            "excluded_count": len(excluded),
            "exclusions": excluded,
            "team_count": len(teams),
            "date_start": min(match.kickoff_date for match in matches).isoformat(),
            "date_end": max(match.kickoff_date for match in matches).isoformat(),
            "raw_path": str(raw_path.relative_to(runtime_dir)).replace("\\", "/"),
            "clean_path": str(clean_path.relative_to(runtime_dir)).replace("\\", "/"),
            "complete_raw_file": raw_count == spec.expected_raw_matches,
            "verified_90_minute_training_only": True,
        }
        items.append(item)
        total += len(matches)
    from .model import import_training_csv

    j1_items = [item for item in items if item["family"] == "日职"]
    j1_matches: list[TrainingMatch] = []
    for item in j1_items:
        for match in import_training_csv(runtime_dir / item["clean_path"]):
            j1_matches.append(TrainingMatch(**{**match.__dict__, "competition": "日职"}))
    aggregate_digest = hashlib.sha256(json.dumps([
        [match.match_id, match.kickoff_date.isoformat(), match.home_goals, match.away_goals]
        for match in j1_matches
    ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    aggregate_path = clean_dir / f"j1-2025-2026-combined-{aggregate_digest[:12]}-normalized.csv"
    if not aggregate_path.exists():
        export_training_csv(j1_matches, aggregate_path)
    model_groups = [{
        "key": "j1-2025-2026-combined",
        "competition": "日职",
        "source_dataset_keys": [item["key"] for item in j1_items],
        "training_match_count": len(j1_matches),
        "team_count": len({team for match in j1_matches for team in (match.home_team, match.away_team)}),
        "date_start": min(match.kickoff_date for match in j1_matches).isoformat(),
        "date_end": max(match.kickoff_date for match in j1_matches).isoformat(),
        "clean_path": str(aggregate_path.relative_to(runtime_dir)).replace("\\", "/"),
    }]
    manifest = {
        "schema_version": 1,
        "scope": "2025/26赛季；日职含2025自然年及2026特殊分区/排名赛；世界杯按2026届",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_match_count": total,
        "dataset_count": len(items),
        "competition_family_count": len({item["family"] for item in items}),
        "policy": "原始文件完整保存；仅字段明确为90分钟且已完赛的记录进入训练；各赛事独立建模",
        "datasets": items,
        "model_groups": model_groups,
    }
    atomic_json_write(root / "season-2025-2026-manifest.json", manifest)
    return manifest


def train_manifest_models(runtime_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    from .model import import_training_csv

    results: list[dict[str, Any]] = []
    model_dir = runtime_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    training_items = [
        item for item in manifest.get("datasets", [])
        if item.get("family", item["competition"]) == item["competition"]
    ] + list(manifest.get("model_groups", []))
    for item in training_items:
        matches = import_training_csv(runtime_dir / item["clean_path"])
        for model_type, suffix in (("poisson", "poisson"), ("dixon_coles", "dc")):
            model = GoalModel.fit(matches, competition=item["competition"], model_type=model_type)
            name = f"{item['key']}-{suffix}-{model.training_data_sha256[:12]}-v1"
            output = model_dir / f"{name}.json"
            if output.exists():
                model = GoalModel.load(output)
                status = "reused"
            else:
                model.save(output)
                status = "trained"
            results.append({"name": name, "competition": item["competition"], "model_type": model_type, "matches": model.validation["matches"], "teams": model.validation["teams"], "training_cutoff": model.training_cutoff.isoformat(), "status": status})
    return results


def load_dataset_manifest(runtime_dir: Path) -> dict[str, Any]:
    path = runtime_dir / "imports" / "season-2025-2026-manifest.json"
    if not path.exists():
        return {"dataset_count": 0, "training_match_count": 0, "datasets": [], "status": "not_synced"}
    return json.loads(path.read_text(encoding="utf-8"))

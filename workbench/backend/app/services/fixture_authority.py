"""Bind client fixture inputs to a locally saved official raw snapshot.

This validates integrity and equality, without making a network request or
changing the snapshot. Unbound manual inputs cannot assert official markets.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from ..schemas import Fixture, OfficialPlay
from .security import safe_child, validate_redirect_chain
from .snapshots import normalize_sporttery_slate


class FixtureAuthorityError(ValueError):
    """The request cannot establish the authority of its official fields."""


_OFFICIAL_FIELDS = (
    "match_id", "match_number", "business_date", "competition", "home_team",
    "away_team", "kickoff_time", "official_handicap", "result_play", "handicap_play",
)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FixtureAuthorityError("官方快照JSON含重复字段")
        result[key] = value
    return result


def _safe_urls(document):
    source = document.get("source_url")
    chain = document.get("redirect_chain")
    if not isinstance(source, str) or not isinstance(chain, list) or not all(isinstance(u, str) for u in chain):
        raise FixtureAuthorityError("官方快照来源或重定向记录缺失")
    urls = [source, *chain]
    try:
        for url in urls:
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 443)):
                raise FixtureAuthorityError("官方快照来源必须是无凭证的HTTPS官方地址")
        if not validate_redirect_chain(urls):
            raise FixtureAuthorityError("官方快照来源或重定向越出体彩官方域")
    except (ValueError, TypeError) as exc:
        if isinstance(exc, FixtureAuthorityError):
            raise
        raise FixtureAuthorityError("官方快照来源地址无效") from exc


def _load_authoritative(snapshot_id: str, *, runtime_dir: Path, requested_date: str):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", snapshot_id):
        raise FixtureAuthorityError("source_snapshot_id必须为64位SHA-256")
    sid = snapshot_id.lower()
    try:
        path = safe_child(runtime_dir / "snapshots", f"{sid}.json")
        document = json.loads(path.read_text(encoding="utf8"), object_pairs_hook=_strict_object)
        if not isinstance(document, dict) or str(document.get("snapshot_id", "")).lower() != sid:
            raise FixtureAuthorityError("官方快照记录ID与请求不一致")
        raw = document.get("raw")
        if not isinstance(raw, dict):
            raise FixtureAuthorityError("官方快照缺少原始JSON对象")
        # Exactly matches import_official_snapshot's existing writer. Its
        # default separators include spaces; no compact-hash migration here.
        raw_bytes = json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf8")
        if hashlib.sha256(raw_bytes).hexdigest() != sid:
            raise FixtureAuthorityError("官方快照原始数据SHA-256不符，拒绝预测")
        _safe_urls(document)
        if raw.get("success") is not True or str(raw.get("errorCode")) != "0":
            raise FixtureAuthorityError("快照不是成功的官方赛单响应")
        metadata = {
            "snapshot_id": sid, "source_url": document["source_url"],
            "source_updated_at": document.get("source_updated_at"),
            "fetched_at": document.get("fetched_at"),
            # Normalization does not use this field to establish authority.
            "observed_at": document.get("observed_at") or document.get("fetched_at"),
        }
        slate = normalize_sporttery_slate(raw, metadata, requested_date, date_basis="all")
        if not slate["complete_for_scope"]:
            raise FixtureAuthorityError("官方原始赛单完整性校验失败")
        return {row["match_id"]: Fixture.model_validate(row) for row in slate["fixtures"]}
    except FixtureAuthorityError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FixtureAuthorityError(f"无法核验官方快照:{type(exc).__name__}") from exc


def validate_fixture_authority(fixtures: list[Fixture], *, runtime_dir: Path) -> list[Fixture]:
    """Return copies with verified official fields or downgraded manual markets.

    Any bound snapshot mismatch raises FixtureAuthorityError (a ValueError), so
    callers can reject the whole request before analysis or external work.
    Frontend sequence and nonofficial neutral/lineup inputs remain unchanged.
    """
    authoritative = {}
    result = []
    seen = set()
    for fixture in fixtures:
        if fixture.match_id in seen:
            raise FixtureAuthorityError("请求中比赛ID重复")
        seen.add(fixture.match_id)
        if not fixture.source_snapshot_id:
            result.append(fixture.model_copy(deep=True, update={
                "official_handicap": None, "result_play": OfficialPlay(),
                "handicap_play": OfficialPlay(), "source_snapshot_id": None,
            }))
            continue
        sid = fixture.source_snapshot_id.lower()
        if sid not in authoritative:
            authoritative[sid] = _load_authoritative(fixture.source_snapshot_id,
                runtime_dir=runtime_dir, requested_date=fixture.business_date)
        expected = authoritative[sid].get(fixture.match_id)
        if expected is None:
            raise FixtureAuthorityError(f"比赛{fixture.match_id}不存在于绑定的官方快照")
        changed = [name for name in _OFFICIAL_FIELDS if getattr(fixture, name) != getattr(expected, name)]
        if changed:
            raise FixtureAuthorityError(f"比赛{fixture.match_id}与官方快照不一致:{','.join(changed)}")
        result.append(fixture.model_copy(deep=True, update={"source_snapshot_id": sid}))
    return result

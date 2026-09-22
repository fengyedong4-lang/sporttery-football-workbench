from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

from app.schemas import Fixture, OfficialPlay
from app.services.auto_model import build_auto_models, digest
from app.services.evidence import _recent_row
from app.services.evidence_workflow import fill_evidence_fallbacks
from app.services import evidence_workflow
from app.services.history import atomic_json_write


def sample(tmp_path, *, match_id="123", team_ids=("10", "20"), competition_id="604", season_id="14954"):
    now = datetime.now(timezone.utc)
    f = Fixture(sequence=1, match_id=match_id, match_number="001", business_date=now.date().isoformat(),
                competition="杯赛", home_team="主队", away_team="客队", kickoff_time=now + timedelta(days=2),
                official_handicap=-1, result_play=OfficialPlay(status="销售中", home=2, draw=3, away=4),
                handicap_play=OfficialPlay(status="销售中", home=2, draw=3, away=4))
    raw = []
    for i in range(12):
        raw.append({"matchId": 1000+i, "matchDate": (now-timedelta(days=30-i)).date().isoformat(),
                    "homeTeamId": 10 if i % 2 else 30, "awayTeamId": 20 if i % 2 else 40,
                    "homeTeamFullCourtGoalCnt": 1+i%3, "awayTeamFullCourtGoalCnt": i%2,
                    "homeTeamShortName": "队A", "awayTeamShortName": "队B", "tournamentShortName": "杯赛",
                    "tournamentId": int(competition_id), "seasonId": int(season_id)})
    payload = {"success": True, "errorCode": "0", "value": {"home": {"matchList": raw}, "away": {"matchList": []}}}
    source = {"source_id": "training:"+digest(payload)[:20], "kind": "training",
              "url": "https://webapi.sporttery.cn/gateway/uniform/football/getMatchResultV1.qry?sportteryMatchId="+match_id,
              "fetched_at": (now-timedelta(seconds=1)).isoformat(), "sha256": digest(payload)}
    atomic_json_write(tmp_path / "evidence" / "sources" / f"{match_id}.json", {"source": source, "payload": payload})
    rows = [_recent_row(r, source["source_id"]) for r in raw]
    b = {"match_id": match_id, "identity_verified": True, "collected_at": now.isoformat(),
         "scope": {"competition_id": competition_id, "season_id": season_id}, "sources": [source],
         "teams": {"home": {"team_id": team_ids[0], "training_matches": rows, "recent_matches": []},
                   "away": {"team_id": team_ids[1], "training_matches": [], "recent_matches": []}},
         "facts": [], "missing": [], "warnings": []}
    return f, b


def test_trains_versioned_model_and_reuses_deduped_corpus(tmp_path):
    f,b = sample(tmp_path)
    first, analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    second, _ = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert first['status'] == second['status'] == 'trained'
    assert first['training_matches'] == second['training_matches'] == 12
    assert first['version'] != second['version']
    assert first['evaluation']['denominator'] > 0
    assert analysis['freeze_eligible'] is False
    assert abs(sum(analysis['raw_probabilities']['result'].values())-1) < 1e-8
    assert len(list((tmp_path/'auto_models'/'models').glob('*.json'))) == 2
    assert not (tmp_path/'prediction_history.json').exists()


def test_source_payload_tampering_rejects_training(tmp_path):
    f,b = sample(tmp_path)
    receipt = tmp_path/'evidence'/'sources'/'123.json'
    saved = json.loads(receipt.read_text(encoding='utf8'))
    saved['payload']['value']['home']['matchList'][0]['homeTeamFullCourtGoalCnt'] = 9
    receipt.write_text(json.dumps(saved),encoding='utf8')
    meta, analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'unavailable' and analysis is None
    assert meta['excluded']['source_not_verified'] == 12


def test_unknown_teams_use_prior_not_other_team_names(tmp_path):
    f,b = sample(tmp_path,team_ids=('99','98'))
    meta, analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'trained' and meta['prior_dominated']
    assert meta['home_matches'] == meta['away_matches'] == 0
    assert analysis['analysis_result'] is None  # symmetric prior cannot break a tie


def test_same_competition_cross_season_cache_allowed_but_missing_identity_blocks(tmp_path):
    f,b = sample(tmp_path)
    build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')
    b['scope']['season_id'] = '777'
    meta, analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'trained' and analysis is not None
    assert meta['training_matches'] == 12
    b['identity_verified'] = False
    assert build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0][0]['status'] == 'unavailable'


def test_corrupt_archive_is_isolated(tmp_path):
    f,b = sample(tmp_path)
    build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')
    folder = tmp_path/'auto_models'/'corpus'/'604-14954'
    (folder/'bad.json').write_text('{"content":{},"content_sha256":"bad"}',encoding='utf8')
    meta,_ = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'trained'
    assert any('校验失败' in s for s in meta['limitations'])


def test_sale_and_handicap_gates_still_apply_to_auto_model(monkeypatch,tmp_path):
    f,b = sample(tmp_path)
    f.official_handicap = None
    f.result_play.status = '未开售'
    monkeypatch.setattr(evidence_workflow,'collect_fixture_evidence',lambda *a,**kw: b)
    row = {}
    report = fill_evidence_fallbacks([(f,row)],evidence_dir=tmp_path/'evidence',llm_enabled=False)
    assert report['auto_model_trained'] == 1
    assert row['model_status'] == 'auto_trained'
    assert row['result'] is row['handicap_result'] is None
    assert row['independent_model']['raw_probabilities']['handicap_result'] is None
    assert not row['freeze_eligible']


def test_future_receipt_cannot_be_training_evidence(tmp_path):
    f,b = sample(tmp_path)
    b['sources'][0]['fetched_at'] = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    receipt = tmp_path/'evidence'/'sources'/'123.json'
    saved = json.loads(receipt.read_text(encoding='utf8'))
    saved['source']['fetched_at'] = b['sources'][0]['fetched_at']
    receipt.write_text(json.dumps(saved), encoding='utf8')
    meta,analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'unavailable' and analysis is None


def test_started_match_never_trains(tmp_path):
    f,b = sample(tmp_path)
    f.kickoff_time = datetime.now(timezone.utc)-timedelta(seconds=1)
    meta,analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'blocked' and analysis is None


def test_club_cross_season_2025_history_is_used_with_explicit_scope(tmp_path):
    f,b = sample(tmp_path,season_id='13729')
    f.competition = '英锦标赛'
    b['scope']['season_id'] = '14954'
    rows = b['teams']['home'].pop('training_matches')
    b['teams']['home']['historical_prior_matches'] = rows
    meta,analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'trained' and not meta['prior_only']
    assert meta['historical_cache']['coverage']['seasons'] == ['13729']
    assert meta['scope']['season_id'] == '14954'
    assert meta['home_matches'] == meta['away_matches'] == 6
    assert meta['evaluation']['status'] == 'ok'


def test_youth_old_generation_never_transfers_team_strength(tmp_path):
    f,b = sample(tmp_path,competition_id='76',season_id='9952')
    f.competition = '亚运男足'
    b['scope']['season_id'] = '15364'
    b['teams']['home']['historical_prior_matches'] = b['teams']['home'].pop('training_matches')
    meta,analysis = build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0]
    assert meta['status'] == 'trained' and meta['prior_only']
    assert meta['home_matches'] == meta['away_matches'] == 0
    assert analysis['analysis_result'] is None


def test_concurrent_requests_preserve_content_archive_and_versions(tmp_path):
    f,b = sample(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: build_auto_models([(f,{})], [b], evidence_dir=tmp_path/'evidence')[0][0], range(2)))
    assert all(r['status'] == 'trained' for r in results)
    assert results[0]['version'] != results[1]['version']
    assert len(list((tmp_path/'auto_models'/'corpus'/'604-14954').glob('*.json'))) == 1

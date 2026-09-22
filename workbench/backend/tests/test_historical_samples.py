from datetime import datetime, timedelta, timezone
import json

import pytest

from app.services.historical_samples import (
    _official_row, _select, ensure_historical_samples, ensure_football_data_history,
    ensure_manifest_history,
)
from app.services.auto_model import build_auto_models
from app.services.cold_start_model import ColdStartModel
from app.services import historical_samples
from test_auto_model import sample


def test_history_download_is_once_and_explicit_rebuild_preserves_versions(tmp_path, monkeypatch):
    fixture, bundle = sample(tmp_path)
    calls = []
    def fetch(mid, folder):
        calls.append(mid)
        raise OSError('offline')
    monkeypatch.setattr(historical_samples, '_fetch_official', fetch)
    args = dict(evidence_dir=tmp_path/'evidence', bundles=[bundle], allow_download=True)
    first = ensure_historical_samples(bundle['scope'], **args)
    second = ensure_historical_samples(bundle['scope'], **args)
    assert first['version'] == second['version'] and second['cache_hit']
    assert calls == ['123']
    third = ensure_historical_samples(bundle['scope'], **args, rebuild=True)
    assert third['version'] != first['version']
    assert len(list((tmp_path/'historical_samples'/'sporttery-604'/'versions').glob('*.json'))) == 2


def test_no_network_prediction_reuses_fit_and_immutable_training_snapshot(tmp_path, monkeypatch):
    fixture, bundle = sample(tmp_path)
    one, _ = build_auto_models([(fixture,{})], [bundle], evidence_dir=tmp_path/'evidence')[0]
    def fail(*a, **kw):
        raise AssertionError('must not refit cached history')
    monkeypatch.setattr(ColdStartModel, 'fit', fail)
    two, _ = build_auto_models([(fixture,{})], [bundle], evidence_dir=tmp_path/'evidence')[0]
    assert one['status'] == two['status'] == 'trained'
    assert two['fit_cache_hit'] and two['historical_cache']['cache_hit']


def test_cache_rechecks_raw_source_hash(tmp_path):
    fixture, bundle = sample(tmp_path)
    ensure_historical_samples(bundle['scope'], evidence_dir=tmp_path/'evidence', bundles=[bundle])
    receipt = tmp_path/'evidence'/'sources'/'123.json'
    value = json.loads(receipt.read_text('utf8'))
    value['payload']['value']['home']['matchList'][0]['awayTeamFullCourtGoalCnt'] = 9
    receipt.write_text(json.dumps(value),encoding='utf8')
    with pytest.raises(ValueError, match='校验失败'):
        ensure_historical_samples(bundle['scope'], evidence_dir=tmp_path/'evidence')


def test_missing_sporttery_id_keeps_separate_uniform_namespace():
    raw = {'matchId':123,'matchDate':'2025-02-01','tournamentId':604,'seasonId':13729,
           'homeTeamId':0,'awayTeamId':20,'uniformHomeTeamId':20,
           'homeTeamFullCourtGoalCnt':1,'awayTeamFullCourtGoalCnt':0,'tournamentShortName':'英锦标赛'}
    row = _official_row(raw,'receipt')
    assert row['home_team_id'] == 'uniform:20'
    assert row['away_team_id'] == '20'


def test_2025_window_cross_season_conflicts_and_cutoff(tmp_path):
    _, bundle = sample(tmp_path)
    base = bundle['teams']['home']['training_matches'][0]
    rows = []
    for index, day in enumerate(['2024-12-31','2025-01-01','2025-05-01','2026-09-22','2026-09-23']):
        rows.append({**base,'match_id':str(index+1),'match_date':day,'season_id':str(index+1)})
    cutoff = datetime(2026,9,22,12,tzinfo=timezone.utc)
    accepted, excluded = _select(rows,'604',cutoff)
    assert [r['match_date'] for r in accepted] == ['2025-01-01','2025-05-01']
    conflict = {**rows[1], 'home_goals_90':9}
    accepted, excluded = _select(rows+[conflict],'604',cutoff)
    assert len(accepted) == 1 and excluded['conflicting_match'] == 2


def test_football_data_closing_odds_source_and_cache(tmp_path, monkeypatch):
    # Freeze the build clock: the 2026-09-22 row is deliberately a same-day
    # result and must stay outside a pre-match history cache.  Using the real
    # clock made the fixture turn into a prior-day match after Beijing midnight.
    fixed_now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(historical_samples, "datetime", FixedDateTime)
    body = ('Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365CH,B365CD,B365CA\n'
            '01/01/2025,Arsenal,Chelsea,2,1,H,1.8,3.5,4.1\n'
            '31/12/2024,Arsenal,Chelsea,1,0,H,1.7,3.4,4.4\n'
            '22/09/2026,Arsenal,Chelsea,2,0,H,1.7,3.4,4.4\n').encode()
    from app.services import datasets
    calls=[]
    monkeypatch.setattr(datasets,'_download',lambda url: calls.append(url) or body)
    cutoff=fixed_now+timedelta(minutes=1)
    first=ensure_football_data_history('英超',runtime_dir=tmp_path,allow_download=True,cutoff=cutoff)
    second=ensure_football_data_history('英超',runtime_dir=tmp_path,allow_download=True,cutoff=cutoff)
    # All three fake season files refer to one actual match; source season
    # disagreement is conservatively a conflict, not three training matches.
    assert first['coverage']['match_count'] == 0
    assert first['excluded']['conflicting_match'] == 3
    assert len(calls) == 3 and second['cache_hit']


def test_youth_current_edition_does_not_reuse_older_team_parameters(tmp_path):
    _,bundle=sample(tmp_path)
    rows=bundle['teams']['home']['training_matches']
    for r in rows:
        r['season_id']='1'
    cutoff=datetime.now(timezone.utc)
    model=ColdStartModel.fit(rows,competition_id='604',season_id='2',cutoff=cutoff,
                              include_seasons=True,isolate_generations=True)
    prediction=model.predict('10','20')
    assert prediction['sample_counts']['home_team_matches'] == 0
    assert prediction['home_rate'] == prediction['away_rate']


def test_common_league_model_uses_2025_aliased_dataset(tmp_path, monkeypatch):
    fixture,bundle=sample(tmp_path,competition_id='1')
    fixture.competition='英超';fixture.home_team='阿森纳';fixture.away_team='切尔西'
    config=tmp_path/'config';config.mkdir()
    (config/'team_aliases.json').write_text(json.dumps({'competitions':{'英超':{'阿森纳':'Arsenal','切尔西':'Chelsea'}}}),encoding='utf8')
    from app.services import datasets
    def download(url):
        season=url.split('/')[-2]
        day={'2425':'01/01/2025','2526':'01/09/2025','2627':'01/09/2026'}[season]
        return f'Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365CH,B365CD,B365CA\n{day},Arsenal,Chelsea,2,1,H,1.8,3.5,4.1\n'.encode()
    monkeypatch.setattr(datasets,'_download',download)
    monkeypatch.setattr(historical_samples,'_fetch_official',lambda *a: (_ for _ in ()).throw(OSError('offline')))
    meta,analysis=build_auto_models([(fixture,{})],[bundle],evidence_dir=tmp_path/'evidence',
                                    allow_historical_download=True,workbench_dir=tmp_path)[0]
    assert meta['status'] == 'trained'
    assert meta['training_scope']['competition_id'] == 'fd:E0'
    assert meta['historical_cache']['coverage']['first_match'] == '2025-01-01'
    assert meta['identity_mapping']['home']['official_id'] == '10'
    assert len(meta['historical_market_rows']) == 3
    assert meta['historical_market_rows'][0]['observed_at'] is None
    assert meta['historical_market_rows'][0]['closing_verified']


def test_historical_asof_never_downloads_new_sources_after_cutoff(tmp_path, monkeypatch):
    _, bundle=sample(tmp_path)
    def forbidden(*a,**kw):
        raise AssertionError('historical replay cannot collect hindsight')
    monkeypatch.setattr(historical_samples,'_fetch_official',forbidden)
    with pytest.raises(ValueError,match='事后采集'):
        ensure_historical_samples(bundle['scope'],evidence_dir=tmp_path/'evidence',bundles=[bundle],
                                  allow_download=True,cutoff=datetime.now(timezone.utc)-timedelta(days=1))


def test_manifest_uses_verified_raw_and_keeps_non_regulation_rows_out(tmp_path,monkeypatch):
    from app.services import datasets
    from hashlib import sha256
    spec=next(s for s in datasets.SEASON_DATASETS if s.competition=='世界杯')
    body=json.dumps({'matches':[
        {'date':'2026-06-11','team1':'Japan','team2':'USA','score':{'ft':[1,1],'et':[2,1]}},
        {'date':'2026-06-12','team1':'Spain','team2':'France','score':{'et':[2,1]}},
    ]}).encode()
    path=tmp_path/'imports'/'raw.json';path.parent.mkdir();path.write_bytes(body)
    (tmp_path/'imports'/'season-2025-2026-manifest.json').write_text(json.dumps({'datasets':[
        {'url':spec.url,'raw_path':'imports/raw.json','source_sha256':sha256(body).hexdigest()}
    ]}),encoding='utf8')
    h=ensure_manifest_history('世界杯',runtime_dir=tmp_path)
    assert len(h['rows'])==1 and h['rows'][0]['home_goals_90']==1
    assert h['rows'][0]['home_team_id']=='ext:worldcup:Japan'
    assert h['partitions'][0]['excluded']==1
    source_path=h['sources'][0]['receipt_path']
    from pathlib import Path
    Path(source_path).write_bytes(b'changed')
    with pytest.raises(ValueError,match='校验失败'):
        ensure_manifest_history('世界杯',runtime_dir=tmp_path)


def test_manifest_ucl_extra_time_date_is_conservatively_excluded(tmp_path,monkeypatch):
    from app.services import datasets
    def download(url):
        if url.endswith('/cl.txt'):
            return b'= UEFA 2024/25\nTue Mar 11 2025\n Liverpool FC v Paris Saint-Germain FC 0-1 a.e.t. (1-4 pen.)\n'
        season='2025' if url.endswith('2024') else '2026'
        return json.dumps([
            {'MatchNumber':1,'DateUtc':f'{season}-03-11 20:00:00Z','HomeTeam':'Liverpool','AwayTeam':'Paris','HomeTeamScore':0,'AwayTeamScore':1},
            {'MatchNumber':2,'DateUtc':f'{season}-03-12 20:00:00Z','HomeTeam':'Arsenal','AwayTeam':'Chelsea','HomeTeamScore':2,'AwayTeamScore':0}
        ]).encode()
    monkeypatch.setattr(datasets,'_download',download)
    h=ensure_manifest_history('欧冠',runtime_dir=tmp_path,allow_download=True)
    old=[r for r in h['rows'] if r['season_id']=='2024/25']
    assert len(old)==1 and old[0]['match_date']=='2025-03-12'
    assert any('存在加时' in text for text in h['parser_exclusions'])

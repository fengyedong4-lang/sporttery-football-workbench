from copy import deepcopy
from datetime import timedelta
import json

import pytest

from app.schemas import Fixture, OfficialPlay
from app.services.fixture_authority import FixtureAuthorityError, validate_fixture_authority
from app.services.snapshots import import_official_snapshot, normalize_sporttery_slate
from test_snapshots import calculator_payload


def bound(tmp_path, payload):
    snapshot=import_official_snapshot(payload,source_url='https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry',
        redirect_chain=[],output_dir=tmp_path/'snapshots',source_updated_at=None)
    normalized=normalize_sporttery_slate(payload,snapshot,'2026-09-22',date_basis='all')
    return [Fixture.model_validate(row) for row in normalized['fixtures']], snapshot


def test_valid_binding_preserves_user_order_and_nonofficial_fields(tmp_path,calculator_payload):
    fixtures,snapshot=bound(tmp_path,calculator_payload)
    selected=[fixtures[2].model_copy(update={'sequence':1,'neutral':True,'lineup_status':'预计'}),
              fixtures[0].model_copy(update={'sequence':2})]
    before=(tmp_path/'snapshots'/f"{snapshot['snapshot_id']}.json").read_bytes()
    result=validate_fixture_authority(selected,runtime_dir=tmp_path)
    assert [f.match_id for f in result]==[fixtures[2].match_id,fixtures[0].match_id]
    assert result[0].neutral and result[0].lineup_status=='预计'
    assert result[0] is not selected[0]
    assert (tmp_path/'snapshots'/f"{snapshot['snapshot_id']}.json").read_bytes()==before


@pytest.mark.parametrize('field,value',[
    ('official_handicap',2),('home_team','冒充主队'),('away_team','冒充客队'),
    ('competition','错误赛事'),('match_number','周二099'),('business_date','2026-09-23'),
    ('result_play',OfficialPlay(status='销售中',home=1.01,draw=3,away=3.1)),
    ('handicap_play',OfficialPlay(status='未开售',home=4.1,draw=3.5,away=1.8)),
])
def test_bound_official_field_tampering_is_rejected(tmp_path,calculator_payload,field,value):
    fixtures,_=bound(tmp_path,calculator_payload)
    altered=fixtures[0].model_copy(update={field:value})
    with pytest.raises(FixtureAuthorityError,match=field):
        validate_fixture_authority([altered],runtime_dir=tmp_path)


def test_kickoff_instant_must_match_but_equivalent_timezone_is_allowed(tmp_path,calculator_payload):
    from datetime import timezone
    fixtures,_=bound(tmp_path,calculator_payload)
    f=fixtures[0]
    equivalent=f.model_copy(update={'kickoff_time':f.kickoff_time.astimezone(timezone.utc)})
    assert validate_fixture_authority([equivalent],runtime_dir=tmp_path)
    with pytest.raises(FixtureAuthorityError,match='kickoff_time'):
        validate_fixture_authority([f.model_copy(update={'kickoff_time':f.kickoff_time+timedelta(minutes=1)})],runtime_dir=tmp_path)


def test_raw_snapshot_hash_tampering_is_rejected(tmp_path,calculator_payload):
    fixtures,snapshot=bound(tmp_path,calculator_payload)
    path=tmp_path/'snapshots'/f"{snapshot['snapshot_id']}.json"
    doc=json.loads(path.read_text('utf8'))
    doc['raw']['value']['matchInfoList'][0]['subMatchList'][0]['hhad']['goalLineValue']='2'
    path.write_text(json.dumps(doc),encoding='utf8')
    with pytest.raises(FixtureAuthorityError,match='SHA-256'):
        validate_fixture_authority([fixtures[0]],runtime_dir=tmp_path)


@pytest.mark.parametrize('sid',['../escape','x'*64,'a'*64])
def test_invalid_or_missing_snapshot_id_fails_closed(tmp_path,calculator_payload,sid):
    fixtures,_=bound(tmp_path,calculator_payload)
    with pytest.raises(FixtureAuthorityError):
        validate_fixture_authority([fixtures[0].model_copy(update={'source_snapshot_id':sid})],runtime_dir=tmp_path)


@pytest.mark.parametrize('urls',[
    ['https://sporttery.cn.evil.test/a'],['http://webapi.sporttery.cn/a'],
    ['https://user:password@webapi.sporttery.cn/a'],['https://webapi.sporttery.cn:8080/a'],
])
def test_source_metadata_must_stay_on_safe_official_https(tmp_path,calculator_payload,urls):
    fixtures,snapshot=bound(tmp_path,calculator_payload)
    path=tmp_path/'snapshots'/f"{snapshot['snapshot_id']}.json"
    doc=json.loads(path.read_text('utf8'));doc['redirect_chain']=urls
    path.write_text(json.dumps(doc),encoding='utf8')
    with pytest.raises(FixtureAuthorityError):
        validate_fixture_authority([fixtures[0]],runtime_dir=tmp_path)


def test_unbound_manual_input_has_no_official_markets_and_original_is_untouched(tmp_path,calculator_payload):
    fixtures,_=bound(tmp_path,calculator_payload)
    original=fixtures[0].model_copy(update={'source_snapshot_id':None})
    result=validate_fixture_authority([original],runtime_dir=tmp_path)[0]
    assert result.official_handicap is None
    assert result.result_play==result.handicap_play==OfficialPlay()
    assert original.official_handicap==-1 and original.result_play.home==2.1
    assert result.home_team==original.home_team and result.kickoff_time==original.kickoff_time


def test_match_absent_from_snapshot_is_rejected(tmp_path,calculator_payload):
    fixtures,_=bound(tmp_path,calculator_payload)
    with pytest.raises(FixtureAuthorityError,match='不存在'):
        validate_fixture_authority([fixtures[0].model_copy(update={'match_id':'999999'})],runtime_dir=tmp_path)


def test_incomplete_snapshot_is_rejected_even_for_a_valid_row(tmp_path,calculator_payload):
    payload=deepcopy(calculator_payload);payload['value']['totalCount']=999
    fixtures,_=bound(tmp_path,payload)
    with pytest.raises(FixtureAuthorityError,match='完整性'):
        validate_fixture_authority([fixtures[0]],runtime_dir=tmp_path)

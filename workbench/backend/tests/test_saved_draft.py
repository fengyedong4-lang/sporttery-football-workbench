import json
from dataclasses import replace

from fastapi.testclient import TestClient
from app import main


def test_read_saved_draft_never_calls_prediction_or_mutates_file(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'settings',replace(main.settings,runtime_dir=tmp_path))
    monkeypatch.setattr(main,'run_daily_prediction',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('must not predict')))
    folder=tmp_path/'drafts'; folder.mkdir()
    run_id='a'*32
    path=folder/f'{run_id}.json'
    text=json.dumps({'run_id':run_id,'matches':[{'match_id':'1','kickoff_time':'2020-01-01T00:00:00+08:00'}],'created_at':'2020-01-01'})
    path.write_text(text,encoding='utf8')
    response=TestClient(main.app).get('/api/drafts/latest')
    assert response.status_code==200
    assert response.json()['read_only_saved_draft'] is True
    assert path.read_text(encoding='utf8')==text


def test_invalid_or_non_versioned_draft_not_exposed(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'settings',replace(main.settings,runtime_dir=tmp_path))
    folder=tmp_path/'drafts'; folder.mkdir()
    (folder/'notes.json').write_text('{"matches":[]}',encoding='utf8')
    (folder/('b'*32+'.json')).write_text('invalid',encoding='utf8')
    assert TestClient(main.app).get('/api/drafts/latest').status_code==404

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from filingcopilot import __version__
from filingcopilot.api import create_app
from filingcopilot.runtime_identity import RuntimeIdentity
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def test_identity_detects_version_or_source_changes(tmp_path):
    (tmp_path/'__init__.py').write_text('__version__ = "1.2.3"', encoding='utf-8')
    code = tmp_path/'service.py'
    code.write_text('x = 1', encoding='utf-8')
    identity = RuntimeIdentity('1.2.3', tmp_path)
    assert not identity.inspect()['needs_restart']
    code.write_text('x = 2', encoding='utf-8')
    assert identity.inspect()['needs_restart']
    with pytest.raises(RuntimeError, match='differs'):
        RuntimeIdentity('old', tmp_path)


def test_same_size_timestamp_preserving_write_is_detected(tmp_path):
    import os

    (tmp_path/'__init__.py').write_text('__version__ = "1.2.3"',encoding='utf-8')
    code=tmp_path/'service.py'
    code.write_text('x = 1',encoding='utf-8')
    identity=RuntimeIdentity('1.2.3',tmp_path)
    stamp=code.stat()
    code.write_text('x = 2',encoding='utf-8')
    os.utime(code,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    assert identity.inspect()['needs_restart']


def test_changed_service_rejects_new_jobs_but_keeps_health_and_history(tmp_path, monkeypatch):
    import filingcopilot.api as api
    class FakeIdentity:
        def __init__(self, *args):
            pass
        def inspect(self):
            return {'loaded_version': __version__, 'needs_restart': True}
    monkeypatch.setattr(api, 'RuntimeIdentity', FakeIdentity)
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode='off'))
    try:
        client = TestClient(create_app(service))
        assert client.get('/api/health').json()['runtime']['needs_restart']
        assert client.get('/api/catalog').status_code == 200
        assert client.post('/api/companies/prepare', json={'ticker': 'AAPL'}).status_code == 503
        assert client.get('/').status_code == 503
        assert client.get('/static/app.js').status_code == 503
    finally:
        service.discovery.executor.shutdown(wait=True)


def test_start_script_has_readonly_preflight_and_never_kills_processes():
    source = Path('start.ps1').read_text(encoding='utf-8')
    assert '$CheckOnly' in source and '8123, 8141, 8159, 8151' in source
    assert 'Stop-Process' not in source and 'taskkill' not in source


@pytest.mark.parametrize('change',['resource','new_module'])
def test_resource_change_and_new_source_file_require_restart(tmp_path,change):
    (tmp_path/'__init__.py').write_text('__version__ = "1.2.3"',encoding='utf-8')
    resource=tmp_path/'domain-defaults.json'
    resource.write_text('{}',encoding='utf-8')
    identity=RuntimeIdentity('1.2.3',tmp_path)
    if change=='resource':
        resource.write_text('{"changed":true}',encoding='utf-8')
    else:
        (tmp_path/'new.py').write_text('x=1',encoding='utf-8')
    assert identity.inspect()['needs_restart']

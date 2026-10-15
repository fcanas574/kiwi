from fastapi.testclient import TestClient

from kiwi_pi.app import create_app
from test_station import cfg


def test_desktop_exit_is_local_only_and_clears_leds_before_stopping_kiosk(cfg, monkeypatch):
    cfg.raw['server'].update(allow_remote_admin=False,enable_desktop_exit=True)
    calls=[]
    class Process:
        returncode=0
        async def communicate(self): return (b'',b'')
    async def execute(*args,**kwargs):
        calls.append(args)
        assert not app.state.scan.station.pending
        assert app.state.scan.snapshot()['capture_state']=='paused'
        return Process()
    monkeypatch.setattr('kiwi_pi.app.asyncio.create_subprocess_exec',execute)
    app=create_app(cfg)
    with TestClient(app,client=('127.0.0.1',1000)) as station:
        phone=TestClient(app,client=('192.168.1.10',1001))
        try:
            assert phone.post('/api/station/desktop').status_code==403
            app.state.scan.station.add_guides([cfg.slot_by_id(1)],'cycle')
            assert station.post('/api/station/desktop').status_code==200
            assert calls==[('/usr/bin/systemctl','--user','stop','kiwi-kiosk.service')]
        finally: phone.close()


def test_desktop_exit_is_disabled_on_generic_installs(cfg):
    cfg.raw['server']['enable_desktop_exit']=False
    with TestClient(create_app(cfg),client=('127.0.0.1',1000)) as client:
        assert client.post('/api/station/desktop').status_code==404

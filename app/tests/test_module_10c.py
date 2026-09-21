"""Module 10C: isolated tests; no paid Codex or network requests."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from gateway.contracts import ChatCompletionRequest, GenerateRequest, GatewayError, ImageRequest
from gateway.episode_sessions import EpisodeSessions, normalize_title, usage_amounts
from gateway.settings_manager import FIELD_SPECS
from gateway.codex_runner import CodexRunner


def test_episode_identity_and_usage_persist_across_restart(tmp_path):
    db = tmp_path / 'episodes.sqlite3'
    a, b = str(uuid4()), str(uuid4())
    store = EpisodeSessions(db)
    store.record('  MY Son\nCalled  Me ', a,
                 {'input_tokens': 200, 'cached_input_tokens': 50, 'output_tokens': 30})
    assert store.get('my son called me') == a
    store.record('my son called me', a,
                 {'input_tokens': 150, 'input_token_details': {'cached_input_tokens': 70}, 'output_tokens': 10})
    store.record('A Different Episode', b)
    records = {r['title'].casefold(): r for r in store.list()}
    first = records['my son called me']
    assert (first['turns'], first['input_tokens'], first['cached_input_tokens'], first['output_tokens']) == (2,350,120,40)
    assert len(records) == 2
    store.close()
    second = EpisodeSessions(db)
    assert second.get('my son called me') == a
    assert second.get('A Different Episode') == b
    assert second.reset('my son called me') is True
    assert second.get('my son called me') is None
    assert second.get('A Different Episode') == b
    second.close()


def test_uncertain_session_cannot_be_resumed_and_new_turn_rotates(tmp_path):
    store = EpisodeSessions(tmp_path / 'state.sqlite3')
    old, new = str(uuid4()), str(uuid4())
    store.record('Episode 1', old)
    store.mark_uncertain('Episode 1')
    assert store.get('Episode 1') is None
    assert store.list()[0]['status'] == 'uncertain'
    store.record('Episode 1', new)
    assert store.get('Episode 1') == new
    assert store.list()[0]['turns'] == 1
    store.close()


def test_title_validation_and_usage_shape(tmp_path):
    store = EpisodeSessions(tmp_path / 'db.sqlite3')
    with pytest.raises(GatewayError):
        store.get(' \t ')
    with pytest.raises(GatewayError):
        store.get('x' * 301)
    assert usage_amounts({'input_tokens': True, 'cached_input_tokens': -2}) == (0,0,0)
    assert normalize_title('  A \n  B  ') == 'A B'
    store.close()


def test_session_lock_serializes_same_title_not_independent_episodes(tmp_path):
    store = EpisodeSessions(tmp_path / 'db.sqlite3')
    async def verify():
        in_first = asyncio.Event()
        release = asyncio.Event()
        entered_second = asyncio.Event()
        async def first():
            async with store.lock(' EPISODE A '):
                in_first.set()
                await release.wait()
        async def second():
            async with store.lock('episode a'):
                entered_second.set()
        t1 = asyncio.create_task(first())
        await in_first.wait()
        t2 = asyncio.create_task(second())
        await asyncio.sleep(0)
        assert not entered_second.is_set()
        async with store.lock('Episode B'):
            assert not entered_second.is_set()
        release.set()
        await asyncio.gather(t1,t2)
        assert entered_second.is_set()
    asyncio.run(verify())
    store.close()


def test_optional_contract_and_opt_in_default():
    assert GenerateRequest(prompt='hi').episode_title is None
    assert ChatCompletionRequest(messages=[{'role': 'user','content':'hi'}]).episode_title is None
    assert ImageRequest(prompt='hi').episode_title is None
    assert FIELD_SPECS['episode_sessions_enabled'].default is False
    assert FIELD_SPECS['episode_sessions_enabled'].restart_required is True
    assert FIELD_SPECS['episode_sessions_enabled'].group == 'execution'


class _FakeStdin:
    def __init__(self): self.sent = b''
    def write(self, data): self.sent += data
    async def drain(self): pass
    def close(self): pass

class _FakeProcess:
    returncode = 0
    pid = 1234
    def __init__(self): self.stdin = _FakeStdin()


def _events(thread_id: str) -> bytes:
    return (json.dumps({'type':'thread.started','thread_id':thread_id})+'\n'+
            json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'cached_input_tokens':5,'output_tokens':2}})+'\n').encode()


def test_text_first_then_resume_uses_exact_id_and_never_ephemeral(monkeypatch):
    import gateway.codex_runner as mod
    observed = []
    t = str(uuid4())
    async def process(*args, **kwargs):
        observed.append(args)
        path = Path(args[args.index('-o')+1])
        path.write_text('hello from Codex',encoding='utf-8')
        return _FakeProcess()
    async def communicate(*args,**kwargs): return (_events(t),b'')
    monkeypatch.setattr(mod.asyncio,'create_subprocess_exec',process)
    monkeypatch.setattr(CodexRunner,'_communicate',staticmethod(communicate))
    runner = CodexRunner(['codex'])
    async def call():
        first = await runner.run(prompt='A',schema=None,web_search=False,timeout_seconds=10,persistent=True)
        second = await runner.run(prompt='B',schema=None,web_search=False,timeout_seconds=10,persistent=True,session_id=first.thread_id)
        return first,second
    first,second = asyncio.run(call())
    assert first.thread_id == second.thread_id == t
    assert len(observed) == 2
    assert '--ephemeral' not in observed[0] and '--ephemeral' not in observed[1]
    assert 'resume' not in observed[0]
    assert observed[1][observed[1].index('resume')+1] == t
    assert '--last' not in observed[1]


def test_wrong_resumed_thread_fails_closed(monkeypatch):
    import gateway.codex_runner as mod
    previous,unexpected = str(uuid4()),str(uuid4())
    async def process(*args, **kwargs):
        Path(args[args.index('-o')+1]).write_text('okay',encoding='utf-8')
        return _FakeProcess()
    async def communicate(*args, **kwargs): return (_events(unexpected),b'')
    monkeypatch.setattr(mod.asyncio,'create_subprocess_exec',process)
    monkeypatch.setattr(CodexRunner,'_communicate',staticmethod(communicate))
    with pytest.raises(GatewayError,match='expected episode session'):
        asyncio.run(CodexRunner(['codex']).run(prompt='x',schema=None,web_search=False,
                 timeout_seconds=10,persistent=True,session_id=previous))


def test_resume_image_never_returns_an_old_image(monkeypatch,tmp_path):
    import gateway.codex_runner as mod
    thread = str(uuid4())
    home = tmp_path/'.codex'
    folder = home/'generated_images'/thread
    folder.mkdir(parents=True)
    (folder/'old.png').write_bytes(b'old file')
    monkeypatch.setenv('CODEX_HOME',str(home))
    observed = []
    async def process(*args, **kwargs):
        observed.append(args)
        return _FakeProcess()
    async def communicate(*args,**kwargs): return (_events(thread),b'')
    monkeypatch.setattr(mod.asyncio,'create_subprocess_exec',process)
    monkeypatch.setattr(CodexRunner,'_communicate',staticmethod(communicate))
    with pytest.raises(GatewayError,match='did not create a new image'):
        asyncio.run(CodexRunner(['codex']).run_image(prompt='image',timeout_seconds=10,
                    persistent=True,session_id=thread))
    assert 'resume' in observed[0]
    assert '--ephemeral' not in observed[0]
    assert observed[0][observed[0].index('resume')+1] == thread


def test_resumed_image_chooses_only_current_turn(monkeypatch,tmp_path):
    import gateway.codex_runner as mod
    thread = str(uuid4())
    home = tmp_path/'.codex'
    folder = home/'generated_images'/thread
    folder.mkdir(parents=True)
    old=folder/'old.png'
    old.write_bytes(b'previous')
    monkeypatch.setenv('CODEX_HOME',str(home))
    async def process(*args, **kwargs):
        (folder/'new.png').write_bytes(b'current turn')
        return _FakeProcess()
    async def communicate(*args,**kwargs): return (_events(thread),b'')
    monkeypatch.setattr(mod.asyncio,'create_subprocess_exec',process)
    monkeypatch.setattr(CodexRunner,'_communicate',staticmethod(communicate))
    result=asyncio.run(CodexRunner(['codex']).run_image(prompt='image',timeout_seconds=10,
                   persistent=True,session_id=thread))
    assert result.path.name=='new.png'
    assert result.thread_id==thread


def test_authenticated_unified_text_image_and_chat_episode_http(tmp_path,monkeypatch):
    """Complete repository only: verify a text turn and an image turn share UUID.

    App composition needs dashboard/retention modules omitted from the code-review
    source ZIP; this test runs as part of the user's complete Windows suite.
    """
    import sys
    from fastapi.testclient import TestClient
    from gateway import app as root
    from gateway.artifact_store import ArtifactStore
    from gateway.config import Settings
    from gateway.contracts import RunResult, ImageRunResult
    from gateway.job_store import JobStore

    home = tmp_path/'data'
    monkeypatch.setenv('AI_GATEWAY_DATA_DIR',str(home))
    thread = str(uuid4())
    calls = []
    image_file = tmp_path/'image.png'
    # A 1x1 valid PNG, no model call or external downloads.
    # Valid RGB 1x1 PNG built entirely from stdlib, no image service/Pillow.
    import struct, zlib
    def chunk(tag, content):
        return (struct.pack('>I',len(content))+tag+content+
                struct.pack('>I',zlib.crc32(tag+content)&0xffffffff))
    image_file.write_bytes(
        b'\x89PNG\r\n\x1a\n'+
        chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+
        chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00'))+
        chunk(b'IEND',b'')
    )

    class FakeRunner:
        async def run(self, **kwargs):
            calls.append(('text',kwargs.get('session_id'),kwargs.get('persistent')))
            return RunResult(response='{"ok":true}',raw_response='{"ok":true}',
                             usage={'input_tokens':10,'cached_input_tokens':3,'output_tokens':2},
                             thread_id=thread)
        async def run_image(self, **kwargs):
            calls.append(('image',kwargs.get('session_id'),kwargs.get('persistent')))
            return ImageRunResult(image_file,'image/png',
                                  {'input_tokens':9,'cached_input_tokens':4,'output_tokens':2},thread)

    token='module-10c-testing-token-abcdefghijk'
    store=JobStore(home/'gateway.sqlite3')
    app=root.create_app(
        settings=Settings(api_token=token,codex_exe=sys.executable,
                          quota_monitor_enabled=False,episode_sessions_enabled=True),
        runner=FakeRunner(),job_store=store,
        artifact_store=ArtifactStore(home/'artifacts'),
    )
    headers={'Authorization':'Bearer '+token}
    with TestClient(app) as client:
        assert client.get('/dashboard/api/episode-sessions').status_code == 401
        first=client.post('/v1/chat/completions',headers=headers,json={
            'episode_title':' The Episode ',
            'messages':[{'role':'user','content':'return JSON'}],
            'response_format':{'type':'json_object'},
        })
        assert first.status_code == 200,first.text
        second=client.post('/v1/images/generations',headers=headers,json={
            'episode_title':'the episode','prompt':'one image',
        })
        assert second.status_code == 200,second.text
        report=client.get('/dashboard/api/episode-sessions',headers=headers).json()
        assert report['enabled'] is True
        assert report['sessions'][0]['turns']==2
        assert report['sessions'][0]['thread_id']==thread
        assert report['sessions'][0]['cached_input_tokens']==7
        assert calls == [('text',None,True),('image',thread,True)]
        reset=client.post('/dashboard/api/episode-sessions/reset',headers=headers,json={
            'episode_title':'The Episode','confirmation':'RESET_EPISODE_SESSION',
        })
        assert reset.status_code == 200 and reset.json()['reset']
        assert not client.get('/dashboard/api/episode-sessions',headers=headers).json()['sessions']
    store.close()

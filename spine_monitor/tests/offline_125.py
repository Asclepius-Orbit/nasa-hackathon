"""Opt-in live offline check. Run via offline-125.compose.yml, never in the quality gate."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx
import uvicorn
from fastapi.testclient import TestClient
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from monitor.db import Store
from monitor.readonly import create_read_app
from monitor.settings import AppSettings
from spine_agent.http import create_app as create_agent_app
from spine_agent.settings import AgentSettings
from spine_engine.api import create_default_app

ROOT = Path(__file__).resolve().parents[1]


def example(name):
    return json.loads((ROOT / 'contracts/examples/valid' / f'{name}.json').read_text(encoding='utf-8'))


def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread
        time.sleep(.1)
    raise RuntimeError(f'HTTP server {port} failed to start')


async def check_mcp(env, assessment_id):
    params = StdioServerParameters(command=sys.executable, args=['-m', 'spine_mcp_server', '--dataset', 'http'], env=env)
    with anyio.fail_after(60), open(os.devnull, 'w') as err:
        async with Client(stdio_client(params, errlog=err)) as client:
            status = await client.call_tool('get_current_status', {'astronaut_id': 'AST-01'})
            assert not status.is_error, status
            assert assessment_id in json.dumps(status.structured_content), status
            evidence = await client.call_tool('search_evidence', {'query': 'microgravity seated height'})
            assert not evidence.is_error and evidence.structured_content['matches'], evidence
            missing_params = dict(env, SPINE_EVIDENCE_DIR='/definitely/missing')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'spine_mcp_server', '--dataset', 'http'], env=missing_params)
    with anyio.fail_after(60), open(os.devnull, 'w') as err:
        async with Client(stdio_client(params, errlog=err)) as client:
            missing = await client.call_tool('search_evidence', {'query': 'height'})
            assert missing.is_error and 'offline evidence index' in missing.content[0].text, missing
    return evidence.structured_content['matches']


def report():
    body = {'schema_version': '1.0.0', 'astronaut_id': 'AST-01',
            'conversation_id': 'c0ffee00-0000-4000-8000-000000000001',
            'question': 'Report on microgravity seated height evidence and cite sources.'}
    url = 'http://127.0.0.1:18003/report'
    assert httpx.post(url, json=body, timeout=30).status_code == 401
    response = httpx.post(url, json=body, headers={'authorization': 'Bearer offline-test-token',
                          'x-astronaut-id': 'AST-01'}, timeout=600)
    assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream'), response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events and events[-1]['type'] == 'end' and not any(e['type'] in ('fallback', 'error') for e in events), events
    assert any(e['type'] == 'tool_call' and e['tool_name'] == 'search_evidence' for e in events), events
    text = ''.join(e['delta'] for e in events if e['type'] == 'text')
    print('REPORT:', text, flush=True)
    corpus = json.loads((ROOT / 'mcp_server/src/spine_mcp_server/evidence_data/corpus.json').read_text(encoding='utf-8-sig'))
    cited = [p for p in corpus if p['url'] in text]
    assert cited and all(p['title'] in text and p['locator'] in text and p['text'] in text and p['limitations'] in text for p in cited), events
    assert len([line for line in text.splitlines() if line.startswith('Source:')]) == len(cited), events


def main():
    # The test container starts on an internal network: even DNS/IP egress must fail.
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=2):
            raise AssertionError('External network unexpectedly reachable')
    except OSError:
        pass
    for _ in range(120):
        try:
            with urllib.request.urlopen('http://ollama:11434/api/tags', timeout=2) as response:
                tags = json.load(response)['models']
            break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError('Ollama unavailable on isolated network')
    if not any(m['name'].startswith('qwen3.5:4b') for m in tags):
        raise RuntimeError('Missing local Ollama model qwen3.5:4b; prepare it before going offline')
    missing_model = httpx.post('http://ollama:11434/api/generate', json={
        'model': 'spine-offline-deliberately-missing:latest', 'prompt': 'test', 'stream': False,
    }, timeout=30)
    assert missing_model.status_code == 404 and 'not found' in missing_model.text.lower(), missing_model.text
    engine = create_default_app()
    client = TestClient(engine)
    stretch = {'schema_version': '1.0.0', 'baseline_resistance_ohm': 4500, 'current_resistance_ohm': 4600,
               'reference_length_mm': 500, 'prediction': {'baseline_posture': 'straight', 'current_posture': 'straight',
               'baseline_gravity': 'earth_1g', 'elapsed_days': 5, 'assumed_daily_change_mm': .2, 'sensor_uncertainty_ohm': 10}}
    estimate = client.post('/stretch-estimate', json=stretch)
    assert estimate.status_code == 200, estimate.text
    baseline = client.post('/baseline', json=example('engine/baseline-request'))
    assert baseline.status_code == 200, baseline.text
    request = example('engine/assess-request') | {'baseline': baseline.json()}
    assessed = client.post('/assess', json=request)
    assert assessed.status_code == 200, assessed.text
    assessment = assessed.json()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'app.db')
        store.put_profile(request['profile'])
        store.put_baseline(baseline.json())
        store.put_assessment(assessment)
        servers = [serve(engine, 18001), serve(create_read_app(AppSettings(_env_file=None), store=store,
                   clock=lambda: datetime(2027, 2, 9, 10, tzinfo=UTC)), 18002),
                   serve(create_agent_app(AgentSettings(request_timeout_s=300, http_token='offline-test-token')), 18003)]
        try:
            env = dict(os.environ)
            asyncio.run(check_mcp(env, assessment['assessment_id']))
            report()
        finally:
            for server, thread in servers:
                server.should_exit = True
                thread.join(timeout=10)
            store.close()
    print('Offline engine, MCP, evidence and Ollama check passed.', flush=True)


if __name__ == '__main__':
    main()

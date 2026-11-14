import json

import httpx
import pytest
from pydantic_ai.exceptions import ModelConnectionError
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from spine_agent.agent import ChatSession, build_agent
from spine_agent.http import create_app
from spine_agent.report import evidence_report
from spine_agent.settings import AgentSettings

PASSAGE = {'title': 'Source', 'url': 'https://example.org/source', 'locator': 'p. 4',
           'text': 'Observed height changed.', 'limitations': 'Not a clinical threshold.'}


def messages(content, outcome='success'):
    return [ModelRequest(parts=[ToolReturnPart(tool_name='search_evidence', content=content, tool_call_id='a', outcome=outcome)])]


@pytest.mark.unit
def test_report_quotes_only_complete_retrieved_passages():
    report = evidence_report(messages({'matches': [PASSAGE]}))
    for value in PASSAGE.values():
        assert value in report


@pytest.mark.unit
@pytest.mark.parametrize('content', [{'matches': []}, {'matches': [{**PASSAGE, 'locator': ''}]}, 'error', {'matches': [{**PASSAGE, 'url': 'javascript:bad'}]}])
def test_missing_or_invalid_evidence_is_explicit(content):
    assert 'Insufficient evidence' in evidence_report(messages(content))


@pytest.mark.unit
def test_failed_tool_is_not_evidence():
    assert 'Insufficient evidence' in evidence_report(messages({'matches': [PASSAGE]}, outcome='failed'))


@pytest.mark.unit
@pytest.mark.anyio
async def test_report_suppresses_model_claims_and_uses_this_turns_tool_result():
    toolset = FunctionToolset()

    @toolset.tool_plain
    def search_evidence(query: str) -> dict:
        return {'matches': [PASSAGE]}

    async def model(messages, info):
        if not any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts):
            yield {0: DeltaToolCall(name='search_evidence', json_args=json.dumps({'query': 'height'}), tool_call_id='c')}
        else:
            yield 'Invented conclusion and invented citation.'

    class Source:
        async def current_status(self, astronaut_id):
            return None
        async def explanation(self, assessment_id):
            return None

    agent = build_agent(AgentSettings(), model=FunctionModel(stream_function=model), toolsets=[toolset])
    async with ChatSession(agent, Source(), 'AST-01') as session:
        chunks = [chunk async for chunk in session.ask_report('Report')]
        assert session.history == []
    text = ''.join(chunk.text for chunk in chunks)
    assert 'Invented' not in text
    assert all(value in text for value in PASSAGE.values())


@pytest.mark.integration
@pytest.mark.anyio
async def test_report_route_requires_auth_and_returns_no_model_claims():
    async def model(messages, info):
        yield 'Unsupported invented claim.'

    settings = AgentSettings(http_token='test-token')
    agent = build_agent(settings, model=FunctionModel(stream_function=model), toolsets=[])
    app = create_app(settings, agent=agent)
    payload = {'schema_version': '1.0.0', 'conversation_id': 'c0ffee00-0000-4000-8000-000000000001', 'astronaut_id': 'AST-01', 'question': 'Report'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/report', json=payload)).status_code == 401
        response = await client.post('/report', json=payload, headers={'authorization': 'Bearer test-token', 'x-astronaut-id': 'AST-01'})
    assert response.status_code == 200
    assert 'Insufficient evidence' in response.text
    assert 'Unsupported invented claim' not in response.text
    assert 'event: end' in response.text


@pytest.mark.integration
@pytest.mark.anyio
async def test_report_accepts_actual_mcp_evidence():
    import sys

    async def model(messages, info):
        if not any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts):
            yield {0: DeltaToolCall(name='search_evidence', json_args='{"query":"seated height"}', tool_call_id='c')}
        else:
            yield 'Not published.'

    settings = AgentSettings(mcp_command=sys.executable, mcp_args=['-m', 'spine_mcp_server', '--dataset', 'examples'])
    agent = build_agent(settings, model=FunctionModel(stream_function=model))
    async with ChatSession(agent, None, 'AST-01') as session:
        chunks = [chunk async for chunk in session.ask_report('Report')]
    text = ''.join(chunk.text for chunk in chunks)
    assert 'NASA' in text
    assert 'https://' in text
    assert 'Limitations:' in text
    assert 'Not published.' not in text


@pytest.mark.unit
@pytest.mark.anyio
async def test_report_with_model_failure_never_emits_partial_text():
    async def model(messages, info):
        yield 'Unverified partial claim.'
        raise ModelConnectionError('test', 'failed', in_stream=True)

    agent = build_agent(AgentSettings(), model=FunctionModel(stream_function=model), toolsets=[])
    async with ChatSession(agent, None, 'AST-01') as session:
        chunks = [chunk async for chunk in session.ask_report('Report')]
    assert len(chunks) == 1
    assert 'Insufficient evidence' in chunks[0].text

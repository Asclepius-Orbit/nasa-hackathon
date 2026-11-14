import json

import pytest
from spine_mcp_server.evidence import EvidenceIndex, EvidenceUnavailable, build_index


def test_local_index_returns_citable_evidence_and_no_unrelated_match():
    index = EvidenceIndex.default()
    matches = index.search('seated posture height', 3)
    assert matches
    assert matches[0].url.startswith('https://')
    assert matches[0].locator
    assert index.search('quasar xylophone', 3) == []


def test_missing_index_is_explicit(tmp_path):
    with pytest.raises(EvidenceUnavailable, match='offline evidence index'):
        EvidenceIndex(tmp_path / 'missing.json').search('height', 3)

@pytest.mark.anyio
@pytest.mark.integration
async def test_mcp_search_exposes_citations():
    from mcp.client.client import Client
    from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
    from spine_mcp_server.server import create_server

    server = create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource())
    async with Client(server) as client:
        result = await client.call_tool('search_evidence', {'query': 'seated height'})
    assert not result.is_error
    assert result.structured_content['matches'][0]['locator']


def test_stale_index_is_explicit(tmp_path):
    import json
    corpus = tmp_path / 'corpus.json'
    corpus.write_text('[]', encoding='utf-8')
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'corpus_sha256': 'wrong', 'vectors': []}), encoding='utf-8')
    with pytest.raises(EvidenceUnavailable, match='index does not match corpus'):
        EvidenceIndex(index, corpus).search('height', 3)


@pytest.mark.anyio
@pytest.mark.integration
async def test_mcp_missing_resources_are_tool_errors(tmp_path, monkeypatch):
    from mcp.client.client import Client
    from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
    from spine_mcp_server.server import create_server

    monkeypatch.setenv('SPINE_EVIDENCE_DIR', str(tmp_path))
    server = create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource())
    async with Client(server) as client:
        result = await client.call_tool('search_evidence', {'query': 'height'})
    assert result.is_error
    assert 'offline evidence index' in result.content[0].text


def test_index_accepts_corpus_with_different_line_endings(tmp_path):
    from spine_mcp_server.evidence import build_index

    source = EvidenceIndex.default().corpus.read_bytes().replace(b'\r\n', b'\n')
    corpus = tmp_path / 'corpus.json'
    index = tmp_path / 'index.json'
    corpus.write_bytes(source)
    build_index(corpus, index)
    corpus.write_bytes(source.replace(b'\n', b'\r\n'))
    assert EvidenceIndex(index, corpus).search('seated height', 2)


@pytest.fixture
def synthetic_evidence(tmp_path, monkeypatch):
    passages = [dict(id=key, title='Title', url='https://example.org', locator='p. 1',
                     text='spinal height', limitations='Example') for key in ('c', 'a', 'b')]
    corpus = tmp_path / 'corpus.json'
    corpus.write_text(json.dumps(passages), encoding='utf-8')
    build_index(corpus, tmp_path / 'index.json')
    monkeypatch.setenv('SPINE_EVIDENCE_DIR', str(tmp_path))


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize(('limit', 'expected', 'truncated'), [(2, ['a', 'b'], True), (3, ['a', 'b', 'c'], False)])
async def test_mcp_search_limit_and_tie_order(synthetic_evidence, limit, expected, truncated):
    from mcp.client.client import Client
    from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
    from spine_mcp_server.server import create_server

    async with Client(create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource())) as client:
        result = await client.call_tool('search_evidence', {'query': 'spinal', 'limit': limit})
    assert not result.is_error
    assert [match['id'] for match in result.structured_content['matches']] == expected
    assert result.structured_content['truncated'] is truncated


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.parametrize('arguments', [{'query': ''}, {'query': 'spinal', 'limit': 0}, {'query': 'spinal', 'limit': 21}])
async def test_mcp_search_rejects_invalid_arguments(synthetic_evidence, arguments):
    from mcp.client.client import Client
    from spine_mcp_server.mocks import ExampleAppReader, ExampleAssessBaseSource, ExampleSimulator
    from spine_mcp_server.server import create_server

    async with Client(create_server(ExampleAppReader(), ExampleSimulator(), ExampleAssessBaseSource())) as client:
        result = await client.call_tool('search_evidence', arguments)
    assert result.is_error


def test_search_ignores_common_words_and_normalizes_accents(synthetic_evidence):
    index = EvidenceIndex.default()
    assert index.search('pizza recipe in italy and of', 3) == []
    assert len(index.search('spínal', 3)) == 3


def test_empty_evidence_directory_uses_default(monkeypatch):
    monkeypatch.setenv('SPINE_EVIDENCE_DIR', '')
    assert EvidenceIndex.default().search('height', 1)


def test_invalid_vector_shape_is_reported(tmp_path):
    corpus = tmp_path / 'corpus.json'
    corpus.write_text(json.dumps([dict(id='a', title='t', url='https://example.org', locator='p',
                                      text='height', limitations='x')]), encoding='utf-8')
    index = tmp_path / 'index.json'
    build_index(corpus, index)
    data = json.loads(index.read_text(encoding='utf-8'))
    data['vectors'] = [None]
    index.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(EvidenceUnavailable, match='invalid index vectors'):
        EvidenceIndex(index, corpus).search('height', 1)

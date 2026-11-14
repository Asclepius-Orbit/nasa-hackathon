"""Extractive reports from evidence actually returned in the current model run."""

import json
from urllib.parse import urlparse

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart


def evidence_report(messages: list[ModelMessage]) -> str:
    passages: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or part.tool_name != 'search_evidence' or part.outcome != 'success':
                continue
            try:
                data = json.loads(part.model_response_str())
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get('matches'), list):
                continue
            for match in data['matches']:
                if not isinstance(match, dict):
                    continue
                fields = [match.get(key) for key in ('title', 'url', 'locator', 'text', 'limitations')]
                if not all(isinstance(value, str) and value.strip() for value in fields):
                    continue
                title, url, locator, text, limitations = fields
                parsed = urlparse(url)
                if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                    continue
                passages.append(f'{text}\nLimitations: {limitations}\nSource: {title} — {url} — {locator}')
    return '\n\n'.join(dict.fromkeys(passages)) if passages else 'Insufficient evidence: no verifiable local source passage was retrieved.'

import asyncio
import datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import requests


class PollFinished(Exception):
    """Stop the service after one polling cycle."""


@pytest.mark.parametrize('failure', ['invalid-index', 'http-error', 'connection-error', 'read-timeout'])
def test_outage_still_publishes_queued_issues(app, monkeypatch, failure):
    monkeypatch.setenv('APOD_URL', '/db/apod.html')
    monkeypatch.setenv('PARSING_INTERVAL_SEC', '1800')
    monkeypatch.setenv('HEALTHCHECK_URL', 'https://example.test/ping')
    queued = [app.Issue('Title', 'Body', '/db/msg/1', 'image.jpg', datetime.date(2026, 11, 17))]
    monkeypatch.setattr(app.models, 'init_db', lambda: None)
    monkeypatch.setattr(app, 'get_unpublished', lambda: queued)
    publish = AsyncMock()
    monkeypatch.setattr(app, 'publish_issues', publish)
    healthcheck = AsyncMock()
    monkeypatch.setattr(app.web_client, 'get', healthcheck)
    sleep = AsyncMock(side_effect=PollFinished)
    monkeypatch.setattr(app.asyncio, 'sleep', sleep)

    requests_made = []

    def get(url, **kwargs):
        requests_made.append(kwargs)
        if failure == 'read-timeout':
            raise requests.ReadTimeout()
        if failure == 'connection-error':
            raise requests.ConnectionError()
        response = requests.Response()
        response.status_code = 503 if failure == 'http-error' else 200
        response._content = b'<html>Unavailable</html>'
        return response

    monkeypatch.setattr(app.requests, 'get', get)
    with pytest.raises(PollFinished):
        asyncio.run(app.main())

    publish.assert_awaited_once_with(queued)
    healthcheck.assert_not_awaited()
    sleep.assert_awaited_once_with(1800)
    assert len(requests_made) == 1
    assert 0 < requests_made[0]['timeout'] < 1800


def test_article_request_has_timeout_and_uses_preview_when_unavailable(app, monkeypatch):
    preview_url = 'https://images.astronet.ru/pubd/preview.jpg'
    index_html = f'''
        <div id="content">
          <p class="title"><a href="/db/msg/1"><img src="{preview_url}"></a>
            <b>Снимок дня</b><small><b>17.11.2026</b></small></p>
          <p class="abstract"><small>На этом снимке видно яркое сияние над горизонтом.</small></p>
        </div>
    '''
    requests_made = []

    def get(url, **kwargs):
        requests_made.append((url, kwargs))
        if url.endswith('/db/msg/1'):
            raise requests.ReadTimeout()
        response = requests.Response()
        response.status_code = 200
        response._content = index_html.encode()
        return response

    monkeypatch.setattr(app.requests, 'get', get)

    issues = app.get_last_issues(f'{app.root_url}/db/apod.html')

    assert [issue.image_url for issue in issues] == [preview_url]
    assert len(requests_made) == 2
    assert all(0 < kwargs['timeout'] < 1800 for _, kwargs in requests_made)


@pytest.mark.parametrize('status', [None, 204, 500])
def test_healthcheck_skips_blank_url_and_reports_http_failures(app, monkeypatch, status):
    url = 'https://example.test/ping/synthetic-private-id' if status else ''
    monkeypatch.setenv('APOD_URL', '/db/apod.html')
    monkeypatch.setenv('PARSING_INTERVAL_SEC', '1800')
    monkeypatch.setenv('HEALTHCHECK_URL', url)
    monkeypatch.setattr(app.models, 'init_db', lambda: None)
    monkeypatch.setattr(app, 'get_last_issues', lambda url: [])
    monkeypatch.setattr(app, 'get_unpublished', lambda: [])
    monkeypatch.setattr(app.asyncio, 'sleep', AsyncMock(side_effect=PollFinished))
    get = AsyncMock()
    if status:
        get.return_value = httpx.Response(status, request=httpx.Request('GET', url))
    monkeypatch.setattr(app.web_client, 'get', get)
    messages = []
    sink = app.logger.add(lambda entry: messages.append(entry.record['message']), level='ERROR')
    try:
        with pytest.raises(PollFinished):
            asyncio.run(app.main())
    finally:
        app.logger.remove(sink)

    if status is None:
        get.assert_not_awaited()
    else:
        assert get.await_count == 1
    if status == 500:
        assert any('Healthcheck' in message and '500' in message for message in messages)
        assert all('synthetic-private-id' not in message for message in messages)
    else:
        assert not messages

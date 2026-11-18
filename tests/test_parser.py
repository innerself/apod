from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('abstract', [
    '',
    '<p class="abstract"><small> </small></p>',
    '<p class="abstract"><small>12345 ...</small></p>',
    '<p class="abstract"><small>This photograph shows the night sky.</small></p>',
])
def test_skips_unusable_abstract_and_parses_next_issue(app, monkeypatch, abstract):
    root_url = 'https://www.astronet.ru'
    index_url = f'{root_url}/db/apod.html'

    body = 'На этом снимке видно яркое полярное сияние над горизонтом.'
    index = f'''
        <div id="content">
          <p class="title">
            <a href="/db/msg/1">First issue</a><b>First issue</b>
            <small><b>17.11.2026</b></small>
            {abstract}
          <p class="title">
            <a href="/db/msg/2">Второй выпуск</a><b>Второй выпуск</b>
            <small><b>16.11.2026</b></small>
            <p class="abstract"><small>{body}</small></p>
        </div>
    '''
    image_url = 'https://images.astronet.ru/pubd/test.jpg'
    article = f'<div id="content"><img src="{image_url}"></div>'
    pages = {
        index_url: index,
        f'{root_url}/db/msg/1': article,
        f'{root_url}/db/msg/2': article,
    }

    def get(url, **kwargs):
        return SimpleNamespace(
            status_code=200, content=pages[url].encode('utf-8'), raise_for_status=lambda: None,
        )

    monkeypatch.setattr(app.requests, 'get', get)
    records = []
    sink = app.logger.add(lambda message: records.append(message.record))
    try:
        issues = app.get_last_issues(index_url)
    finally:
        app.logger.remove(sink)

    assert [(issue.issue_url, issue.body, issue.image_url) for issue in issues] == [
        ('/db/msg/2', body, image_url),
    ]
    assert any(
        record['level'].name == 'WARNING' and '/db/msg/1' in record['message']
        for record in records
    )
    assert all(record['exception'] is None for record in records)

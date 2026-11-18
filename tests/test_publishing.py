import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendPhoto


@pytest.fixture
def bot(app, monkeypatch):
    monkeypatch.setenv('BOT_TOKEN', 'unused')
    monkeypatch.setenv('CHAT_ID', '-123')
    monkeypatch.setattr(app, 'AiohttpSession', Mock())
    monkeypatch.setattr(app.asyncio, 'sleep', AsyncMock())
    response = httpx.Response(200, content=b'image', request=httpx.Request('GET', 'https://example.test'))
    monkeypatch.setattr(app.web_client, 'get', AsyncMock(return_value=response))
    bot = Mock(send_photo=AsyncMock(), send_message=AsyncMock())
    monkeypatch.setattr(app, 'Bot', Mock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=bot))))
    return bot


def photo_issue(title, body):
    return SimpleNamespace(
        title=title,
        body=body,
        issue_url='/db/msg/1',
        image_url='https://images.astronet.ru/pubd/image.jpg',
        pub_date=datetime.date(2026, 11, 17),
        published=False,
        save=Mock(),
    )


@pytest.mark.parametrize(('title', 'body'), [
    ('Короткий заголовок', 'Короткий текст.'),
    ('Ночное небо', '🌌' * 1500),
    ('✨' * 1500, 'Обзор изображения.'),
], ids=['short', 'long-abstract', 'long-title'])
def test_photo_caption_fits_limit_and_keeps_article_link(app, bot, title, body):
    issue = photo_issue(title, body)

    asyncio.run(app.publish_issues([issue]))

    bot.send_photo.assert_awaited_once()
    kwargs = bot.send_photo.await_args.kwargs
    caption = kwargs['caption']
    assert len(caption.encode('utf-16-le')) // 2 <= 1024, 'Caption exceeds Telegram limit'
    assert caption.startswith(title[:15])
    assert any(
        entity.type == 'text_link' and entity.url == f'{app.root_url}{issue.issue_url}'
        for entity in kwargs['caption_entities']
    )
    if len(body) + len(title) < 1024:
        assert body in caption
    else:
        assert '…' in caption
    assert issue.published
    issue.save.assert_called_once()


def test_telegram_failure_keeps_issue_queued_and_logs_without_credentials(app, bot):
    issue = photo_issue('Title', 'Body')
    bot.send_photo.side_effect = TelegramNetworkError(
        method=SendPhoto(chat_id=-123, photo='placeholder'),
        message='Request failed at https://api.telegram.org/botSYNTHETIC-SECRET/sendPhoto',
    )
    records = []
    sink = app.logger.add(lambda entry: records.append(entry.record), level='ERROR')
    try:
        asyncio.run(app.publish_issues([issue]))
    finally:
        app.logger.remove(sink)

    assert not issue.published
    issue.save.assert_not_called()
    assert any(issue.issue_url in record['message'] for record in records)
    assert all('SYNTHETIC-SECRET' not in record['message'] for record in records)
    assert all(record['exception'] is None for record in records)

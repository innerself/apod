import asyncio
import datetime
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from httpx import AsyncClient, HTTPError as ClientHTTPError, HTTPStatusError, InvalidURL
from langdetect import detect
from langdetect.lang_detect_exception import ErrorCode, LangDetectException
from loguru import logger
from peewee import IntegrityError, SqliteDatabase
from requests.exceptions import HTTPError, RequestException
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from aiogram.utils.formatting import Bold, Text, TextLink, sizeof
from aiogram.utils.text_decorations import markdown_decoration

import models

logger.remove()
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time}</green> <level>{message}</level>",
    backtrace=True,
    diagnose=False
)

load_dotenv()

root_url = os.environ['ROOT_URL']
db = SqliteDatabase('db.sqlite')
web_client = AsyncClient(follow_redirects=True)
SCRAPE_TIMEOUT = 10
PHOTO_CAPTION_LIMIT = 1024
ARTICLE_LINK_LABEL = 'Подробности на astronet.ru'


class InvalidIndexError(ValueError):
    """The response did not contain the Astronet issue index."""


@dataclass
class Issue:
    title: str
    body: str
    issue_url: str
    image_url: Optional[str]
    pub_date: datetime.date


def get_image_from_issue(rel_url: str):
    full_url = f'{root_url}{rel_url}'
    try:
        full_issue_response = requests.get(full_url, timeout=SCRAPE_TIMEOUT)
        full_issue_response.raise_for_status()
    except RequestException as err:
        logger.warning(f'Issue {rel_url} media lookup failed ({type(err).__name__})')
        return None

    if not full_issue_response.content:
        return None

    issue_soup = BeautifulSoup(full_issue_response.content, 'html.parser')

    content_div = issue_soup.find(id='content')
    if content_div is None:
        return None

    img_tags = content_div.find_all('img')
    url_list = [x for x in img_tags if '://images.astronet.ru/pubd/' in (x.get('src') or '')]
    if len(url_list) == 0:
        video_tags = content_div.find_all('iframe')
        url_list = [
            x for x in video_tags
            if (x.get('src') or '').startswith('https://www.youtube.com/embed/')
        ]

    if not url_list:
        url = ''
    elif len(url_list) > 1:
        raise ValueError(f'More than one image in issue {rel_url}')
    else:
        url = url_list[0]['src']

    return url


def get_last_issues(url: str):
    response = requests.get(url, timeout=SCRAPE_TIMEOUT)
    if response.status_code != 200:
        raise HTTPError(f'{response.status_code}: {response.reason}')

    soup = BeautifulSoup(response.content, 'html.parser')

    content_div = soup.find(id='content')
    if content_div is None:
        raise InvalidIndexError('Could not find #content div')
    title_tags = content_div.find_all('p', {'class': 'title'})

    issues = []
    for tag in title_tags:
        try:
            issue_url = (
                tag.a['href']
                .removeprefix('https')
                .removeprefix('http')
                .removeprefix('://www.astronet.ru')
            )

            issue_raw_date = tag.small.b.text.split(' | ')[0]
            day, month, year = [int(x) for x in issue_raw_date.split('.')]
            issue_date = datetime.date(year, month, day)

            title = tag.b.text.strip()
            # Stop at the next issue if this entry has no abstract.
            body_tag = tag.find_next('p', class_=['abstract', 'title'])
            if body_tag is None or 'abstract' not in body_tag['class']:
                logger.warning(f'Issue {issue_url} has no abstract -> skipping')
                continue

            body_text = ' '.join(body_tag.get_text().split())
            if not body_text:
                logger.warning(f'Issue {issue_url} has an empty abstract -> skipping')
                continue

            try:
                body_lang = detect(body_text)
            except LangDetectException as err:
                if err.get_code() != ErrorCode.CantDetectError:
                    raise
                logger.warning(f'Issue {issue_url} has an undetectable abstract language -> skipping')
                continue

            if body_lang != 'ru':
                logger.warning(f'Issue {issue_url} is in "{body_lang}" language -> skipping')
                continue

            preview_image_url = tag.a.img['src'] if tag.a.img else None
            image_url = get_image_from_issue(
                issue_url
                .removeprefix('http://www.astronet.ru')
                .removeprefix('https://www.astronet.ru')
            )
            resolved_image_url = image_url or preview_image_url
            if not resolved_image_url:
                logger.warning(f'Issue {issue_url} has no image -> skipping')
                continue

            issue = Issue(
                title=title,
                body=body_text,
                issue_url=issue_url,
                image_url=resolved_image_url,
                pub_date=issue_date,
            )
            issues.append(issue)
        except Exception as err:
            logger.exception(err)

    return issues


def create_issue(issue: Issue) -> models.Issue:
    try:
        return models.Issue.create(
            title=issue.title,
            body=issue.body,
            issue_url=issue.issue_url,
            image_url=issue.image_url,
            pub_date=issue.pub_date,
        )
    except IntegrityError as err:
        if str(err) != 'UNIQUE constraint failed: issue.issue_url':
            logger.error(err)
    except Exception as err:
        logger.exception(err)


def get_unpublished():
    return models.Issue.select().where(
        (models.Issue.published == False) &
        models.Issue.image_url.is_null(False) &
        (models.Issue.image_url != '')
    )


def truncate_caption_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ''
    encoded = text.encode('utf-16-le')
    if len(encoded) <= limit * 2:
        return text
    # A slice can end in half of a supplementary character.
    prefix = encoded[:(limit - 1) * 2].decode('utf-16-le', errors='ignore')
    return f'{prefix.rstrip()}…'


def photo_caption(issue: models.Issue) -> Text:
    separator = '\n\n'
    budget = PHOTO_CAPTION_LIMIT - sizeof(ARTICLE_LINK_LABEL) - 2 * sizeof(separator)
    title = truncate_caption_text(issue.title.strip(), budget)
    body = truncate_caption_text(issue.body, budget - sizeof(title))
    return Text(
        Bold(title), separator, body, separator,
        TextLink(ARTICLE_LINK_LABEL, url=f'{root_url}{issue.issue_url}'),
    )


async def publish_issues(unpublished: List[models.Issue]):
    session = AiohttpSession(timeout=30)
    async with Bot(token=os.environ['BOT_TOKEN'], session=session) as bot:
        for issue in sorted(unpublished, key=lambda x: x.pub_date):
            try:
                if 'youtube' in issue.image_url:
                    title = markdown_decoration.quote(issue.title.strip())
                    body = markdown_decoration.quote(issue.body)
                    url = markdown_decoration.quote(f'{root_url}{issue.issue_url}')
                    label = markdown_decoration.quote(ARTICLE_LINK_LABEL)
                    caption = f'*{title}*\n\n{body}\n\n[{label}]({url})'
                    video_url = issue.image_url.replace('embed/', 'watch?v=').removesuffix('?rel=0')
                    video_url = markdown_decoration.quote(video_url)
                    await bot.send_message(
                        chat_id=os.environ['CHAT_ID'],
                        text=f'{video_url}\n\n{caption}',
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                else:
                    response = await web_client.get(issue.image_url)
                    response.raise_for_status()
                    filename = issue.image_url.rsplit('/', 1)[-1]
                    photo = BufferedInputFile(response.content, filename=filename)
                    await bot.send_photo(
                        chat_id=os.environ['CHAT_ID'],
                        photo=photo,
                        **photo_caption(issue).as_caption_kwargs(),
                    )
            except HTTPStatusError as err:
                logger.error(f'Issue {issue.issue_url} media download failed (HTTP {err.response.status_code})')
            except Exception as err:
                # Telegram transport errors may contain the credential-bearing request URL.
                logger.error(f'Issue {issue.issue_url} publishing failed ({type(err).__name__})')
            else:
                issue.published = True
                issue.save()
                logger.info(f'Published {issue.issue_url} issue')

            await asyncio.sleep(2)


def now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


async def main():
    logger.info('Started APOD Telegram publishing service')
    models.init_db()
    apod_full_url = f'{os.environ["ROOT_URL"]}{os.environ["APOD_URL"]}'
    healthcheck_url = os.environ.get('HEALTHCHECK_URL', '').strip()

    while True:
        try:
            issues = get_last_issues(apod_full_url)
        except (RequestException, InvalidIndexError) as err:
            logger.error(f'Astronet scrape failed: {err}')
            issues = None
        else:
            logger.info(f'Parsed {len(issues)} issue(s)')
            for issue in issues:
                create_issue(issue)

        if unpublished := get_unpublished():
            logger.info(f'Got {len(unpublished)} unpublished issue(s)')
            await publish_issues(unpublished)
        else:
            logger.info('No unpublished issues')

        if issues is not None and healthcheck_url:
            try:
                response = await web_client.get(url=healthcheck_url)
                response.raise_for_status()
            except HTTPStatusError as err:
                logger.error(f'Healthcheck failed (HTTP {err.response.status_code})')
            except (ClientHTTPError, InvalidURL) as err:
                logger.error(f'Healthcheck failed ({type(err).__name__})')

        await asyncio.sleep(int(os.environ['PARSING_INTERVAL_SEC']))


if __name__ == '__main__':
    # import sentry_sdk
    #
    # sentry_sdk.init(os.environ['SENTRY_DSN'])
    asyncio.run(main())

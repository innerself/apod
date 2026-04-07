import asyncio
import datetime
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from httpx import AsyncClient
from langdetect import detect
from loguru import logger
from peewee import IntegrityError, SqliteDatabase
from requests.exceptions import HTTPError, ConnectTimeout
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from aiogram.utils.text_decorations import markdown_decoration

import models

logger.remove()
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time}</green> <level>{message}</level>",
    backtrace=True,
    diagnose=True
)

load_dotenv()

root_url = os.environ['ROOT_URL']
db = SqliteDatabase('db.sqlite')
web_client = AsyncClient(follow_redirects=True)


@dataclass
class Issue:
    title: str
    body: str
    issue_url: str
    image_url: Optional[str]
    pub_date: datetime.date


def get_image_from_issue(rel_url: str):
    full_url = f'{root_url}{rel_url}'
    full_issue_response = requests.get(full_url)

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
    response = requests.get(url)
    if response.status_code != 200:
        raise HTTPError(f'{response.status_code}: {response.reason}')

    soup = BeautifulSoup(response.content, 'html.parser')

    issues = []
    retries = 5
    while True:
        try:
            content_div = soup.find(id='content')
            if content_div is None:
                raise ValueError('Could not find #content div')
            title_tags = content_div.find_all('p', {'class': 'title'})
            break
        except Exception as e:
            logger.error(e)
            retries -= 1
            if retries == 0:
                raise
        time.sleep(10)

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

            preview_image_url = tag.a.img['src'] if tag.a.img else None
            image_url = get_image_from_issue(
                issue_url
                .removeprefix('http://www.astronet.ru')
                .removeprefix('https://www.astronet.ru')
            )

            title = tag.b.text.strip()
            body_tag = tag.find_next('p', {'class': 'abstract'})
            body_text = ' '.join(body_tag.small.text.split())

            if (body_lang := detect(body_text)) != 'ru':
                logger.warning(f'Issue {issue_url} is in "{body_lang}" language -> skipping')
                continue

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


async def publish_issues(unpublished: List[models.Issue]):
    session = AiohttpSession(timeout=30)
    async with Bot(token=os.environ['BOT_TOKEN'], session=session) as bot:
        for issue in sorted(unpublished, key=lambda x: x.pub_date):
            title = markdown_decoration.quote(issue.title.strip())
            body = markdown_decoration.quote(issue.body)
            url = markdown_decoration.quote(f'{root_url}{issue.issue_url}')
            caption = f'*{title}*\n\n{body}\n\n[Подробности на astronet\\.ru]({url})'

            common_params = {
                'chat_id': os.environ['CHAT_ID'],
                'caption': caption,
                'parse_mode': ParseMode.MARKDOWN_V2,
            }

            sent = False
            if 'youtube' in issue.image_url:
                video_url = issue.image_url.replace('embed/', 'watch?v=').removesuffix('?rel=0')
                video_url = markdown_decoration.quote(video_url)
                try:
                    await bot.send_message(
                        chat_id=os.environ['CHAT_ID'],
                        text=f'{video_url}\n\n{caption}',
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    sent = True
                except Exception as err:
                    logger.exception(err)
            else:
                try:
                    response = await web_client.get(issue.image_url)
                    response.raise_for_status()
                    filename = issue.image_url.rsplit('/', 1)[-1]
                    photo = BufferedInputFile(response.content, filename=filename)
                    await bot.send_photo(photo=photo, **common_params)
                    sent = True
                except Exception as err:
                    logger.exception(err)

            if sent:
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

    while True:
        try:
            issues = get_last_issues(apod_full_url)
            logger.info(f'Parsed {len(issues)} issue(s)')
            for issue in issues:
                create_issue(issue)

            if unpublished := get_unpublished():
                logger.info(f'Got {len(unpublished)} unpublished issue(s)')
                await publish_issues(unpublished)
            else:
                logger.info(f'No unpublished issues')

            try:
                await web_client.get(url=os.environ['HEALTHCHECK_URL'])
            except Exception as err:
                logger.error(f'Healthcheck failed: {err}')
        except (HTTPError, ConnectTimeout) as err:
            logger.error(err)

        await asyncio.sleep(int(os.environ['PARSING_INTERVAL_SEC']))


if __name__ == '__main__':
    # import sentry_sdk
    #
    # sentry_sdk.init(os.environ['SENTRY_DSN'])
    asyncio.run(main())

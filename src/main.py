import asyncio
import datetime
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langdetect import detect
from loguru import logger
from peewee import IntegrityError, SqliteDatabase
from telegram import Bot
from telegram.helpers import escape_markdown

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

    img_tags = issue_soup.find(id='content').findChildren('img')
    url_list = [x for x in img_tags if x.get('src').startswith('https://images.astronet.ru/pubd/')]
    if len(url_list) == 0:
        video_tags = issue_soup.find(id='content').findChildren('iframe')
        url_list = [
            x for x in video_tags
            if x.get('src').startswith('https://www.youtube.com/embed/')
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
    soup = BeautifulSoup(response.content, 'html.parser')

    issues = []
    title_tags = soup.find(id='content').findChildren('p', {'class': 'title'})
    for tag in title_tags:
        try:
            issue_url = tag.a['href']

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

            issue = Issue(
                title=title,
                body=body_text,
                issue_url=issue_url,
                image_url=image_url or preview_image_url,
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
    return models.Issue.select().where(models.Issue.published == False)


async def publish_issues(unpublished: List[models.Issue]):
    bot = Bot(token=os.environ['BOT_TOKEN'])

    for issue in sorted(unpublished, key=lambda x: x.pub_date):
        title = escape_markdown(issue.title.strip(), version=2)
        body = escape_markdown(issue.body, version=2)
        url = escape_markdown(f'{root_url}{issue.issue_url}', version=2)
        caption = f'*{title}*\n\n{body}\n\n[Подробности на astronet\\.ru]({url})'

        common_params = {
            'chat_id': os.environ['CHAT_ID'],
            'caption': caption,
            'parse_mode': 'MarkdownV2',
        }

        if 'youtube' in issue.image_url:
            video_url = issue.image_url.replace('embed/', 'watch?v=').removesuffix('?rel=0')
            video_url = escape_markdown(video_url, version=2)
            try:
                await bot.send_message(
                    chat_id=os.environ['CHAT_ID'],
                    text=f'{video_url}\n\n{caption}',
                    parse_mode='MarkdownV2',
                )
            except Exception as err:
                logger.exception(err)
        else:
            try:
                await bot.send_photo(
                    photo=issue.image_url,
                    **common_params,
                )
            except Exception as err:
                logger.exception(err)

        issue.published = True
        issue.save()

        logger.info(f'Published {issue.issue_url} issue')

        await asyncio.sleep(2)


async def main():
    logger.info('Started APOD Telegram publishing service')
    models.init_db()
    apod_full_url = f'{os.environ["ROOT_URL"]}{os.environ["APOD_URL"]}'

    while True:
        issues = get_last_issues(apod_full_url)
        logger.info(f'Parsed {len(issues)} issue(s)')
        for issue in issues:
            create_issue(issue)

        if unpublished := get_unpublished():
            logger.info(f'Got {len(unpublished)} unpublished issue(s)')
            await publish_issues(unpublished)
        else:
            logger.info(f'No unpublished issues')

        await asyncio.sleep(int(os.environ['PARSING_INTERVAL_SEC']))


if __name__ == '__main__':
    asyncio.run(main())

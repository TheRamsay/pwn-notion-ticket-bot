import discord
from enum import Enum
from discord.abc import GuildChannel
import os
from notion_client import Client
from dotenv import load_dotenv
from datetime import datetime
from discord import Message
import re
from loguru import logger
from functools import lru_cache

load_dotenv()

DISCORD_OPEN_TICKET_CHANNEL_NAME_REGEX = re.compile(
    os.environ["DISCORD_OPEN_TICKET_CHANNEL_NAME_REGEX"]
)
DISCORD_CLOSED_TICKET_CHANNEL_NAME_REGEX = re.compile(
    os.environ["DISCORD_CLOSED_TICKET_CHANNEL_NAME_REGEX"]
)
DISCORD_TICKET_START_MESSAGE = os.environ["DISCORD_TICKET_START_MESSAGE"]
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_TICKET_BOT_ID = int(os.environ["DISCORD_TICKET_BOT_ID"])
NOTION_PARENT_DATABASE_ID = os.environ["NOTION_PARENT_DATABASE_ID"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
SAVE_FILE_PATH = os.environ["SAVE_FILE_PATH"]


URL_REGEX_COMPILED = re.compile(r"(.*?)(https?://[^\s]+)")
TICKET_TO_PAGE_ID = {}

if os.path.exists(SAVE_FILE_PATH):
    with open(SAVE_FILE_PATH, "r") as f:
        count = 0
        for line in f:
            ticket_number, page_id = line.strip().split(",")
            TICKET_TO_PAGE_ID[int(ticket_number)] = page_id
            count += 1
        logger.info(f"Loaded {count} tickets from save file")
else:
    # Create the file if it doesn't exist
    with open(SAVE_FILE_PATH, "w") as f:
        pass

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

client = discord.Client(intents=intents)
notion = Client(auth=NOTION_TOKEN)


class ChannelType(Enum):
    OPEN_TICKET = 1
    CLOSED_TICKET = 2
    UNKNOWN = 3


# Get database by ID and create all properties
def init_database():
    try:
        notion.databases.update(
            database_id=NOTION_PARENT_DATABASE_ID,
            title=[{"text": {"content": "PWN Discord Tickets 🎫"}}],
            description=[
                {
                    "text": {
                        "content": "Database for PWN Discord Tickets, created by the Discord bot. Each ticket is a page in this database. Messages in the ticket channels are appended to the page in real-time."
                    }
                }
            ],
            properties={
                "Name": {"title": {}},
                "Ticket Status": {
                    "select": {
                        "options": [
                            {"name": "Open 🔓", "color": "yellow"},
                            {"name": "Closed ✅", "color": "green"},
                        ]
                    }
                },
                "Created At": {"date": {}},
                "Closed At": {"date": {}},
                "Author": {"rich_text": {}},
                "Related Links": {"url": {}},
            },
        )
        logger.info("Initialized database with ID {NOTION_PARENT_DATABASE_ID}")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")


@lru_cache
def cached_regex(pattern: re.Pattern[str], string: str) -> re.Match[str] | None:
    return pattern.match(string)


def split_message_by_link(message: str):
    matches = URL_REGEX_COMPILED.findall(message)
    parts = []

    if not matches:
        return [message]

    for match in matches:
        if match[0] != "":
            parts.append(match[0])

        if match[1] != "":
            parts.append(match[1])

    trailing_split = message.split(parts[-1])

    if trailing_split[-1] != "":
        parts.append(trailing_split[-1])

    return parts


def get_channel_info(channel: GuildChannel) -> tuple[ChannelType, int | None]:
    if res := cached_regex(DISCORD_OPEN_TICKET_CHANNEL_NAME_REGEX, channel.name):
        return ChannelType.OPEN_TICKET, int(res.group(1))
    elif res := cached_regex(DISCORD_CLOSED_TICKET_CHANNEL_NAME_REGEX, channel.name):
        return ChannelType.CLOSED_TICKET, int(res.group(1))
    else:
        return ChannelType.UNKNOWN, None


def get_mentioned_users(message: Message) -> list[str]:
    return [mention.name for mention in message.mentions]


def handle_author_resolution(message: Message, ticket_number: int):
    mentioned_users = get_mentioned_users(message)
    if len(mentioned_users) == 0:
        logger.error(
            f"No user mentioned in message {message.content} for ticket {ticket_number}"
        )
        return

    username = mentioned_users[0]

    logger.info(
        f"Resolving ticket {ticket_number} to {username} on Notion page {TICKET_TO_PAGE_ID[ticket_number]}"
    )

    page_id = TICKET_TO_PAGE_ID[ticket_number]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "Author": {"rich_text": [{"text": {"content": username}}]},
            },
        )
    except Exception as e:
        logger.error(f"Error resolving ticket: {e}")


def handle_closed_by_resolution(message: Message, ticket_number: int):
    mentioned_users = get_mentioned_users(message)
    if len(mentioned_users) == 0:
        logger.error(
            f"No user mentioned in 'Closed by' message {message.content} for ticket {ticket_number}"
        )
        return

    username = mentioned_users[0]

    logger.info(f"Ticked {ticket_number} closed by {username}")

    page_id = TICKET_TO_PAGE_ID[ticket_number]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "Closed By": {"rich_text": [{"text": {"content": username}}]},
            },
        )
    except Exception as e:
        logger.error(f"Error in 'Closed by' resolution: {e}")


def handle_content_update(message: Message, ticket_number: int):
    message_content = message.content

    author = message.author.name

    try:
        page_id = TICKET_TO_PAGE_ID[ticket_number]

        message_parts = split_message_by_link(message_content)
        rich_texts = [
            {
                "type": "text",
                "text": {"content": f"{author}: "},
                "annotations": {"bold": True},
            }
        ]

        for part in message_parts:
            if part.startswith("http"):
                rich_texts.append(
                    {
                        "type": "text",
                        "text": {"content": part, "link": {"url": part}},
                    }
                )
            else:
                rich_texts.append({"type": "text", "text": {"content": part}})

        notion.blocks.children.append(
            block_id=page_id,
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": rich_texts,
                        "color": "default",
                    },
                },
                *[
                    {
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "external",
                            "external": {"url": attachment.url},
                        },
                    }
                    for attachment in message.attachments
                ],
            ],
        )
        logger.info(f"Updated ticket content for ticket {ticket_number}")
    except Exception as e:
        logger.error(f"Error updating ticket content: {e}")


@client.event
async def on_ready():
    logger.info(f"We have logged in as {client.user}")


@client.event
async def on_guild_channel_create(channel: GuildChannel):
    channel_type, ticket_number = get_channel_info(channel)

    if channel_type != ChannelType.OPEN_TICKET:
        return

    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_PARENT_DATABASE_ID},
            icon={"emoji": "🎫"},
            properties={
                "Name": {"title": [{"text": {"content": f"Ticket #{ticket_number}"}}]},
                "Ticket Status": {"select": {"name": "Open 🔓"}},
                "Created At": {"date": {"start": datetime.now().isoformat()}},
                "Related Links": {"url": channel.jump_url},
            },
        )

        page_id = res["id"]
        TICKET_TO_PAGE_ID[ticket_number] = page_id

        with open(SAVE_FILE_PATH, "a") as f:
            f.write(f"{ticket_number},{page_id}\n")

        logger.info(f"Created Notion page for ticket {ticket_number}: {page_id}")
    except Exception as e:
        logger.error(f"Error creating Notion page for ticket {ticket_number}: {e}")


@client.event
async def on_guild_channel_delete(channel: GuildChannel):
    channel_type, ticket_number = get_channel_info(channel)

    if channel_type != ChannelType.OPEN_TICKET:
        return

    try:
        page_id = TICKET_TO_PAGE_ID[ticket_number]

        notion.pages.update(
            page_id=page_id,
            properties={
                "Ticket Status": {"select": {"name": "Closed ✅"}},
                "Closed At": {"date": {"start": datetime.now().isoformat()}},
            },
        )
        logger.info(f"Updated Notion page for ticket {ticket_number} to closed")
    except Exception as e:
        logger.error(f"Error updating Notion page for ticket {ticket_number}: {e}")


@client.event
async def on_guild_channel_update(before: GuildChannel, after: GuildChannel):
    before_channel_type, _ = get_channel_info(before)
    if before_channel_type != ChannelType.OPEN_TICKET:
        return

    after_channel_type, ticket_number = get_channel_info(after)
    if after_channel_type != ChannelType.CLOSED_TICKET:
        return

    try:
        page_id = TICKET_TO_PAGE_ID[ticket_number]

        notion.pages.update(
            page_id=page_id,
            properties={
                "Ticket Status": {"select": {"name": "Closed ✅"}},
                "Closed At": {"date": {"start": datetime.now().isoformat()}},
            },
        )
        logger.info(f"Updated Notion page for ticket {ticket_number} to closed")
    except Exception as e:
        logger.error(f"Error updating Notion page for ticket {ticket_number}: {e}")


@client.event
async def on_message(message: Message):
    channel_type, ticket_number = get_channel_info(message.channel)

    if channel_type == ChannelType.UNKNOWN:
        return

    if (
        channel_type == ChannelType.OPEN_TICKET
        and message.author.id == DISCORD_TICKET_BOT_ID
        and DISCORD_TICKET_START_MESSAGE in message.content
    ):
        handle_author_resolution(message, ticket_number)

    # TODO: 'Closed By' username resolution
    # elif message.author.id == DISCORD_TICKET_BOT_ID and "Closed by" in message.content:
    #     handle_closed_by_resolution(message, ticket_number)

    elif (
        channel_type == ChannelType.OPEN_TICKET
        and message.author.id != DISCORD_TICKET_BOT_ID
    ):
        handle_content_update(message, ticket_number)


if __name__ == "__main__":
    if len(os.sys.argv) > 1:
        if os.sys.argv[1] == "init":
            init_database()
            os.sys.exit(0)

    logger.info("Starting the bot")
    client.run(DISCORD_BOT_TOKEN)

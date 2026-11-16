import asyncio
import io
import logging
import os
import re
import secrets
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from PIL import Image, ImageColor, ImageDraw, ImageFont
from dotenv import load_dotenv
from discord.ext import commands


load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("zap")

PREFIX = "?"
BOT_COMMANDS_CHANNEL_ID = 1551143625931493413
ENABLE_SERVER_LOGS = False
ENABLE_BOOST_ANNOUNCEMENTS = False
ENABLE_FEEDBACK_POST_PROMPTS = False
LOG_CHANNEL_ID = 1551143626875084808
BOOST_CHANNEL_ID = int(os.getenv("BOOST_CHANNEL_ID", "1551143624350113804"))
FEEDBACK_FORUM_ID = int(os.getenv("FEEDBACK_FORUM_ID", "1551143626422358108"))
STAFF_MANAGER_ROLE_ID = int(os.getenv("STAFF_MANAGER_ROLE_ID", "1551143623440076864"))
STAFF_FOUNDER_ROLE_ID = int(os.getenv("STAFF_FOUNDER_ROLE_ID", "1551143623440076869"))
STAFF_DEVELOPER_ROLE_ID = int(os.getenv("STAFF_DEVELOPER_ROLE_ID", "1551143623440076866"))
STAFF_TEAM_ROLE_ID = int(os.getenv("STAFF_TEAM_ROLE_ID", "1551143623427620911"))
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60
DURATION_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 60 * 60,
    "hr": 60 * 60,
    "hrs": 60 * 60,
    "hour": 60 * 60,
    "hours": 60 * 60,
    "d": 24 * 60 * 60,
    "day": 24 * 60 * 60,
    "days": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "weeks": 7 * 24 * 60 * 60,
    "mo": 30 * 24 * 60 * 60,
    "month": 30 * 24 * 60 * 60,
    "months": 30 * 24 * 60 * 60,
    "y": 365 * 24 * 60 * 60,
    "yr": 365 * 24 * 60 * 60,
    "year": 365 * 24 * 60 * 60,
    "years": 365 * 24 * 60 * 60,
}
DURATION_TOKEN = re.compile(r"(\d+)\s*([a-z]+)", re.IGNORECASE)
COMMAND_RATE_LIMIT = 5
COMMAND_RATE_WINDOW = 5.0
SIMULATION_AMOUNTS = {
    "small": (5500, (100_000, 125_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000)),
    "medium": (2000, (500_000, 750_000, 1_000_000)),
    "large": (1500, (1_250_000, 1_500_000, 2_000_000, 2_500_000, 5_000_000)),
    "mega": (700, (7_500_000, 10_000_000)),
    "ultra": (250, (15_000_000, 20_000_000, 25_000_000)),
    "legendary": (50, (50_000_000,)),
}
SIMULATION_THEMES = {
    "pink": {"accent": "#FF0088", "gradient": "#FF0082"},
    "red": {"accent": "#FF0000", "gradient": "#FF0A00"},
}
DONATION_STYLES = {
    "red": {"accent": "#FF0000", "glow": "#FF0A00", "glow_start": 230, "glow_alpha": 138},
    "pink": {"accent": "#FF0088", "glow": "#FF0082", "glow_start": 480, "glow_alpha": 69},
    "purple": {"accent": "#FF00F7", "glow": None, "glow_start": 704, "glow_alpha": 0},
}
DONATION_AMOUNT_ICON_FILES = {
    "100k": "donation-100k.png",
    "1m": "donation-1m.png",
    "10m": "donation-10m.png",
}
ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets"
MONTSERRAT_FONT_PATH = ASSET_DIRECTORY / "Montserrat-Variable.ttf"
SIMULATION_CATEGORIES = {
    "diamond": "🔹",
    "trophy": "🏆",
    "heart": "💗",
}
DONATION_AMOUNT_EMOJIS = {
    "100k": "nuke",
    "1m": "smite",
    "10m": "starfalllol",
}
ROBLOX_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_]{1,18}[A-Za-z0-9])$")
AVATAR_CACHE_SECONDS = 6 * 60 * 60
AVATAR_FAILURE_CACHE_SECONDS = 10 * 60
DEFAULT_SIMULATION_TIMEZONE = "America/New_York"


def validate_roblox_username(username: str) -> str:
    username = username.strip()
    if not ROBLOX_USERNAME.fullmatch(username) or "__" in username:
        raise ValueError(
            "Roblox usernames must be 3–20 letters, numbers, or single underscores, "
            "and cannot start or end with an underscore."
        )
    return username


def choose_donation_amount(
    weights: dict[str, int] | None = None,
    randbelow=None,
) -> int:
    weights = weights or {name: values[0] for name, values in SIMULATION_AMOUNTS.items()}
    randbelow = randbelow or secrets.randbelow
    valid_weights = {
        name: max(0, int(weights.get(name, 0)))
        for name in SIMULATION_AMOUNTS
    }
    total_weight = sum(valid_weights.values())
    if total_weight <= 0:
        raise ValueError("At least one donation amount weight must be greater than zero.")
    choice = randbelow(total_weight)
    for name, weight in valid_weights.items():
        if choice < weight:
            amounts = SIMULATION_AMOUNTS[name][1]
            return amounts[randbelow(len(amounts))]
        choice -= weight
    raise RuntimeError("Donation amount selection exceeded its configured weights.")


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    try:
        font = ImageFont.truetype(str(MONTSERRAT_FONT_PATH), size)
    except OSError:
        font = None
    if font is not None:
        font.set_variation_by_name("Bold" if bold else "Regular")
        return font

    candidates = (
        ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if bold
        else ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    initial_size: int,
    *,
    bold: bool = True,
) -> ImageFont.ImageFont:
    size = initial_size
    font = _load_font(size, bold)
    while size > 12 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
        size -= 2
        font = _load_font(size, bold)
    return font


def donation_style_for_amount(amount: int) -> str:
    if amount >= 5_000_000:
        return "red"
    if amount >= 500_000:
        return "pink"
    return "purple"


def donation_icon_path_for_amount(amount: int) -> Path:
    if amount >= 10_000_000:
        icon_name = DONATION_AMOUNT_ICON_FILES["10m"]
    elif amount >= 1_000_000:
        icon_name = DONATION_AMOUNT_ICON_FILES["1m"]
    else:
        icon_name = DONATION_AMOUNT_ICON_FILES["100k"]
    return ASSET_DIRECTORY / icon_name


def donation_emoji_for_amount(amount: int, guild_id: int) -> str:
    if amount >= 10_000_000:
        emoji_name = DONATION_AMOUNT_EMOJIS["10m"]
    elif amount >= 1_000_000:
        emoji_name = DONATION_AMOUNT_EMOJIS["1m"]
    else:
        emoji_name = DONATION_AMOUNT_EMOJIS["100k"]

    guild = bot.get_guild(guild_id)
    if guild is None:
        log.warning("Cannot find guild %s to retrieve donation emoji %s", guild_id, emoji_name)
        return f":{emoji_name}:"
    emoji = discord.utils.get(guild.emojis, name=emoji_name)
    if emoji is None:
        log.warning("Guild %s does not have the donation emoji %s", guild_id, emoji_name)
        return f":{emoji_name}:"
    return str(emoji)


def robux_emoji_for_guild(guild_id: int | None) -> str:
    if guild_id is None:
        return ":robux:"
    guild = bot.get_guild(guild_id)
    if guild is None:
        log.warning("Cannot find guild %s to retrieve the Robux emoji", guild_id)
        return ":robux:"
    emoji = discord.utils.get(guild.emojis, name="robux")
    if emoji is None:
        log.warning("Guild %s does not have the Robux emoji", guild_id)
        return ":robux:"
    return str(emoji)


def render_donation_card(
    donor: str,
    receiver: str,
    amount: int,
    donor_avatar: bytes | None = None,
    receiver_avatar: bytes | None = None,
) -> bytes:
    if amount < 100_000:
        raise ValueError("Simulated donations must be at least 100,000 Robux.")

    style = DONATION_STYLES[donation_style_for_amount(amount)]
    accent = style["accent"]
    scale = 2
    width, height = 2816, 768
    canvas_width, canvas_height = width * scale, height * scale
    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

    if style["glow"]:
        glow = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        red, green, blue = ImageColor.getrgb(str(style["glow"]))
        start = int(style["glow_start"])
        max_alpha = int(style["glow_alpha"])
        for y in range(start, height):
            alpha = round(max_alpha * (y - start) / (height - start))
            glow_draw.line(
                (0, y * scale, (width - 1) * scale, y * scale),
                fill=(red, green, blue, alpha),
                width=scale,
            )
        image.alpha_composite(glow)

    draw = ImageDraw.Draw(image)

    def centered_text(
        text: str,
        font: ImageFont.ImageFont,
        center_x: float,
        y: int,
        *,
        fill: str,
        width_scale: float = 1,
        height_scale: float = 1,
        stroke_width: int = 0,
        stroke_fill: str = "#000000",
    ) -> tuple[int, int, int, int]:
        stroke_width *= scale
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        text_layer = Image.new(
            "RGBA",
            (bounds[2] - bounds[0], bounds[3] - bounds[1]),
            (0, 0, 0, 0),
        )
        ImageDraw.Draw(text_layer).text(
            (-bounds[0], -bounds[1]),
            text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        if width_scale != 1:
            text_layer = text_layer.resize(
                (
                    round(text_layer.width * width_scale),
                    round(text_layer.height * height_scale),
                ),
                Image.Resampling.LANCZOS,
            )
        elif height_scale != 1:
            text_layer = text_layer.resize(
                (text_layer.width, round(text_layer.height * height_scale)),
                Image.Resampling.LANCZOS,
            )
        x = round(center_x * scale - text_layer.width / 2)
        y *= scale
        image.alpha_composite(text_layer, (x, y))
        return x, y, x + text_layer.width, y + text_layer.height

    def draw_avatar(
        avatar_bytes: bytes | None, center_x: int, username: str
    ) -> None:
        center_x *= scale
        center_y = 300 * scale
        outer_radius = 200
        draw.ellipse(
            (
                center_x - outer_radius * scale,
                center_y - outer_radius * scale,
                center_x + outer_radius * scale,
                center_y + outer_radius * scale,
            ),
            fill="#09090B",
        )
        draw.ellipse(
            (
                center_x - 195 * scale,
                center_y - 195 * scale,
                center_x + 195 * scale,
                center_y + 195 * scale,
            ),
            fill=accent,
        )
        draw.ellipse(
            (
                center_x - 180 * scale,
                center_y - 180 * scale,
                center_x + 180 * scale,
                center_y + 180 * scale,
            ),
            fill="#101014",
        )

        avatar_size = 352 * scale
        avatar = Image.new("RGBA", (avatar_size, avatar_size), "#464650")
        if avatar_bytes:
            try:
                with Image.open(io.BytesIO(avatar_bytes)) as source:
                    source = source.convert("RGBA")
                    crop_size = min(source.width, source.height)
                    left = (source.width - crop_size) // 2
                    top = (source.height - crop_size) // 2
                    source = source.crop(
                        (left, top, left + crop_size, top + crop_size)
                    ).resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
                    avatar.alpha_composite(source)
            except (OSError, ValueError):
                log.warning("Using fallback avatar for simulated user %s", username)
        mask = Image.new("L", (avatar_size, avatar_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)
        image.paste(
            avatar,
            (center_x - avatar_size // 2, center_y - avatar_size // 2),
            mask,
        )

    draw_avatar(donor_avatar, 540, donor)
    draw_avatar(receiver_avatar, 2298, receiver)

    amount_text = f"{amount:,}"
    amount_stroke_width = 5 * scale
    amount_top = 88
    gap = 16 * scale

    with Image.open(donation_icon_path_for_amount(amount)) as source:
        icon_source = source.convert("RGBA")
        artwork_bounds = icon_source.getchannel("A").point(
            lambda alpha: 255 if alpha >= 8 else 0
        ).getbbox()
        if artwork_bounds is None:
            raise ValueError("The donation Robux icon has no visible artwork.")
        icon_source = icon_source.crop(artwork_bounds)
        icon_aspect_ratio = icon_source.width / icon_source.height

    max_group_width = (width - (540 + 200) - (width - 2298 + 200)) * scale
    font_size = 214
    while True:
        amount_font = _load_font(font_size * scale, True)
        amount_bounds = draw.textbbox(
            (0, 0),
            amount_text,
            font=amount_font,
            stroke_width=amount_stroke_width,
        )
        amount_fill_bounds = draw.textbbox((0, 0), amount_text, font=amount_font)
        amount_width = amount_bounds[2] - amount_bounds[0]
        amount_height = amount_bounds[3] - amount_bounds[1]
        icon_size = amount_fill_bounds[3] - amount_fill_bounds[1]
        icon_width = round(icon_size * icon_aspect_ratio)
        if amount_width + gap + icon_width <= max_group_width:
            break
        font_size -= 2
        if font_size <= 12:
            raise ValueError("The donation amount cannot fit between the avatars.")

    icon = icon_source.resize((icon_width, icon_size), Image.Resampling.LANCZOS)
    group_width = icon_width + gap + amount_width
    group_left = (canvas_width - group_width) / 2
    icon_left = round(group_left)
    amount_left = icon_left + icon_width + gap
    amount_layer = Image.new(
        "RGBA",
        (round(amount_width), amount_height),
        (0, 0, 0, 0),
    )
    amount_draw = ImageDraw.Draw(amount_layer)
    amount_draw.text(
        (-amount_bounds[0], -amount_bounds[1]),
        amount_text,
        font=amount_font,
        fill=accent,
        stroke_width=amount_stroke_width,
        stroke_fill="#000000",
    )
    image.alpha_composite(amount_layer, (amount_left, amount_top * scale))
    icon_top = amount_top * scale + amount_fill_bounds[1] - amount_bounds[1]
    image.alpha_composite(icon, (icon_left, icon_top))

    centered_text(
        "donated to",
        _load_font(132 * scale, True),
        width / 2,
        365,
        fill="#FFFFFF",
        width_scale=1.05,
        height_scale=1.12,
        stroke_width=4,
    )

    def draw_username(username: str, center_x: int) -> None:
        name_font = _fit_font(draw, username, 700 * scale, 58 * scale, bold=True)
        name_width = draw.textlength(username, font=name_font)
        at_font = _load_font(58 * scale, True)
        at_width = draw.textlength("@", font=at_font)
        group_width = at_width + name_width
        left = round(center_x * scale - group_width / 2)
        top = 565
        draw.text(
            (left, (top + 8) * scale),
            "@",
            font=at_font,
            fill="#FFFFFF",
            stroke_width=5 * scale,
            stroke_fill="#000000",
        )
        draw.text(
            (left + at_width, (top + 8) * scale),
            username,
            font=name_font,
            fill="#FFFFFF",
            stroke_width=6 * scale,
            stroke_fill="#000000",
        )

    draw_username(donor, 540)
    draw_username(receiver, 2298)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def parse_duration(value: str) -> int:
    """Parse durations such as 30m, 2h, or 1h30m into seconds."""
    value = value.strip().lower()
    if not value:
        raise ValueError("Enter a duration such as `30m`, `2h`, or `1h30m`.")

    total = 0
    position = 0
    for match in DURATION_TOKEN.finditer(value):
        if value[position:match.start()].strip(" ,"):
            raise ValueError("Use a duration such as `30m`, `2h`, or `1h30m`.")
        unit = match.group(2)
        if unit not in DURATION_UNITS:
            raise ValueError(f"`{unit}` isn't a supported duration unit.")
        total += int(match.group(1)) * DURATION_UNITS[unit]
        position = match.end()

    if position == 0 or value[position:].strip(" ,") or total <= 0:
        raise ValueError("Enter a duration greater than zero, such as `30m`.")
    return total


def level_for_boosts(boosts: int) -> int:
    if boosts >= 14:
        return 3
    if boosts >= 7:
        return 2
    if boosts >= 2:
        return 1
    return 0


def format_elapsed(seconds: int) -> str:
    days, seconds = divmod(seconds, 24 * 60 * 60)
    hours, seconds = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(seconds, 60)
    parts = [
        (days, "day"),
        (hours, "hour"),
        (minutes, "minute"),
        (seconds, "second"),
    ]
    result = [f"{value} {unit}{'' if value == 1 else 's'}" for value, unit in parts if value]
    return " ".join(result[:2]) or "less than a second"


def get_command_prefix(_bot: commands.Bot, message: discord.Message) -> str:
    if re.match(r"^\.say(?:\s|$)", message.content, re.IGNORECASE):
        return "."
    return PREFIX


class CommandRateLimiter:
    def __init__(self) -> None:
        self.command_times: dict[int, deque[float]] = {}

    def allow(self, user_id: int, now: float | None = None) -> bool:
        current_time = time.monotonic() if now is None else now
        command_times = self.command_times.setdefault(user_id, deque())
        while command_times and current_time - command_times[0] >= COMMAND_RATE_WINDOW:
            command_times.popleft()
        if len(command_times) >= COMMAND_RATE_LIMIT:
            return False
        command_times.append(current_time)
        return True


class ZapUtilities(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=get_command_prefix, intents=intents)
        db_path = os.getenv(
            "ZAP_DB_PATH",
            os.getenv("PATE_DB_PATH", "pate_utilities.sqlite3"),
        )
        self.db = sqlite3.connect(db_path)
        self.boost_batches: dict[int, list[discord.Member]] = {}
        self.boost_tasks: dict[int, asyncio.Task] = {}
        self.command_rate_limiter = CommandRateLimiter()
        self.expiry_task: asyncio.Task | None = None
        self.simulation_tasks: dict[int, asyncio.Task] = {}
        self.http_session: aiohttp.ClientSession | None = None
        self.avatar_cache: dict[str, tuple[float, bytes | None]] = {}
        self._initialize_database()

    def _initialize_database(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS temp_bans (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS afk (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS economy (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 1000,
                last_daily INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS simulation_settings (
                guild_id INTEGER PRIMARY KEY,
                auto_enabled INTEGER NOT NULL DEFAULT 0,
                channel_id INTEGER,
                min_interval_seconds INTEGER NOT NULL DEFAULT 8,
                max_interval_seconds INTEGER NOT NULL DEFAULT 8,
                max_per_hour INTEGER NOT NULL DEFAULT 50,
                manual_enabled INTEGER NOT NULL DEFAULT 1,
                timezone TEXT NOT NULL DEFAULT 'America/New_York',
                weight_small INTEGER NOT NULL DEFAULT 5500,
                weight_medium INTEGER NOT NULL DEFAULT 2000,
                weight_large INTEGER NOT NULL DEFAULT 1500,
                weight_mega INTEGER NOT NULL DEFAULT 700,
                weight_ultra INTEGER NOT NULL DEFAULT 250,
                weight_legendary INTEGER NOT NULL DEFAULT 50
            );
            CREATE TABLE IF NOT EXISTS simulation_users (
                guild_id INTEGER NOT NULL,
                username TEXT NOT NULL COLLATE NOCASE,
                pool TEXT NOT NULL CHECK (pool IN ('donors', 'receivers')),
                enabled INTEGER NOT NULL DEFAULT 1,
                weight INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, username, pool)
            );
            CREATE TABLE IF NOT EXISTS simulation_themes (
                guild_id INTEGER NOT NULL,
                theme TEXT NOT NULL CHECK (theme IN ('pink', 'red')),
                enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, theme)
            );
            CREATE TABLE IF NOT EXISTS simulation_categories (
                guild_id INTEGER NOT NULL,
                category TEXT NOT NULL CHECK (category IN ('diamond', 'trophy', 'heart')),
                enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, category)
            );
            CREATE TABLE IF NOT EXISTS simulation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                donor TEXT NOT NULL,
                receiver TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount >= 100000),
                theme TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('manual', 'automatic')),
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS simulation_events_recent
                ON simulation_events (guild_id, created_at);
            """
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY)"
        )
        timezone_migration = "simulation_timezone_default_new_york"
        if self.db.execute(
            "SELECT 1 FROM app_migrations WHERE name = ?",
            (timezone_migration,),
        ).fetchone() is None:
            self.db.execute(
                "UPDATE simulation_settings SET timezone = ? WHERE timezone = 'UTC'",
                (DEFAULT_SIMULATION_TIMEZONE,),
            )
            self.db.execute(
                "INSERT INTO app_migrations (name) VALUES (?)",
                (timezone_migration,),
            )
        self.db.commit()

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12)
        )
        self.expiry_task = asyncio.create_task(self._expire_temporary_bans())
        guild_id = os.getenv("ZAP_GUILD_ID")
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
            except ValueError as error:
                raise RuntimeError("ZAP_GUILD_ID must be a numeric Discord server ID.") from error
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(
                "Synced %s Zap slash commands to test server %s.",
                len(synced),
                guild.id,
            )
        else:
            synced = await self.tree.sync()
            log.info(
                "Synced %s Zap global slash commands. Discord may take up to an hour "
                "to display global commands.",
                len(synced),
            )

    async def close(self) -> None:
        if self.expiry_task:
            self.expiry_task.cancel()
        for task in self.boost_tasks.values():
            task.cancel()
        for task in self.simulation_tasks.values():
            task.cancel()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        self.db.close()
        await super().close()

    def _ensure_account(self, guild_id: int, user_id: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO economy (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        self.db.commit()

    async def _expire_temporary_bans(self) -> None:
        while not self.is_closed():
            now = int(time.time())
            expired = self.db.execute(
                "SELECT guild_id, user_id FROM temp_bans WHERE expires_at <= ?",
                (now,),
            ).fetchall()
            for guild_id, user_id in expired:
                guild = self.get_guild(guild_id)
                if guild is None:
                    continue
                try:
                    await guild.unban(
                        discord.Object(id=user_id),
                        reason="Temporary ban expired",
                    )
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    log.exception(
                        "Could not expire temporary ban for user %s in guild %s",
                        user_id,
                        guild_id,
                    )
                    continue
                except discord.HTTPException:
                    log.exception(
                        "Discord API error while expiring ban for user %s in guild %s",
                        user_id,
                        guild_id,
                    )
                    continue
                self.db.execute(
                    "DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            self.db.commit()
            await asyncio.sleep(15)

    async def _announce_boosts(self, guild: discord.Guild) -> None:
        if not ENABLE_BOOST_ANNOUNCEMENTS:
            return
        await asyncio.sleep(3)
        members = self.boost_batches.pop(guild.id, [])
        self.boost_tasks.pop(guild.id, None)
        if not members:
            return

        current_count = guild.premium_subscription_count or 0
        previous_count = max(0, current_count - len(members))
        previous_level = level_for_boosts(previous_count)
        current_level = level_for_boosts(current_count)
        names = ", ".join(member.mention for member in members)
        boost_word = "boost" if len(members) == 1 else "boosts"
        message = (
            f"Thanks for the {boost_word}, {names}! We got **{len(members)}** "
            f"{boost_word}.\n"
            f"Server boosts: **{current_count}** | Level: **{current_level}**"
        )
        if previous_level > 0:
            message += f" (was level **{previous_level}**)"

        next_tier = next(
            ((count, level) for count, level in ((2, 1), (7, 2), (14, 3))
             if count > current_count),
            None,
        )
        if next_tier:
            message += (
                f" | **{next_tier[0] - current_count}** more for level "
                f"**{next_tier[1]}**"
            )
        else:
            message += " | Max boost level"

        channel = guild.get_channel(BOOST_CHANNEL_ID)
        if channel is None:
            log.error("Boost announcement channel %s was not found", BOOST_CHANNEL_ID)
            return
        try:
            await channel.send(message)
        except discord.Forbidden:
            log.exception("Missing permission to send boost announcement")
        except discord.HTTPException:
            log.exception("Could not send boost announcement")


bot = ZapUtilities()


def ensure_simulation_settings(guild_id: int) -> None:
    bot.db.execute(
        """
        INSERT OR IGNORE INTO simulation_settings (guild_id, timezone)
        VALUES (?, ?)
        """,
        (guild_id, DEFAULT_SIMULATION_TIMEZONE),
    )
    bot.db.executemany(
        "INSERT OR IGNORE INTO simulation_themes (guild_id, theme) VALUES (?, ?)",
        ((guild_id, theme) for theme in SIMULATION_THEMES),
    )
    bot.db.executemany(
        "INSERT OR IGNORE INTO simulation_categories (guild_id, category) VALUES (?, ?)",
        ((guild_id, category) for category in SIMULATION_CATEGORIES),
    )
    bot.db.commit()


def get_simulation_settings(guild_id: int) -> dict[str, int | str | None]:
    ensure_simulation_settings(guild_id)
    row = bot.db.execute(
        """
        SELECT auto_enabled, channel_id, min_interval_seconds, max_interval_seconds,
               max_per_hour,
               manual_enabled, timezone, weight_small, weight_medium,
               weight_large, weight_mega, weight_ultra, weight_legendary
        FROM simulation_settings WHERE guild_id = ?
        """,
        (guild_id,),
    ).fetchone()
    keys = (
        "auto_enabled", "channel_id", "min_interval_seconds",
        "max_interval_seconds", "max_per_hour",
        "manual_enabled", "timezone", "weight_small", "weight_medium",
        "weight_large", "weight_mega", "weight_ultra", "weight_legendary",
    )
    return dict(zip(keys, row))


def get_simulation_weights(settings: dict[str, int | str | None]) -> dict[str, int]:
    return {
        name: int(settings[f"weight_{name}"] or 0)
        for name in SIMULATION_AMOUNTS
    }


def enabled_simulation_themes(guild_id: int) -> list[str]:
    ensure_simulation_settings(guild_id)
    return [
        row[0]
        for row in bot.db.execute(
            "SELECT theme FROM simulation_themes WHERE guild_id = ? AND enabled = 1",
            (guild_id,),
        ).fetchall()
    ]


def enabled_simulation_categories(guild_id: int) -> list[str]:
    ensure_simulation_settings(guild_id)
    return [
        row[0]
        for row in bot.db.execute(
            "SELECT category FROM simulation_categories WHERE guild_id = ? AND enabled = 1",
            (guild_id,),
        ).fetchall()
    ]


def _configured_users(guild_id: int, pool: str) -> list[tuple[str, int]]:
    return [
        (username, weight)
        for username, weight in bot.db.execute(
            """
            SELECT username, weight FROM simulation_users
            WHERE guild_id = ? AND pool = ? AND enabled = 1
            """,
            (guild_id, pool),
        ).fetchall()
    ]


def choose_demo_users(
    guild_id: int,
    donor: str | None = None,
    receiver: str | None = None,
) -> tuple[str, str]:
    donors = [(donor, 1)] if donor else _configured_users(guild_id, "donors")
    receivers = [(receiver, 1)] if receiver else _configured_users(guild_id, "receivers")
    if not donors:
        donors = [
            (f"DemoDonor{secrets.randbelow(100_000_000):08d}", 1)
            for _ in range(20)
        ]
    if not receivers:
        receivers = [
            (f"DemoGuest{secrets.randbelow(100_000_000):08d}", 1)
            for _ in range(20)
        ]

    all_pairs = [
        (donor_name, receiver_name, donor_weight * receiver_weight)
        for donor_name, donor_weight in donors
        for receiver_name, receiver_weight in receivers
        if donor_name.casefold() != receiver_name.casefold()
    ]
    if not all_pairs:
        raise ValueError(
            "Donor and receiver must be different. Configure at least one valid pair."
        )

    recent_pairs = {
        (donor_name.casefold(), receiver_name.casefold())
        for donor_name, receiver_name in bot.db.execute(
            """
            SELECT donor, receiver FROM simulation_events
            WHERE guild_id = ? ORDER BY created_at DESC, id DESC LIMIT 100
            """,
            (guild_id,),
        ).fetchall()
    }
    fresh_pairs = [
        pair for pair in all_pairs
        if (pair[0].casefold(), pair[1].casefold()) not in recent_pairs
    ]
    candidates = fresh_pairs or all_pairs
    total_weight = sum(pair[2] for pair in candidates)
    selection = secrets.randbelow(total_weight)
    for donor_name, receiver_name, weight in candidates:
        if selection < weight:
            return donor_name, receiver_name
        selection -= weight
    raise RuntimeError("Demo username selection exceeded its configured weights.")


async def fetch_roblox_avatar(username: str) -> bytes | None:
    now = time.monotonic()
    cached = bot.avatar_cache.get(username.casefold())
    if cached and cached[0] > now:
        return cached[1]
    if bot.http_session is None or bot.http_session.closed:
        raise RuntimeError("Roblox avatar requests are unavailable before bot startup.")

    try:
        async with bot.http_session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
        ) as response:
            response.raise_for_status()
            profile = await response.json()
        users = profile.get("data", [])
        if not users:
            bot.avatar_cache[username.casefold()] = (
                now + AVATAR_FAILURE_CACHE_SECONDS,
                None,
            )
            log.info("No Roblox profile found for demo username %s; using fallback.", username)
            return None

        async with bot.http_session.get(
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            params={
                "userIds": str(users[0]["id"]),
                "size": "150x150",
                "format": "Png",
                "isCircular": "false",
            },
        ) as response:
            response.raise_for_status()
            thumbnail = await response.json()
        image_url = thumbnail.get("data", [{}])[0].get("imageUrl")
        if not image_url:
            raise ValueError("Roblox returned no avatar thumbnail URL.")

        async with bot.http_session.get(image_url) as response:
            response.raise_for_status()
            if not response.content_type.startswith("image/"):
                raise ValueError("Roblox avatar endpoint returned a non-image response.")
            image_bytes = await response.read()
        bot.avatar_cache[username.casefold()] = (now + AVATAR_CACHE_SECONDS, image_bytes)
        return image_bytes
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as error:
        log.warning("Could not retrieve Roblox avatar for %s: %s", username, error)
        bot.avatar_cache[username.casefold()] = (
            now + AVATAR_FAILURE_CACHE_SECONDS,
            None,
        )
        return None


async def create_simulation_file(
    guild_id: int,
    donor: str | None = None,
    receiver: str | None = None,
    amount: int | None = None,
) -> tuple[discord.File, dict[str, int | str | None]]:
    if amount is not None and amount < 100_000:
        raise ValueError("Simulated donations must be at least 100,000 Robux.")
    settings = get_simulation_settings(guild_id)
    donor_name, receiver_name = choose_demo_users(guild_id, donor, receiver)
    selected_amount = amount or choose_donation_amount(get_simulation_weights(settings))
    theme = donation_style_for_amount(selected_amount)
    categories = enabled_simulation_categories(guild_id)
    if not categories:
        raise ValueError("Enable at least one notification icon category.")
    category = secrets.choice(categories)
    donor_avatar, receiver_avatar = await asyncio.gather(
        fetch_roblox_avatar(donor_name),
        fetch_roblox_avatar(receiver_name),
    )
    try:
        local_time = datetime.now(ZoneInfo(str(settings["timezone"])))
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            "The configured timezone is invalid. Set it with `/simulation-settings`."
        ) from error
    card_bytes = render_donation_card(
        donor_name,
        receiver_name,
        selected_amount,
        donor_avatar,
        receiver_avatar,
    )
    file = discord.File(io.BytesIO(card_bytes), filename="zap-simulation.png")
    return file, {
        "donor": donor_name,
        "receiver": receiver_name,
        "amount": selected_amount,
        "theme": theme,
        "category": category,
        "created_at": int(time.time()),
        "display_time": local_time.strftime("%m/%d/%Y %I:%M %p"),
        "auto_enabled": settings["auto_enabled"],
    }


def simulation_message(
    event: dict[str, int | str | None],
    guild_id: int | None = None,
) -> str:
    emoji = (
        SIMULATION_CATEGORIES[str(event["category"])]
        if guild_id is None
        else donation_emoji_for_amount(int(event["amount"]), guild_id)
    )
    robux_emoji = robux_emoji_for_guild(guild_id)
    return (
        f"{emoji} `@{event['donor']}` donated "
        f"**{robux_emoji} {int(event['amount']):,}** to `@{event['receiver']}`"
    )


def simulation_embed(
    event: dict[str, int | str | None],
    filename: str,
) -> discord.Embed:
    style = DONATION_STYLES[donation_style_for_amount(int(event["amount"]))]
    embed = discord.Embed(
        color=int(str(style["accent"]).lstrip("#"), 16),
    )
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Donated on • {event['display_time']}")
    return embed


async def post_simulation(
    channel: discord.abc.Messageable,
    file: discord.File,
    event: dict[str, int | str | None],
    guild_id: int,
    source: str,
) -> None:
    await channel.send(
        content=simulation_message(event, guild_id),
        file=file,
        embed=simulation_embed(event, file.filename),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    bot.db.execute(
        """
        INSERT INTO simulation_events
            (guild_id, donor, receiver, amount, theme, category, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            event["donor"],
            event["receiver"],
            event["amount"],
            event["theme"],
            event["category"],
            source,
            event["created_at"],
        ),
    )
    bot.db.commit()


async def _run_automatic_simulations(guild_id: int) -> None:
    try:
        while not bot.is_closed():
            settings = get_simulation_settings(guild_id)
            if not settings["auto_enabled"]:
                return
            min_interval = int(settings["min_interval_seconds"])
            max_interval = int(settings["max_interval_seconds"])
            interval = min_interval + secrets.randbelow(max_interval - min_interval + 1)
            since = int(time.time()) - 60 * 60
            sent_this_hour = bot.db.execute(
                """
                SELECT COUNT(*) FROM simulation_events
                WHERE guild_id = ? AND source = 'automatic' AND created_at >= ?
                """,
                (guild_id, since),
            ).fetchone()[0]
            if sent_this_hour >= int(settings["max_per_hour"]):
                await asyncio.sleep(min(interval, 60))
                continue

            channel_id = settings["channel_id"]
            channel = bot.get_channel(channel_id) if channel_id else None
            if channel is None and channel_id:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    log.exception(
                        "Could not access Zap simulation channel %s in guild %s",
                        channel_id,
                        guild_id,
                    )
                    bot.db.execute(
                        "UPDATE simulation_settings SET auto_enabled = 0 WHERE guild_id = ?",
                        (guild_id,),
                    )
                    bot.db.commit()
                    return
            if channel is None or not hasattr(channel, "send"):
                log.error("Automatic simulation has no usable channel for guild %s", guild_id)
                bot.db.execute(
                    "UPDATE simulation_settings SET auto_enabled = 0 WHERE guild_id = ?",
                    (guild_id,),
                )
                bot.db.commit()
                return

            try:
                file, event = await create_simulation_file(guild_id)
                await post_simulation(channel, file, event, guild_id, "automatic")
            except (discord.Forbidden, discord.NotFound):
                log.exception("Zap cannot post in the configured channel for guild %s", guild_id)
                bot.db.execute(
                    "UPDATE simulation_settings SET auto_enabled = 0 WHERE guild_id = ?",
                    (guild_id,),
                )
                bot.db.commit()
                return
            except (ValueError, discord.HTTPException):
                log.exception("Automatic Zap simulation failed in guild %s", guild_id)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    finally:
        bot.simulation_tasks.pop(guild_id, None)


@bot.tree.command(
    name="simulated-donation",
    description="Post a simulated donation card.",
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.cooldown(1, 10.0)
async def simulated_donation(
    interaction: discord.Interaction,
    amount: discord.app_commands.Range[int, 100_000, 50_000_000] | None = None,
    donor: str | None = None,
    receiver: str | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command is only available in a server.", ephemeral=True
        )
        return
    settings = get_simulation_settings(interaction.guild.id)
    if not settings["manual_enabled"]:
        await interaction.response.send_message(
            "Manual simulations are disabled by this server's administrators.",
            ephemeral=True,
        )
        return
    if (donor or receiver) and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "Only the server owner can provide custom demo usernames.",
            ephemeral=True,
        )
        return
    try:
        donor = validate_roblox_username(donor) if donor else None
        receiver = validate_roblox_username(receiver) if receiver else None
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        file, event = await create_simulation_file(
            interaction.guild.id, donor, receiver, amount
        )
        if interaction.channel is None:
            raise ValueError("This channel is unavailable for sending a simulation.")
        await post_simulation(
            interaction.channel, file, event, interaction.guild.id, "manual"
        )
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    await interaction.followup.send("Simulated donation posted.", ephemeral=True)


simulation_user_group = discord.app_commands.Group(
    name="simulation-user",
    description="Manage fictional or administrator-configured demo usernames.",
)


@simulation_user_group.command(name="add", description="Add a demo username.")
@discord.app_commands.choices(
    pool=[
        discord.app_commands.Choice(name="Donors", value="donors"),
        discord.app_commands.Choice(name="Receivers", value="receivers"),
    ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_user_add(
    interaction: discord.Interaction,
    pool: discord.app_commands.Choice[str],
    username: str,
    weight: discord.app_commands.Range[int, 1, 100] = 1,
) -> None:
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "Only the server owner can add custom demo usernames.", ephemeral=True
        )
        return
    try:
        username = validate_roblox_username(username)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return
    bot.db.execute(
        """
        INSERT INTO simulation_users (guild_id, username, pool, weight)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, username, pool)
        DO UPDATE SET enabled = 1, weight = excluded.weight
        """,
        (interaction.guild_id, username, pool.value, weight),
    )
    bot.db.commit()
    await interaction.response.send_message(
        f"Added **{username}** to the {pool.value} demo pool (weight {weight}).",
        ephemeral=True,
    )


@simulation_user_group.command(name="remove", description="Remove a demo username.")
@discord.app_commands.choices(
    pool=[
        discord.app_commands.Choice(name="Donors", value="donors"),
        discord.app_commands.Choice(name="Receivers", value="receivers"),
    ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_user_remove(
    interaction: discord.Interaction,
    pool: discord.app_commands.Choice[str],
    username: str,
) -> None:
    cursor = bot.db.execute(
        "DELETE FROM simulation_users WHERE guild_id = ? AND username = ? AND pool = ?",
        (interaction.guild_id, username.strip(), pool.value),
    )
    removed = cursor.rowcount
    bot.db.commit()
    result = f"Removed **{username.strip()}** from the {pool.value} pool." if removed else "That username was not in the selected pool."
    await interaction.response.send_message(result, ephemeral=True)


@simulation_user_group.command(name="list", description="List configured demo usernames.")
@discord.app_commands.choices(
    pool=[
        discord.app_commands.Choice(name="Donors", value="donors"),
        discord.app_commands.Choice(name="Receivers", value="receivers"),
    ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_user_list(
    interaction: discord.Interaction,
    pool: discord.app_commands.Choice[str] | None = None,
) -> None:
    params: tuple[int, ...] | tuple[int, str]
    query = (
        "SELECT username, pool, enabled, weight FROM simulation_users WHERE guild_id = ? "
    )
    params = (interaction.guild_id,)
    if pool:
        query += "AND pool = ? "
        params = (interaction.guild_id, pool.value)
    query += "ORDER BY pool, username COLLATE NOCASE LIMIT 100"
    users = bot.db.execute(query, params).fetchall()
    if not users:
        content = "No custom usernames are configured. Fictional demo accounts will be generated automatically."
    else:
        content = "\n".join(
            f"`{username}` — {user_pool}, {'enabled' if enabled else 'disabled'}, weight {weight}"
            for username, user_pool, enabled, weight in users
        )
    await interaction.response.send_message(content[:1900], ephemeral=True)


@simulation_user_group.command(
    name="import",
    description="Import up to 100 comma- or newline-separated demo usernames.",
)
@discord.app_commands.choices(
        pool=[
            discord.app_commands.Choice(name="Donors", value="donors"),
            discord.app_commands.Choice(name="Receivers", value="receivers"),
        ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_user_import(
    interaction: discord.Interaction,
    pool: discord.app_commands.Choice[str],
    usernames: str,
) -> None:
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "Only the server owner can import custom demo usernames.",
            ephemeral=True,
        )
        return
    candidates = [item.strip() for item in re.split(r"[\n,]+", usernames) if item.strip()]
    if not candidates or len(candidates) > 100:
        await interaction.response.send_message(
            "Provide between 1 and 100 usernames.", ephemeral=True
        )
        return
    valid: list[str] = []
    invalid: list[str] = []
    for candidate in candidates:
        try:
            valid.append(validate_roblox_username(candidate))
        except ValueError:
            invalid.append(candidate)
    cursor = bot.db.executemany(
        """
        INSERT OR IGNORE INTO simulation_users (guild_id, username, pool)
        VALUES (?, ?, ?)
        """,
        ((interaction.guild_id, username, pool.value) for username in valid),
    )
    imported = cursor.rowcount
    bot.db.commit()
    response = f"Imported {max(imported, 0)} username(s) into the {pool.value} pool."
    if invalid:
        response += f" Invalid names skipped: {', '.join(invalid[:10])}."
    await interaction.response.send_message(response[:1900], ephemeral=True)


@simulation_user_group.command(
    name="enable", description="Enable or disable a configured demo account."
)
@discord.app_commands.choices(
    pool=[
        discord.app_commands.Choice(name="Donors", value="donors"),
        discord.app_commands.Choice(name="Receivers", value="receivers"),
    ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_user_enable(
    interaction: discord.Interaction,
    pool: discord.app_commands.Choice[str],
    username: str,
    enabled: bool,
) -> None:
    cursor = bot.db.execute(
        """
        UPDATE simulation_users SET enabled = ?
        WHERE guild_id = ? AND username = ? AND pool = ?
        """,
        (int(enabled), interaction.guild_id, username.strip(), pool.value),
    )
    bot.db.commit()
    if not cursor.rowcount:
        await interaction.response.send_message(
            "That username is not configured in the selected pool.", ephemeral=True
        )
        return
    status = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"**{username.strip()}** is now {status} in the {pool.value} pool.",
        ephemeral=True,
    )


bot.tree.add_command(simulation_user_group)


@bot.tree.command(name="simulation-theme", description="Enable or disable a card color theme.")
@discord.app_commands.choices(
    theme=[
        discord.app_commands.Choice(name="Pink", value="pink"),
        discord.app_commands.Choice(name="Red", value="red"),
    ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_theme(
    interaction: discord.Interaction,
    theme: discord.app_commands.Choice[str],
    enabled: bool,
) -> None:
    ensure_simulation_settings(interaction.guild_id)
    if not enabled:
        enabled_count = bot.db.execute(
            """
            SELECT COUNT(*) FROM simulation_themes
            WHERE guild_id = ? AND enabled = 1 AND theme != ?
            """,
            (interaction.guild_id, theme.value),
        ).fetchone()[0]
        if enabled_count == 0:
            await interaction.response.send_message(
                "At least one theme must remain enabled.", ephemeral=True
            )
            return
    bot.db.execute(
        """
        UPDATE simulation_themes SET enabled = ?
        WHERE guild_id = ? AND theme = ?
        """,
        (int(enabled), interaction.guild_id, theme.value),
    )
    bot.db.commit()
    status = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"The **{theme.value}** simulation theme is now {status}.", ephemeral=True
    )


@bot.tree.command(
    name="simulation-category",
    description="Enable or disable a notification icon category.",
)
@discord.app_commands.choices(
    category=[
        discord.app_commands.Choice(name="Blue diamond", value="diamond"),
        discord.app_commands.Choice(name="Gold trophy", value="trophy"),
        discord.app_commands.Choice(name="Pink heart", value="heart"),
    ]
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_category(
    interaction: discord.Interaction,
    category: discord.app_commands.Choice[str],
    enabled: bool,
) -> None:
    ensure_simulation_settings(interaction.guild_id)
    if not enabled:
        enabled_count = bot.db.execute(
            """
            SELECT COUNT(*) FROM simulation_categories
            WHERE guild_id = ? AND enabled = 1 AND category != ?
            """,
            (interaction.guild_id, category.value),
        ).fetchone()[0]
        if enabled_count == 0:
            await interaction.response.send_message(
                "At least one notification category must remain enabled.",
                ephemeral=True,
            )
            return
    bot.db.execute(
        """
        UPDATE simulation_categories SET enabled = ?
        WHERE guild_id = ? AND category = ?
        """,
        (int(enabled), interaction.guild_id, category.value),
    )
    bot.db.commit()
    status = "enabled" if enabled else "disabled"
    await interaction.response.send_message(
        f"The **{category.value}** notification category is now {status}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="simulation-settings",
    description="Configure simulated donation timing, limits, and amount weights.",
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_settings(
    interaction: discord.Interaction,
    min_interval_seconds: discord.app_commands.Range[int, 8, 3600] | None = None,
    max_interval_seconds: discord.app_commands.Range[int, 8, 3600] | None = None,
    max_per_hour: discord.app_commands.Range[int, 1, 600] | None = None,
    timezone_name: str | None = None,
    manual_enabled: bool | None = None,
    small_weight: discord.app_commands.Range[int, 0, 10000] | None = None,
    medium_weight: discord.app_commands.Range[int, 0, 10000] | None = None,
    large_weight: discord.app_commands.Range[int, 0, 10000] | None = None,
    mega_weight: discord.app_commands.Range[int, 0, 10000] | None = None,
    ultra_weight: discord.app_commands.Range[int, 0, 10000] | None = None,
    legendary_weight: discord.app_commands.Range[int, 0, 10000] | None = None,
) -> None:
    ensure_simulation_settings(interaction.guild_id)
    updates: dict[str, int | str] = {}
    if min_interval_seconds is not None:
        updates["min_interval_seconds"] = min_interval_seconds
    if max_interval_seconds is not None:
        updates["max_interval_seconds"] = max_interval_seconds
    if max_per_hour is not None:
        updates["max_per_hour"] = max_per_hour
    if manual_enabled is not None:
        updates["manual_enabled"] = int(manual_enabled)
    if timezone_name is not None:
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            await interaction.response.send_message(
                "Use a valid IANA timezone such as `UTC` or `America/New_York`.",
                ephemeral=True,
            )
            return
        updates["timezone"] = timezone_name
    for key, value in (
        ("small", small_weight),
        ("medium", medium_weight),
        ("large", large_weight),
        ("mega", mega_weight),
        ("ultra", ultra_weight),
        ("legendary", legendary_weight),
    ):
        if value is not None:
            updates[f"weight_{key}"] = value
    if any(key.startswith("weight_") for key in updates):
        settings = get_simulation_settings(interaction.guild_id)
        if sum(
            int(updates.get(f"weight_{name}", settings[f"weight_{name}"]) or 0)
            for name in SIMULATION_AMOUNTS
        ) <= 0:
            await interaction.response.send_message(
                "At least one amount weight must be greater than zero.",
                ephemeral=True,
            )
            return
    current_settings = get_simulation_settings(interaction.guild_id)
    min_interval = int(
        updates.get("min_interval_seconds", current_settings["min_interval_seconds"])
    )
    max_interval = int(
        updates.get("max_interval_seconds", current_settings["max_interval_seconds"])
    )
    if min_interval > max_interval:
        await interaction.response.send_message(
            "The minimum interval cannot exceed the maximum interval.",
            ephemeral=True,
        )
        return
    if updates:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        bot.db.execute(
            f"UPDATE simulation_settings SET {assignments} WHERE guild_id = ?",
            (*updates.values(), interaction.guild_id),
        )
        bot.db.commit()
    settings = get_simulation_settings(interaction.guild_id)
    await interaction.response.send_message(
        "Simulation settings saved. "
        f"Interval: {settings['min_interval_seconds']}–"
        f"{settings['max_interval_seconds']}s; "
        f"hourly limit: {settings['max_per_hour']}; "
        f"manual generation: {'on' if settings['manual_enabled'] else 'off'}; "
        f"timezone: `{settings['timezone']}`. "
        "Amount weights use relative proportions and can be set to zero.",
        ephemeral=True,
    )


@bot.tree.command(
    name="simulation-status",
    description="Show whether Zap's simulation mode is enabled.",
)
@discord.app_commands.guild_only()
async def simulation_status(interaction: discord.Interaction) -> None:
    settings = get_simulation_settings(interaction.guild_id)
    themes = enabled_simulation_themes(interaction.guild_id)
    channel_text = f"<#{settings['channel_id']}>" if settings["channel_id"] else "not set"
    await interaction.response.send_message(
        "**Zap simulation status**\n"
        f"Automatic: {'enabled' if settings['auto_enabled'] else 'disabled'}\n"
        f"Channel: {channel_text}\n"
        f"Interval: {settings['min_interval_seconds']}–"
        f"{settings['max_interval_seconds']} seconds\n"
        f"Manual generation: {'enabled' if settings['manual_enabled'] else 'disabled'}\n"
        f"Enabled themes: {', '.join(themes) or 'none'}\n"
        f"Enabled notification icons: {', '.join(enabled_simulation_categories(interaction.guild_id)) or 'none'}\n"
        "All generated cards and messages are simulations, not Roblox transactions.",
        ephemeral=True,
    )


@bot.tree.command(
    name="simulation-test",
    description="Preview a simulated donation card privately.",
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def simulation_test(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        file, event = await create_simulation_file(interaction.guild_id)
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    await interaction.followup.send(
        content=simulation_message(event, interaction.guild_id),
        file=file,
        embed=simulation_embed(event, file.filename),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(
    name="start",
    description="Start labeled simulated donations in a channel every 8 seconds.",
)
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def start_simulation(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> None:
    ensure_simulation_settings(interaction.guild_id)
    bot.db.execute(
        """
        UPDATE simulation_settings
        SET auto_enabled = 1, channel_id = ?,
            min_interval_seconds = 8, max_interval_seconds = 8
        WHERE guild_id = ?
        """,
        (channel.id, interaction.guild_id),
    )
    bot.db.commit()
    current_task = bot.simulation_tasks.get(interaction.guild_id)
    if current_task is None or current_task.done():
        bot.simulation_tasks[interaction.guild_id] = asyncio.create_task(
            _run_automatic_simulations(interaction.guild_id)
        )
    await interaction.response.send_message(
        f"Automatic simulated donations are enabled in {channel.mention} every 8 seconds. "
        "Every post is labeled as a simulation.",
        ephemeral=True,
    )


@bot.tree.command(name="stop", description="Stop automatic simulated donations.")
@discord.app_commands.guild_only()
@discord.app_commands.checks.has_permissions(administrator=True)
async def stop_simulation(interaction: discord.Interaction) -> None:
    ensure_simulation_settings(interaction.guild_id)
    bot.db.execute(
        "UPDATE simulation_settings SET auto_enabled = 0 WHERE guild_id = ?",
        (interaction.guild_id,),
    )
    bot.db.commit()
    task = bot.simulation_tasks.get(interaction.guild_id)
    if task:
        task.cancel()
    await interaction.response.send_message(
        "Automatic simulated donations are disabled.", ephemeral=True
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: discord.app_commands.AppCommandError
) -> None:
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        message = f"Try again in {error.retry_after:.1f} seconds."
    elif isinstance(error, discord.app_commands.MissingPermissions):
        message = "Only server administrators can configure simulations."
    else:
        log.error("Zap app command failed: %s", error, exc_info=error)
        message = "That command failed. Please try again or contact a server administrator."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def staff_only():
    return commands.has_role(STAFF_TEAM_ROLE_ID)


async def command_channel_check(ctx: commands.Context) -> bool:
    if ctx.command and ctx.command.name == "afk":
        return True
    if ctx.guild is None:
        return True
    return (
        ctx.channel.id == BOT_COMMANDS_CHANNEL_ID
        or ctx.author.guild_permissions.manage_guild
    )


bot.add_check(command_channel_check)


async def command_rate_limit_check(ctx: commands.Context) -> bool:
    return bot.command_rate_limiter.allow(ctx.author.id)


bot.add_check(command_rate_limit_check)


async def send_server_log(
    guild: discord.Guild,
    title: str,
    description: str,
    *,
    color: discord.Colour = discord.Colour.blurple(),
    fields: list[tuple[str, str, bool]] | None = None,
) -> None:
    if not ENABLE_SERVER_LOGS:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except discord.NotFound:
            log.error("Log channel %s was not found", LOG_CHANNEL_ID)
            return
        except discord.Forbidden:
            log.exception("Missing permission to access log channel %s", LOG_CHANNEL_ID)
            return
        except discord.HTTPException:
            log.exception("Could not access log channel %s", LOG_CHANNEL_ID)
            return

    embed = discord.Embed(
        title=title[:256],
        description=description[:4096],
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=f"{guild.name} • {guild.id}")
    for name, value, inline in fields or []:
        embed.add_field(name=name[:256], value=value[:1024] or "—", inline=inline)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        log.exception("Missing permission to send server log to channel %s", LOG_CHANNEL_ID)
    except discord.HTTPException:
        log.exception("Could not send server log to channel %s", LOG_CHANNEL_ID)


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")
    for (guild_id,) in bot.db.execute(
        "SELECT guild_id FROM simulation_settings WHERE auto_enabled = 1"
    ).fetchall():
        task = bot.simulation_tasks.get(guild_id)
        if task is None or task.done():
            bot.simulation_tasks[guild_id] = asyncio.create_task(
                _run_automatic_simulations(guild_id)
            )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if (
        ENABLE_BOOST_ANNOUNCEMENTS
        and before.premium_since is None
        and after.premium_since is not None
    ):
        guild_id = after.guild.id
        batch = bot.boost_batches.setdefault(guild_id, [])
        if not batch:
            bot.boost_tasks[guild_id] = asyncio.create_task(
                bot._announce_boosts(after.guild)
            )
        batch.append(after)

    if before.nick != after.nick:
        await send_server_log(
            after.guild,
            "Member nickname changed",
            f"{after.mention} ({after.id})",
            fields=[
                ("Before", before.nick or before.name, True),
                ("After", after.nick or after.name, True),
            ],
        )
    if before.roles != after.roles:
        added = [role.mention for role in after.roles if role not in before.roles]
        removed = [role.mention for role in before.roles if role not in after.roles]
        changes = []
        if added:
            changes.append(f"Added: {', '.join(added)}")
        if removed:
            changes.append(f"Removed: {', '.join(removed)}")
        await send_server_log(
            after.guild,
            "Member roles updated",
            f"{after.mention} ({after.id})",
            fields=[("Changes", "\n".join(changes), False)],
        )
    if before.timed_out_until != after.timed_out_until:
        until = (
            discord.utils.format_dt(after.timed_out_until, style="F")
            if after.timed_out_until
            else "Timeout removed"
        )
        await send_server_log(
            after.guild,
            "Member timeout updated",
            f"{after.mention} ({after.id})",
            fields=[("Status", until, False)],
            color=discord.Colour.orange(),
        )


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.guild is None or before.author.bot or before.content == after.content:
        return
    await send_server_log(
        before.guild,
        "Message edited",
        f"By {before.author.mention} in {before.channel.mention}",
        fields=[
            ("Before", before.content or "(empty)", False),
            ("After", after.content or "(empty)", False),
            ("Message", f"[Jump to message]({after.jump_url})", False),
        ],
        color=discord.Colour.orange(),
    )


@bot.event
async def on_message_delete(message: discord.Message) -> None:
    if message.guild is None:
        return
    attachment_names = ", ".join(item.filename for item in message.attachments)
    fields = [("Content", message.content or "(empty or unavailable)", False)]
    if attachment_names:
        fields.append(("Attachments", attachment_names, False))
    await send_server_log(
        message.guild,
        "Message deleted",
        f"By {message.author.mention} in {message.channel.mention}",
        fields=fields,
        color=discord.Colour.red(),
    )


@bot.event
async def on_bulk_message_delete(messages: list[discord.Message]) -> None:
    if not messages or messages[0].guild is None:
        return
    authors = sorted({f"{message.author} ({message.author.id})" for message in messages})
    await send_server_log(
        messages[0].guild,
        "Messages bulk deleted",
        f"**{len(messages)}** messages deleted in {messages[0].channel.mention}.",
        fields=[("Authors", "\n".join(authors) or "Unknown", False)],
        color=discord.Colour.red(),
    )


@bot.event
async def on_member_join(member: discord.Member) -> None:
    await send_server_log(
        member.guild,
        "Member joined",
        f"{member.mention} ({member}, {member.id})",
        color=discord.Colour.green(),
    )


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    await send_server_log(
        member.guild,
        "Member left",
        f"{member} ({member.id})",
        color=discord.Colour.orange(),
    )


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    await send_server_log(
        guild,
        "Member banned",
        f"{user} ({user.id})",
        color=discord.Colour.red(),
    )


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    await send_server_log(
        guild,
        "Member unbanned",
        f"{user} ({user.id})",
        color=discord.Colour.green(),
    )


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    await send_server_log(channel.guild, "Channel created", f"{channel.mention} ({channel.id})")


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    await send_server_log(
        channel.guild,
        "Channel deleted",
        f"{channel.name} ({channel.id})",
        color=discord.Colour.red(),
    )


@bot.event
async def on_guild_channel_update(
    before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
) -> None:
    if before.name == after.name:
        return
    await send_server_log(
        after.guild,
        "Channel renamed",
        f"Channel ID: {after.id}",
        fields=[("Before", before.name, True), ("After", after.name, True)],
    )


@bot.event
async def on_guild_role_create(role: discord.Role) -> None:
    await send_server_log(role.guild, "Role created", f"{role.mention} ({role.id})")


@bot.event
async def on_guild_role_delete(role: discord.Role) -> None:
    await send_server_log(
        role.guild,
        "Role deleted",
        f"{role.name} ({role.id})",
        color=discord.Colour.red(),
    )


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    if before.name == after.name and before.permissions == after.permissions:
        return
    changes = []
    if before.name != after.name:
        changes.append(f"Name: {before.name} -> {after.name}")
    if before.permissions != after.permissions:
        changes.append("Role permissions changed")
    await send_server_log(
        after.guild,
        "Role updated",
        f"{after.mention} ({after.id})",
        fields=[("Changes", "\n".join(changes), False)],
    )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if before.channel == after.channel:
        return
    if before.channel is None:
        description = f"{member.mention} joined {after.channel.mention}"
    elif after.channel is None:
        description = f"{member.mention} left {before.channel.mention}"
    else:
        description = (
            f"{member.mention} moved from {before.channel.mention} "
            f"to {after.channel.mention}"
        )
    await send_server_log(member.guild, "Voice channel updated", description)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    if (
        ENABLE_FEEDBACK_POST_PROMPTS
        and isinstance(message.channel, discord.Thread)
        and message.channel.parent_id == FEEDBACK_FORUM_ID
        and message.id == message.channel.id
    ):
        try:
            await message.add_reaction("👍")
            await message.channel.send("What Do You Think About This Post?")
        except discord.Forbidden:
            log.exception("Missing permission to respond to a feedback post")
        except discord.HTTPException:
            log.exception("Could not respond to a feedback post")

    author_afk = bot.db.execute(
        "SELECT started_at FROM afk WHERE guild_id = ? AND user_id = ?",
        (message.guild.id, message.author.id),
    ).fetchone()
    if author_afk:
        bot.db.execute(
            "DELETE FROM afk WHERE guild_id = ? AND user_id = ?",
            (message.guild.id, message.author.id),
        )
        bot.db.commit()
        elapsed = max(0, int(time.time()) - author_afk[0])
        await message.channel.send(
            f"Welcome back, {message.author.mention}! You were AFK for "
            f"**{format_elapsed(elapsed)}**."
        )

    for mentioned in message.mentions:
        if mentioned.id == message.author.id:
            continue
        afk_row = bot.db.execute(
            "SELECT reason, started_at FROM afk WHERE guild_id = ? AND user_id = ?",
            (message.guild.id, mentioned.id),
        ).fetchone()
        if afk_row:
            started = datetime.fromtimestamp(afk_row[1], timezone.utc)
            await message.channel.send(
                f"{mentioned.display_name} is AFK: {afk_row[0]} "
                f"(since {discord.utils.format_dt(started, style='R')})."
            )

    await bot.process_commands(message)


@bot.command()
@commands.guild_only()
async def afk(ctx: commands.Context, *, reason: str = "AFK") -> None:
    bot.db.execute(
        """
        INSERT INTO afk (guild_id, user_id, reason, started_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET reason = excluded.reason, started_at = excluded.started_at
        """,
        (ctx.guild.id, ctx.author.id, reason, int(time.time())),
    )
    bot.db.commit()
    await ctx.send(f"{ctx.author.mention} is now AFK: {reason}")


@bot.command()
@commands.guild_only()
@staff_only()
async def ban(
    ctx: commands.Context,
    member: discord.Member,
    duration: str,
    *,
    reason: str = "No reason provided",
) -> None:
    if duration.lower() in {"perm", "permanent"}:
        await member.ban(reason=reason)
        bot.db.execute(
            "DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, member.id),
        )
        bot.db.commit()
        await ctx.send(f"Banned {member} permanently. Reason: {reason}")
        return

    try:
        seconds = parse_duration(duration)
    except ValueError as error:
        await ctx.send(str(error))
        return
    expires_at = int(time.time()) + seconds
    await member.ban(reason=f"{reason} (temporary ban)")
    bot.db.execute(
        """
        INSERT INTO temp_bans (guild_id, user_id, expires_at) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET expires_at = excluded.expires_at
        """,
        (ctx.guild.id, member.id, expires_at),
    )
    bot.db.commit()
    await ctx.send(
        f"Banned {member} for **{duration}**. Reason: {reason}"
    )


@bot.command()
@commands.guild_only()
@staff_only()
async def kick(
    ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"
) -> None:
    await member.kick(reason=reason)
    await ctx.send(f"Kicked {member}. Reason: {reason}")


@bot.command()
@commands.guild_only()
@staff_only()
async def mute(
    ctx: commands.Context,
    member: discord.Member,
    duration: str,
    *,
    reason: str = "No reason provided",
) -> None:
    try:
        seconds = parse_duration(duration)
    except ValueError as error:
        await ctx.send(str(error))
        return
    if seconds > MAX_TIMEOUT_SECONDS:
        await ctx.send("Discord only allows timeouts of up to 28 days.")
        return
    until = datetime.fromtimestamp(time.time() + seconds, timezone.utc)
    await member.timeout(until, reason=reason)
    await ctx.send(f"Timed out {member} for **{duration}**. Reason: {reason}")


@bot.command()
@commands.guild_only()
@staff_only()
async def unmute(
    ctx: commands.Context,
    member: discord.Member,
    *,
    reason: str = "No reason provided",
) -> None:
    await member.timeout(None, reason=reason)
    await ctx.send(f"Removed the timeout for {member}. Reason: {reason}")


@bot.command()
@commands.guild_only()
@staff_only()
async def unban(
    ctx: commands.Context,
    user: discord.User,
    *,
    reason: str = "No reason provided",
) -> None:
    await ctx.guild.unban(user, reason=reason)
    bot.db.execute(
        "DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?",
        (ctx.guild.id, user.id),
    )
    bot.db.commit()
    await ctx.send(f"Unbanned {user}. Reason: {reason}")


@bot.command()
@commands.guild_only()
async def role(ctx: commands.Context, target: str, *, role_name: str) -> None:
    if not (
        ctx.author.guild_permissions.manage_roles
        or ctx.author.guild_permissions.manage_guild
    ):
        await ctx.send(
            "You need **Manage Roles** or **Manage Server** to use this command."
        )
        return

    bot_member = ctx.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        await ctx.send("I need **Manage Roles** to assign roles.")
        return

    try:
        target_role = await commands.RoleConverter().convert(ctx, role_name)
    except commands.RoleNotFound:
        role_query = role_name.strip().casefold()
        if not role_query:
            await ctx.send(f"I couldn't find the role `{role_name}`.")
            return
        exact_matches = [
            role for role in ctx.guild.roles if role.name.casefold() == role_query
        ]
        matches = exact_matches or [
            role for role in ctx.guild.roles if role_query in role.name.casefold()
        ]
        if not matches:
            await ctx.send(f"I couldn't find the role `{role_name}`.")
            return
        if len(matches) > 1:
            role_names = ", ".join(f"`{role.name}`" for role in matches)
            await ctx.send(
                f"More than one role matches `{role_name}`: {role_names}. "
                "Please be more specific."
            )
            return
        target_role = matches[0]

    if not target_role.is_assignable():
        await ctx.send("I can't assign that role. Check its position and whether it's managed.")
        return
    if ctx.guild.owner_id != ctx.author.id and target_role >= ctx.author.top_role:
        await ctx.send("You can only assign roles below your highest role.")
        return

    if target.casefold() == "all":
        updated = 0
        failed = 0
        async for member in ctx.guild.fetch_members(limit=None):
            if member.id == bot_member.id or target_role in member.roles:
                continue
            try:
                await member.add_roles(
                    target_role,
                    reason=f"Role assigned by {ctx.author} ({ctx.author.id})",
                )
                updated += 1
            except discord.HTTPException:
                log.exception(
                    "Could not assign role %s to member %s in guild %s",
                    target_role.id,
                    member.id,
                    ctx.guild.id,
                )
                failed += 1
        result = f"Assigned {target_role.name} to **{updated}** member(s)."
        if failed:
            result += f" **{failed}** assignment(s) failed."
        await ctx.send(result)
        return

    try:
        member = await commands.MemberConverter().convert(ctx, target)
    except commands.MemberNotFound:
        matches = [
            candidate
            for candidate in ctx.guild.members
            if target.casefold() in {
                candidate.name.casefold(),
                candidate.display_name.casefold(),
            }
        ]
        if len(matches) == 1:
            member = matches[0]
        elif len(matches) > 1:
            await ctx.send(
                f"More than one member matches `{target}`. "
                "Please use a mention or user ID."
            )
            return
        else:
            await ctx.send(f"I couldn't find the member `{target}`.")
            return

    if target_role in member.roles:
        await ctx.send(f"{member.mention} already has {target_role.name}.")
        return

    await member.add_roles(
        target_role,
        reason=f"Role assigned by {ctx.author} ({ctx.author.id})",
    )
    await ctx.send(f"Assigned {target_role.name} to {member.mention}.")


@bot.command()
@commands.guild_only()
@staff_only()
@commands.has_guild_permissions(manage_messages=True)
async def purge(ctx: commands.Context, messageamount: int) -> None:
    if messageamount <= 0:
        await ctx.send("The message amount must be greater than zero.")
        return
    deleted = await ctx.channel.purge(
        limit=messageamount,
        reason=f"Purged by {ctx.author} ({ctx.author.id})",
    )
    await ctx.send(f"Deleted **{len(deleted)}** messages.", delete_after=5)


@bot.command()
@commands.guild_only()
async def say(ctx: commands.Context, *, message: str) -> None:
    if not ctx.message.content.startswith("."):
        return
    if not ctx.author.guild_permissions.administrator:
        return
    await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())


@bot.command()
@commands.guild_only()
@staff_only()
async def warn(
    ctx: commands.Context, member: discord.Member, *, reason: str
) -> None:
    bot.db.execute(
        """
        INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ctx.guild.id, member.id, ctx.author.id, reason, int(time.time())),
    )
    bot.db.commit()
    await ctx.send(f"Warned {member}. Reason: {reason}")


@bot.command()
@commands.guild_only()
@staff_only()
async def warnings(ctx: commands.Context, member: discord.Member) -> None:
    rows = bot.db.execute(
        """
        SELECT reason, moderator_id, created_at FROM warnings
        WHERE guild_id = ? AND user_id = ?
        ORDER BY id DESC LIMIT 10
        """,
        (ctx.guild.id, member.id),
    ).fetchall()
    if not rows:
        await ctx.send(f"{member} has no warnings.")
        return
    lines = [
        f"• <t:{created_at}:d> — {reason} (by <@{moderator_id}>)"
        for reason, moderator_id, created_at in rows
    ]
    await ctx.send(f"Warnings for **{member}**:\n" + "\n".join(lines))


async def set_channel_lock(
    ctx: commands.Context, channel: discord.TextChannel, locked: bool
) -> None:
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False if locked else None
    overwrite.send_messages_in_threads = False if locked else None
    await channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite,
        reason=f"{'Locked' if locked else 'Unlocked'} by {ctx.author}",
    )
    await ctx.send(f"Locked {channel}." if locked else f"Unlocked {channel}.")


@bot.command()
@commands.guild_only()
@staff_only()
@commands.has_guild_permissions(manage_channels=True)
async def lock(ctx: commands.Context, channel: discord.TextChannel) -> None:
    await set_channel_lock(ctx, channel, True)


@bot.command()
@commands.guild_only()
@staff_only()
@commands.has_guild_permissions(manage_channels=True)
async def unlock(ctx: commands.Context, channel: discord.TextChannel) -> None:
    await set_channel_lock(ctx, channel, False)


def get_balance(guild_id: int, user_id: int) -> int:
    bot._ensure_account(guild_id, user_id)
    row = bot.db.execute(
        "SELECT balance FROM economy WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0]


@bot.command()
@commands.guild_only()
async def balance(ctx: commands.Context, member: discord.Member | None = None) -> None:
    target = member or ctx.author
    amount = get_balance(ctx.guild.id, target.id)
    await ctx.send(f"{target.mention} has **{amount:,}** coins.")


@bot.command()
@commands.guild_only()
async def daily(ctx: commands.Context) -> None:
    get_balance(ctx.guild.id, ctx.author.id)
    row = bot.db.execute(
        "SELECT balance, last_daily FROM economy WHERE guild_id = ? AND user_id = ?",
        (ctx.guild.id, ctx.author.id),
    ).fetchone()
    now = int(time.time())
    remaining = 24 * 60 * 60 - (now - row[1])
    if remaining > 0:
        await ctx.send(
            "You already claimed your daily coins. Try again "
            f"<t:{row[1] + 24 * 60 * 60}:R>."
        )
        return
    bot.db.execute(
        """
        UPDATE economy SET balance = balance + 500, last_daily = ?
        WHERE guild_id = ? AND user_id = ?
        """,
        (now, ctx.guild.id, ctx.author.id),
    )
    bot.db.commit()
    await ctx.send("You got **500** coins. Come back tomorrow for more.")


@bot.command()
@commands.guild_only()
async def coinflip(
    ctx: commands.Context, amount: int, choice: str = "heads"
) -> None:
    choice = choice.lower()
    if amount <= 0:
        await ctx.send("Bet an amount greater than zero.")
        return
    if choice not in {"heads", "tails"}:
        await ctx.send("Choose `heads` or `tails`.")
        return
    balance_amount = get_balance(ctx.guild.id, ctx.author.id)
    if amount > balance_amount:
        await ctx.send("You don't have enough coins for that bet.")
        return

    result = "heads" if secrets.randbelow(2) == 0 else "tails"
    won = result == choice
    change = amount if won else -amount
    bot.db.execute(
        "UPDATE economy SET balance = balance + ? WHERE guild_id = ? AND user_id = ?",
        (change, ctx.guild.id, ctx.author.id),
    )
    bot.db.commit()
    outcome = f"You won **{amount:,}** coins!" if won else f"You lost **{amount:,}** coins."
    await ctx.send(
        f"It's **{result}** — {outcome} Your balance is now "
        f"**{balance_amount + change:,}**."
    )


@bot.event
async def on_command_error(
    ctx: commands.Context, error: commands.CommandError
) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("I couldn't find that member or channel. Check the mention and try again.")
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("That command can only be used in a server.")
        return
    if isinstance(error, commands.CommandInvokeError):
        original = error.original
        log.error(
            "Command %s failed: %s",
            ctx.command,
            original,
            exc_info=(type(original), original, original.__traceback__),
        )
        if isinstance(original, discord.Forbidden):
            await ctx.send("I don't have permission to do that.")
        else:
            await ctx.send("That command ran into an error. Please try again or contact staff.")
        return
    log.error("Command error for %s: %s", ctx.command, error)
    await ctx.send("Something went wrong while running that command.")


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "Set the DISCORD_TOKEN environment variable before starting the bot."
        )
    bot.run(token)

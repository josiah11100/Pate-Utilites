import asyncio
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv
from discord.ext import commands


load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pate_utilities")

PREFIX = "?"
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


class PateUtilities(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=PREFIX, intents=intents)
        self.db = sqlite3.connect(os.getenv("PATE_DB_PATH", "pate_utilities.sqlite3"))
        self.boost_batches: dict[int, list[discord.Member]] = {}
        self.boost_tasks: dict[int, asyncio.Task] = {}
        self.expiry_task: asyncio.Task | None = None
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
            """
        )
        self.db.commit()

    async def setup_hook(self) -> None:
        self.expiry_task = asyncio.create_task(self._expire_temporary_bans())

    async def close(self) -> None:
        if self.expiry_task:
            self.expiry_task.cancel()
        for task in self.boost_tasks.values():
            task.cancel()
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


bot = PateUtilities()


def staff_only():
    return commands.has_role(STAFF_TEAM_ROLE_ID)


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if before.premium_since is not None or after.premium_since is None:
        return
    guild_id = after.guild.id
    batch = bot.boost_batches.setdefault(guild_id, [])
    if not batch:
        bot.boost_tasks[guild_id] = asyncio.create_task(
            bot._announce_boosts(after.guild)
        )
    batch.append(after)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None:
        return

    if (
        isinstance(message.channel, discord.Thread)
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
    if isinstance(error, commands.MissingRole):
        await ctx.send("You need the Staff Team role to use that command.")
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need Manage Channels to use that command.")
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

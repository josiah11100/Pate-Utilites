import os
import unittest
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from discord import PartialEmoji

os.environ.setdefault("PATE_DB_PATH", ":memory:")

from discord.ext import commands

from pate_utilities import (
    CommandRateLimiter,
    choose_donation_amount,
    choose_demo_users,
    donation_icon_path_for_amount,
    donation_style_for_amount,
    bot as zap_bot,
    get_simulation_settings,
    format_elapsed,
    get_command_prefix,
    level_for_boosts,
    on_member_update,
    on_message,
    on_command_error,
    parse_duration,
    render_donation_card,
    robux_emoji_for_guild,
    role as role_command,
    send_server_log,
    simulation_embed,
    simulation_message,
    validate_roblox_username,
    _load_font,
)


class CommandPrefixTests(unittest.TestCase):
    def test_only_say_uses_the_dot_prefix(self):
        self.assertEqual(get_command_prefix(None, SimpleNamespace(content=".say hello")), ".")
        self.assertEqual(get_command_prefix(None, SimpleNamespace(content=".ban @user")), "?")
        self.assertEqual(get_command_prefix(None, SimpleNamespace(content="?say hello")), "?")


class PermissionErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_and_role_failures_are_silent(self):
        errors = (
            commands.MissingRole("Staff Team"),
            commands.MissingPermissions(["administrator"]),
            commands.CheckFailure("command check failed"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                ctx = SimpleNamespace(send=AsyncMock())
                await on_command_error(ctx, error)
                ctx.send.assert_not_awaited()


class RoleCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self, roles):
        author = SimpleNamespace(
            id=1,
            guild_permissions=SimpleNamespace(manage_roles=True, manage_guild=False),
            top_role=object(),
        )
        bot_member = SimpleNamespace(
            id=9, guild_permissions=SimpleNamespace(manage_roles=True)
        )
        guild = SimpleNamespace(roles=roles, me=bot_member, owner_id=author.id)
        return SimpleNamespace(
            author=author,
            guild=guild,
            send=AsyncMock(),
        )

    async def test_partial_role_name_is_case_insensitive_and_not_mentioned(self):
        role = SimpleNamespace(
            id=3,
            name="Moderator",
            mention="<@&3>",
            is_assignable=Mock(return_value=True),
        )
        member = SimpleNamespace(
            id=2,
            mention="<@2>",
            roles=[],
            add_roles=AsyncMock(),
        )
        ctx = self.make_context([role])

        with (
            patch.object(
                commands.RoleConverter,
                "convert",
                new=AsyncMock(side_effect=commands.RoleNotFound("mOd")),
            ),
            patch.object(
                commands.MemberConverter,
                "convert",
                new=AsyncMock(return_value=member),
            ),
        ):
            await role_command.callback(ctx, "member", role_name="mOd")

        member.add_roles.assert_awaited_once()
        ctx.send.assert_awaited_once_with("Assigned Moderator to <@2>.")

    async def test_ambiguous_partial_role_name_does_not_assign_a_role(self):
        roles = [
            SimpleNamespace(name="Moderator"),
            SimpleNamespace(name="Moderator in training"),
        ]
        ctx = self.make_context(roles)

        with patch.object(
            commands.RoleConverter,
            "convert",
            new=AsyncMock(side_effect=commands.RoleNotFound("mod")),
        ):
            await role_command.callback(ctx, "member", role_name="mod")

        ctx.send.assert_awaited_once_with(
            "More than one role matches `mod`: `Moderator`, "
            "`Moderator in training`. Please be more specific."
        )


class CommandRateLimiterTests(unittest.TestCase):
    def test_limits_each_user_and_allows_commands_after_the_window(self):
        limiter = CommandRateLimiter()
        self.assertEqual([limiter.allow(1, now=0) for _ in range(5)], [True] * 5)
        self.assertFalse(limiter.allow(1, now=1))
        self.assertTrue(limiter.allow(2, now=1))
        self.assertTrue(limiter.allow(1, now=5))


class OptionalChannelFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_logs_are_disabled_without_looking_up_the_channel(self):
        with (
            patch.object(zap_bot, "get_channel") as get_channel,
            patch.object(zap_bot, "fetch_channel", new=AsyncMock()) as fetch_channel,
        ):
            await send_server_log(SimpleNamespace(name="Test", id=1), "Title", "Body")

        get_channel.assert_not_called()
        fetch_channel.assert_not_awaited()

    async def test_boost_announcements_are_disabled_by_default(self):
        before = SimpleNamespace(
            premium_since=None,
            nick=None,
            name="Member",
            roles=[],
            timed_out_until=None,
        )
        guild = SimpleNamespace(id=987654321)
        after = SimpleNamespace(
            premium_since=datetime.now(),
            nick=None,
            name="Member",
            roles=[],
            timed_out_until=None,
            guild=guild,
        )

        with patch("pate_utilities.asyncio.create_task") as create_task:
            await on_member_update(before, after)

        create_task.assert_not_called()

    async def test_feedback_prompts_are_disabled_by_default(self):
        channel = SimpleNamespace(
            add_reaction=AsyncMock(),
            send=AsyncMock(),
        )
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=1),
            guild=SimpleNamespace(id=987654322),
            channel=channel,
            id=1,
            mentions=[],
        )

        with patch.object(zap_bot, "process_commands", new=AsyncMock()):
            await on_message(message)

        channel.add_reaction.assert_not_awaited()
        channel.send.assert_not_awaited()


class DurationTests(unittest.TestCase):
    def test_parses_common_and_compound_durations(self):
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("1d, 2h"), 93600)
        self.assertEqual(parse_duration("1mo"), 30 * 24 * 60 * 60)

    def test_rejects_empty_zero_and_unknown_durations(self):
        for value in ("", "0m", "soon", "1fortnight"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_duration(value)


class BoostLevelTests(unittest.TestCase):
    def test_level_thresholds(self):
        self.assertEqual(level_for_boosts(1), 0)
        self.assertEqual(level_for_boosts(2), 1)
        self.assertEqual(level_for_boosts(6), 1)
        self.assertEqual(level_for_boosts(7), 2)
        self.assertEqual(level_for_boosts(14), 3)


class ElapsedFormatTests(unittest.TestCase):
    def test_formats_duration_for_afk_return_message(self):
        self.assertEqual(format_elapsed(0), "less than a second")
        self.assertEqual(format_elapsed(62), "1 minute 2 seconds")
        self.assertEqual(format_elapsed(90061), "1 day 1 hour")


class SimulationTests(unittest.TestCase):
    def test_simulation_defaults_to_automatic_mode_off(self):
        self.assertEqual(get_simulation_settings(987654321)["auto_enabled"], 0)

    def test_demo_user_pairs_are_distinct_and_not_reused_when_alternatives_exist(self):
        guild_id = 987654322
        zap_bot.db.execute(
            "DELETE FROM simulation_events WHERE guild_id = ?", (guild_id,)
        )
        zap_bot.db.execute(
            "DELETE FROM simulation_users WHERE guild_id = ?", (guild_id,)
        )
        zap_bot.db.executemany(
            "INSERT INTO simulation_users (guild_id, username, pool) VALUES (?, ?, ?)",
            (
                (guild_id, "DemoDonorA", "donors"),
                (guild_id, "DemoDonorB", "donors"),
                (guild_id, "DemoGuestA", "receivers"),
                (guild_id, "DemoGuestB", "receivers"),
            ),
        )
        zap_bot.db.commit()
        try:
            first = choose_demo_users(guild_id)
            zap_bot.db.execute(
                """
                INSERT INTO simulation_events
                    (guild_id, donor, receiver, amount, theme, category, source, created_at)
                VALUES (?, ?, ?, 100000, 'pink', 'diamond', 'manual', 1)
                """,
                (guild_id, *first),
            )
            zap_bot.db.commit()
            second = choose_demo_users(guild_id)
            self.assertNotEqual(first, second)
            self.assertNotEqual(second[0].casefold(), second[1].casefold())
        finally:
            zap_bot.db.execute(
                "DELETE FROM simulation_events WHERE guild_id = ?", (guild_id,)
            )
            zap_bot.db.execute(
                "DELETE FROM simulation_users WHERE guild_id = ?", (guild_id,)
            )
            zap_bot.db.commit()

    def test_usernames_are_validated_and_amounts_never_fall_below_minimum(self):
        self.assertEqual(validate_roblox_username("Demo_User09"), "Demo_User09")
        for value in ("ab", "_DemoUser", "DemoUser_", "Bad__Name", "Name With Space"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_roblox_username(value)

        self.assertEqual(
            choose_donation_amount({"small": 1}, randbelow=lambda upper: 0),
            100_000,
        )
        self.assertEqual(
            choose_donation_amount({"legendary": 1}, randbelow=lambda upper: 0),
            50_000_000,
        )
        with self.assertRaises(ValueError):
            choose_donation_amount({"small": 0})

    def test_card_renders_in_landscape_and_rejects_small_amounts(self):
        png = render_donation_card(
            "DemoDonor001",
            "DemoGuest001",
            1_000_000,
        )
        with Image.open(BytesIO(png)) as card:
            self.assertEqual(card.size, (2816, 768))
            self.assertEqual(card.format, "PNG")
            self.assertEqual(card.mode, "RGBA")
            self.assertEqual(card.getpixel((0, 0))[3], 0)
        with self.assertRaises(ValueError):
            render_donation_card(
                "DemoDonor001",
                "DemoGuest001",
                99_999,
            )

    def test_donation_style_tracks_amount_tiers(self):
        self.assertEqual(donation_style_for_amount(199_999), "purple")
        self.assertEqual(donation_style_for_amount(1_000_000), "pink")
        self.assertEqual(donation_style_for_amount(10_000_000), "red")

    def test_donation_icon_file_tracks_amount_tiers(self):
        self.assertEqual(
            donation_icon_path_for_amount(999_999).name,
            "donation-100k.png",
        )
        self.assertEqual(
            donation_icon_path_for_amount(1_000_000).name,
            "donation-1m.png",
        )
        self.assertEqual(
            donation_icon_path_for_amount(10_000_000).name,
            "donation-10m.png",
        )

    def test_each_amount_tier_renders_its_accent_on_a_transparent_canvas(self):
        samples = (
            (199_999, (255, 0, 247, 255)),
            (1_000_000, (255, 0, 136, 255)),
            (10_000_000, (255, 0, 0, 255)),
        )
        for amount, expected_accent in samples:
            with self.subTest(amount=amount):
                png = render_donation_card(
                    "DemoDonor001",
                    "DemoGuest001",
                    amount,
                )
                with Image.open(BytesIO(png)) as card:
                    icon_pixels = card.crop((0, 100, card.width // 2, 300)).getdata()
                    self.assertIn(expected_accent, icon_pixels)
                    self.assertEqual(card.getpixel((0, 0))[3], 0)

    def test_card_renders_only_the_left_amount_icon(self):
        png = render_donation_card("DemoDonor001", "DemoGuest001", 1_000_000)
        with Image.open(BytesIO(png)) as card:
            icon_pixels = card.crop((0, 100, card.width // 2, 300)).getdata()
            self.assertIn((255, 0, 136, 255), icon_pixels)
            self.assertEqual(card.getpixel((1970, 180))[3], 0)

    def test_graphic_uses_montserrat_for_its_amount_text(self):
        self.assertEqual(_load_font(100, True).getname(), ("Montserrat", "Bold"))

    def test_message_uses_robux_emoji_next_to_amount_without_trailing_period(self):
        event = {
            "donor": "DemoDonor001",
            "receiver": "DemoGuest001",
            "amount": 1_000_000,
            "category": "diamond",
        }
        with (
            patch("pate_utilities.donation_emoji_for_amount", return_value="<:smite:1>"),
            patch("pate_utilities.robux_emoji_for_guild", return_value="<:robux:2>"),
        ):
            self.assertEqual(
                simulation_message(event, guild_id=1),
                "<:smite:1> `@DemoDonor001` donated "
                "**<:robux:2> 1,000,000** to `@DemoGuest001`",
            )

    def test_robux_emoji_is_found_by_name_in_the_guild(self):
        emoji = PartialEmoji(name="robux", id=123456789)
        guild = SimpleNamespace(emojis=[emoji])
        with patch.object(zap_bot, "get_guild", return_value=guild):
            self.assertEqual(
                robux_emoji_for_guild(1),
                "<:robux:123456789>",
            )

    def test_donation_message_is_separate_from_embed_with_image(self):
        event = {
            "donor": "DemoDonor001",
            "receiver": "DemoGuest001",
            "amount": 1_000_000,
            "category": "diamond",
            "theme": "pink",
            "display_time": "11/16/2026 12:00 AM",
        }
        embed = simulation_embed(event, "zap-simulation.png")

        self.assertIsNone(embed.author.name)
        self.assertIsNone(embed.description)
        self.assertEqual(
            simulation_message(event),
            "🔹 `@DemoDonor001` donated **:robux: 1,000,000** to `@DemoGuest001`",
        )
        self.assertEqual(
            embed.image.url,
            "attachment://zap-simulation.png",
        )
        self.assertEqual(embed.color.value, 0xFF0088)
        self.assertEqual(embed.footer.text, "Donated on • 11/16/2026 12:00 AM")


if __name__ == "__main__":
    unittest.main()

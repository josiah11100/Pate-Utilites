# Pate Utilities

A `discord.py` server bot using `?` as its prefix. Python 3.10 or newer is required.

## Setup

1. Install dependencies with `python -m pip install -r requirements.txt`.
2. Create a bot in the Discord Developer Portal, enable **Server Members Intent**
   and **Message Content Intent**, then invite it with the permissions needed for
   moderation, timeouts, sending messages, reactions, and managing channel
   permissions.
3. Add your bot token to `.env`. Keep `.env` private; it is ignored by git.
4. Run `python pate_utilities.py`.
5. Ensure the bot can access the configured boost and feedback channels. The
   SQLite database is created as `pate_utilities.sqlite3`; set `PATE_DB_PATH` to
   change its location.

The bot needs the **Staff Team** role (`1551143623427620911`) for moderation
commands. `?lock` and `?unlock` additionally require the user to have
**Manage Channels**.

## Host on Render

1. Push this repository to GitHub and create a new **Blueprint** in the
   [Render Dashboard](https://dashboard.render.com/), selecting the repository.
   Render will read `render.yaml` and create the Background Worker.
2. In the worker's **Environment** settings, add `DISCORD_TOKEN` with your
   Discord bot token. Do not commit the token or put it in `render.yaml`.
3. In the Discord Developer Portal, enable **Server Members Intent** and
   **Message Content Intent** for the bot, then invite it to your server with
   the permissions it needs.
4. Deploy the worker. Render installs `requirements.txt` and starts
   `python pate_utilities.py`.

The Blueprint provisions a 1 GB persistent disk at `/var/data`, where the bot's
SQLite database is stored across deploys and restarts. Render persistent disks
require a paid instance, so this configuration uses the `starter` worker plan.
The bot runs continuously as a worker and does not need a web server or a port.

## Commands

- `?ban @user <duration|perm> [reason]` — temporary bans are lifted automatically
  after expiry, including after a bot restart. Duration examples: `30m`, `2h`,
  `1d`, `1h30m`. Month and year units are treated as 30 and 365 days.
- `?kick @user [reason]`
- `?mute @user <duration> [reason]` — Discord timeouts support up to 28 days.
- `?warn @user <reason>` and `?warnings @user`
- `?lock #channel` and `?unlock #channel`
- `?afk [reason]` — mentions show the AFK status; the status is cleared when
  that member next speaks.
- `?balance [@user]`, `?daily`, and `?coinflip <amount> [heads|tails]`

New economy accounts start with 1,000 coins. `?daily` awards 500 coins every
24 hours, and coinflip bets pay even odds.

Boost announcements are grouped for three seconds in channel
`1551143624350113804`. New posts in feedback forum `1551143626422358108` receive
a 👍 reaction and the prompt “What Do You Think About This Post?”.

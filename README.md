# Zap Utilities

A `discord.py` server bot using `?` as its prefix. Python 3.10 or newer is required.

## Setup

1. Install dependencies with `python -m pip install -r requirements.txt`.
2. Create a bot in the Discord Developer Portal, enable **Server Members Intent**
   and **Message Content Intent**, then invite it with the permissions needed for
   moderation, timeouts, sending messages, reactions, and managing roles and
   channel permissions.
3. Add your bot token to `.env`. Keep `.env` private; it is ignored by git.
4. Run `python pate_utilities.py`.
5. The SQLite database is created as `pate_utilities.sqlite3`; set
   `ZAP_DB_PATH` to change its location. The existing `PATE_DB_PATH` remains
   supported.

Server event logs, boost announcements, and feedback-post reactions/prompts
are disabled by default. To enable any of them, set the corresponding
`ENABLE_SERVER_LOGS`, `ENABLE_BOOST_ANNOUNCEMENTS`, or
`ENABLE_FEEDBACK_POST_PROMPTS` constant to `True` in `pate_utilities.py` and
ensure the bot can access that feature's configured channel.

The bot needs the **Staff Team** role (`1551143623427620911`) for moderation
commands. `?lock` and `?unlock` additionally require **Manage Channels**;
`?purge` additionally requires **Manage Messages**. Users with **Manage Server**
can use commands in any channel. Other members must use commands in
`1551143625931493413`; `?afk` is available in any channel.

## Host on Square Cloud

1. Create a Square Cloud account and choose a plan with at least 512 MB of
   available memory. The `squarecloud.app` file configures this as a Python bot
   with automatic restarts.
2. Deploy the project from the Square Cloud dashboard or CLI. Include
   `pate_utilities.py`, `requirements.txt`, `squarecloud.app`, and the bot's
   other source files. `squarecloud.ignore` keeps `.env`, local SQLite
   databases, and development artifacts out of CLI uploads.
3. Set the environment variables from `.env` in the application's Square Cloud
   **Environment Variables** settings. Do not upload `.env` or put the bot
   token in the source files. Restart the application after changing variables.
4. In the Discord Developer Portal, enable **Server Members Intent** and
   **Message Content Intent** for the bot, then invite it to your server with
   the permissions it needs.
5. Deploy and check the application logs. Square Cloud installs dependencies
   from `requirements.txt` and starts `pate_utilities.py`.

The SQLite database uses `ZAP_DB_PATH`, then the legacy `PATE_DB_PATH`, or
`pate_utilities.sqlite3` by default in the app's working directory. Keep a
backup if the database contains data you need to preserve across deployments.
The bot runs continuously and does not need a web server or a port.

## Simulated donation cards

Zap can generate demo cards; these are not Roblox transactions and never ping
Discord users. Automatic posting is disabled until an
administrator runs `/start channel`. Use `/stop` to disable it. The default
automatic interval is eight seconds, with a 50-post hourly cap. Administrators
can adjust minimum/maximum intervals, limits, timezones, amount weights, themes,
notification icons, and demo username pools with `/simulation-settings`,
`/simulation-theme`, `/simulation-category`, and `/simulation-user`.
Donation timestamps use New York time by default.
`/simulation-test` sends a private preview, while
`/simulation-status` reports the current configuration.
Donation cards render at double resolution for sharper text and avatar
outlines. The tier-specific Robux icons and Montserrat font are bundled in
`assets/`, so cards do not depend on expiring image links or host-installed fonts.

`/simulated-donation` creates a manual card when manual generation is enabled.
Only the server owner can add/import or manually provide custom Roblox
usernames; all other generated names are fictional demo accounts. The bot uses
Roblox's official public username and avatar thumbnail endpoints when available
and shows a fallback avatar when a demo username cannot be resolved.

Slash commands are synced globally when the bot starts; Discord can take up to
an hour to show new global commands. For immediate testing, set `ZAP_GUILD_ID`
to your Discord server ID in the host's environment variables and restart. The
bot then syncs the commands directly to that server.

## Commands

- `?ban @user <duration|perm> [reason]` — temporary bans are lifted automatically
  after expiry, including after a bot restart. Duration examples: `30m`, `2h`,
  `1d`, `1h30m`. Month and year units are treated as 30 and 365 days.
- `?kick @user [reason]`
- `?mute @user <duration> [reason]` — Discord timeouts support up to 28 days.
- `?unmute @user [reason]`
- `?unban @user-or-id [reason]`
- `?role <user|all> <role>` — assign a role by name or mention. The caller needs
  **Manage Roles** or **Manage Server**; the bot and caller must be able to
  manage the selected role.
- `?purge <messageamount>` — deletes messages in the current channel.
- `.say <message>` — only command using `.`; requires **Administrator**.
- `?warn @user <reason>` and `?warnings @user`
- `?lock #channel` and `?unlock #channel`
- `?afk [reason]` — mentions show the AFK status; the status is cleared when
  that member next speaks.
- `?balance [@user]`, `?daily`, and `?coinflip <amount> [heads|tails]`
- `/simulated-donation [amount] [donor] [receiver]` — creates a simulated card;
  custom names require the server owner.
- `/start <channel>` and `/stop` — enable or disable automatic simulated cards.
- `/simulation-settings`, `/simulation-user`, `/simulation-theme`, and
  `/simulation-category` — admin controls for the simulation system.
- `/simulation-test` and `/simulation-status` — private preview and status.

New economy accounts start with 1,000 coins. `?daily` awards 500 coins every
24 hours, and coinflip bets pay even odds.

Commands are limited to five per user every five seconds; excess commands are
silently ignored.

When enabled, boost announcements are grouped for three seconds in channel
`1551143624350113804`. When enabled, new posts in feedback forum
`1551143626422358108` receive a 👍 reaction and the prompt
“What Do You Think About This Post?”.

When enabled, server event logs are sent as embeds to channel
`1551143626875084808`. The bot logs message edits/deletions, member joins/leaves
and changes, bans/unbans, channel and role changes, and voice-channel moves.

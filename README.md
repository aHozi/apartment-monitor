# Apartment monitor → Telegram

Checks the W|27 apartment list on https://www.apartments-hn.de/en/book-apartment
every 10 minutes (GitHub Actions) and sends you a Telegram message when an
apartment becomes **Available** or **Soon available**, with a direct **Apply now** link.

```
GitHub Actions (every 10 min)
        ↓
headless browser opens the booking page
        ↓
grab the apartment list the page loads (203 apartments, JSON)
        ↓
status = same rule as the website:
   no date                    → ✅ Available
   date < 6 months away       → 🕒 Soon available
   otherwise                  → ⛔ Already taken
        ↓
compare with state.json (last run)
        ↓
changed to available / soon?  → Telegram message
        ↓
save state.json (committed back to the repo)
```

## Setup

1. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = your chat ID
2. Open your bot in Telegram and press **Start** once (bots can't message you first).
3. Repo → **Actions → Apartment monitor → Run workflow** (leave "debug" unticked).
   You get a "🤖 Apartment monitor started" message.

From then on it runs by itself every ~10 minutes.

## Messages you can get

- **1–5 changes** → one message per apartment (type, location, size, rent, date, Apply link)
- **more than 5 at once** → one combined list
- **site broken / changed** → one ⚠️ warning, then ✅ when it works again

## Debug mode

**Actions → Apartment monitor → Run workflow → tick "debug"**. It sends nothing and saves
`dump.txt` (all apartments + status) and page snapshots to the `debug` branch.

## Test locally (optional)

```bash
pip install -r requirements.txt
python -m playwright install chromium
python monitor.py --dump      # list all apartments + status, sends nothing
python monitor.py --dry-run   # print Telegram messages instead of sending
```

## Good to know

- GitHub's schedule isn't exact: runs can come 5–15 minutes late at busy times.
- GitHub pauses schedules in repos with no commits for 60 days. The script commits a
  small weekly "heartbeat" to `state.json` to prevent that.
- To check more or less often, change `*/10` in `.github/workflows/monitor.yml`.

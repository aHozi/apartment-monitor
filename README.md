# Apartment monitor → Telegram

Checks https://www.apartments-hn.de/en/book-apartment every 10 minutes (GitHub Actions)
and sends you a Telegram message when an apartment is **new** or **changes to**
"available" or "soon available".

```
GitHub Actions (every 10 min)
        ↓
monitor.py downloads the page
        ↓
finds each listing + its status (available / soon / booked)
        ↓
compares with state.json from the last run
        ↓
something new is available?  → Telegram message
        ↓
saves state.json (committed back to the repo)
```

## Setup (about 5 minutes)

1. Create a new GitHub repo. **Make it public**: Actions minutes are free for public repos.
   Private repos only get 2000 minutes/month, and checking every 10 min uses ~4300.
   (Your bot token stays secret either way: it's stored as a secret, not in the code.)
2. Upload these files (keep the `.github/workflows/` folder).
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = your chat ID
4. Open your bot in Telegram and press **Start** once (bots can't message you first).
5. Repo → **Actions** tab → enable workflows → **Apartment monitor → Run workflow**.
   You should get a "🤖 Apartment monitor started…" message listing what's available now.

From then on it runs by itself.

## Test locally (optional)

```bash
pip install -r requirements.txt
python monitor.py --dump      # shows what it finds on the page, sends nothing
python monitor.py --dry-run   # prints the Telegram messages instead of sending
```

## If it finds nothing or the wrong things

The parser finds listings by their status words (available / soon / booked, English + German),
so it doesn't depend on exact HTML. If the site's layout doesn't fit that:

- Set a repo **variable** (not secret) `CARD_SELECTOR` to the CSS class of one apartment box,
  e.g. `.apartment-item` (right-click a listing → Inspect to find it).
- If the listings only appear after JavaScript runs (`--dump` shows 0 listings even though
  you can see them in your browser): uncomment the Playwright line in
  `.github/workflows/monitor.yml` and set `USE_BROWSER: "1"`.

If the page breaks or the site is down, you get **one** ⚠️ warning, then a ✅ when it works again.

## Good to know

- GitHub's schedule isn't exact. Runs can come 5–15 minutes late at busy times.
- GitHub pauses scheduled workflows in repos with no commits for 60 days.
  The script commits a small weekly "heartbeat" to `state.json` to prevent that.
- To check more or less often, change `*/10` in the workflow's `cron` line (e.g. `*/5`).

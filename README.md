# Apartment monitor → Telegram

Watches the W|27 apartment list on <https://www.apartments-hn.de/en/book-apartment>.
When an apartment becomes **Available** or **Soon available**, you get a Telegram
message with a direct **Apply now** link.

It runs for free on GitHub Actions, every ~10 minutes. No server or computer needs to stay on.

---

## 1. How it works

```
GitHub Actions starts a run (every ~10 min)
        ↓
headless browser (Chromium) opens the booking page
        ↓
catch the apartment list the page loads from its API (JSON)
        ↓
give each apartment a status (same rule as the website)
        ↓
compare with state.json (the result of the last run)
        ↓
changed to Available / Soon available?  → send Telegram message
        ↓
save the new state.json (committed back to the repo)
```

**Step by step:**

1. **Fetch** — `monitor.py` opens the page with Playwright (a real browser without a window).
   The page loads its apartment list from a URL that contains `/api/apartments/`.
   The script grabs that JSON response. No API key is needed.
2. **Parse** — for each apartment it keeps: number, type, location, size, rent, date, and
   the Apply link.
3. **Status** — it uses the same rule as the website:

   | `next_rental_begin` (date)        | Status              |
   |-----------------------------------|---------------------|
   | empty                             | ✅ Available        |
   | less than 6 months from today     | 🕒 Soon available   |
   | 6+ months away (or invalid)       | ⛔ Already taken    |

4. **Compare** — it loads `state.json` from the last run. An apartment triggers a message if:
   - it is **new** on the list and Available / Soon, **or**
   - its status **changed to** Available / Soon.

   Changes to "Already taken" are silent.
5. **Message** — sends the alerts to Telegram.
6. **Save** — writes the new `state.json`. This only happens **after** the messages went out.
   So if Telegram fails, the next run tries again and you don't miss an alert.

---

## 2. How it runs on GitHub

The workflow lives in `.github/workflows/monitor.yml`.

**What starts a run:**
- **Schedule** — cron `3-59/10 * * * *` = every 10 minutes (at :03, :13, :23 …).
- **Manual** — Actions tab → **Apartment monitor** → **Run workflow** (with an optional "debug" tick box).

**What each run does:**

```
check out the repo
        ↓
install Python 3.12 + requirements.txt
        ↓
install Chromium (cached between runs → faster)
        ↓
python monitor.py   (uses the Telegram secrets)
        ↓
state.json changed? → commit "Update apartment state" + push
```

**Good to know:**
- The commits named **"Update apartment state"** by `apartment-bot` are normal. That is how the
  script remembers what it saw last time.
- Runs never overlap (`concurrency` in the workflow). A run is stopped after 8 minutes.

---

## 3. Setup (one time)

**1. Create a Telegram bot**
- In Telegram, open **@BotFather** → send `/newbot` → follow the steps.
- Copy the **token** it gives you (looks like `123456:ABC-...`).

**2. Get your chat ID**
- Open your new bot and press **Start** (bots can't message you first).
- Send it any message, e.g. `hi`.
- Open this in a browser (put in your token):
  `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
- Find `"chat":{"id": 123456789 ...}`. That number is your **chat ID**.

**3. Add the secrets to GitHub**
- Repo → **Settings → Secrets and variables → Actions → New repository secret**:
  - `TELEGRAM_BOT_TOKEN` = your bot token
  - `TELEGRAM_CHAT_ID` = your chat ID

**4. Allow the workflow to push**
- Repo → **Settings → Actions → General → Workflow permissions** → **Read and write permissions**.
- Without this, the "Save state" step can't commit `state.json`.

**5. Start it**
- Repo → **Actions → Apartment monitor → Run workflow** (leave "debug" unticked).
- You get a **"🤖 Apartment monitor started"** message with everything that is available right now.

From then on it runs by itself every ~10 minutes.

**BUT:** if the secrets are missing, the run does not fail. It prints a warning and skips.
So check the run log if you get no message.

---

## 4. Messages you can get

| When                              | Message                                                    |
|-----------------------------------|------------------------------------------------------------|
| First run                         | 🤖 "Apartment monitor started" + list of available / soon  |
| 1–5 apartments changed            | One message per apartment (full details + Apply link)      |
| More than 5 changed at once       | One combined list                                          |
| Site down / page changed          | One ⚠️ warning (not repeated every run)                    |
| Working again after a problem     | ✅ "Apartment monitor is working again."                   |

**Example alert:**

```
🏠 ✅ AVAILABLE (was: ⛔ Already taken)

Apartment 0/ 001
Type: Single, balcony
Location: Inner courtyard
Size: 27.23 m²
Rent (all-inclusive): 520.50 €

👉 Apply now
📋 Apartment list
```

For **Soon available** there is also a line `Available from: YYYY-MM-DD`.

---

## 5. Run it on your own computer

**Install:**

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

**Try it without sending anything:**

```bash
python monitor.py --dump
```
→ prints every apartment with its status. Sends nothing, saves nothing.

```bash
python monitor.py --dry-run
```
→ prints the Telegram messages instead of sending them.

**All options:**

```
python monitor.py                      → normal run (what GitHub runs)
python monitor.py --dump               → list all apartments + status, then stop
python monitor.py --dry-run            → print messages instead of sending
python monitor.py --file X.json        → use a saved API response (or debug network.json)
python monitor.py --today 2026-11-01   → pretend it's another day (test the 6-month rule)
```

**Send real messages from your computer:**

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-..."
export TELEGRAM_CHAT_ID="123456789"
python monitor.py
```

**BUT:** a normal run or `--dry-run` **writes `state.json`** on your computer. Don't commit that
file by accident, or GitHub will compare against your local result.

---

## 6. Debug mode

Use it when something looks wrong (e.g. you got the ⚠️ warning).

- **Actions → Apartment monitor → Run workflow → tick "debug"**.
- It sends **no** Telegram messages and does **not** touch `state.json`.
- It saves a snapshot to the **`debug`** branch:

| File            | What it is                                                     |
|-----------------|----------------------------------------------------------------|
| `dump.txt`      | All apartments + status (same as `--dump`)                     |
| `rendered.html` | The page after JavaScript loaded the list                      |
| `network.json`  | Every data request the page made, with its response            |
| `scripts.json`  | The JavaScript files that handle the apartment list            |

Login/cookie headers are left out of `network.json`.

You can test locally with that snapshot:

```bash
python monitor.py --dump --file network.json
```

---

## 7. Project files

| File                             | What it does                                              |
|----------------------------------|-----------------------------------------------------------|
| `monitor.py`                     | The main script: fetch → status → compare → Telegram      |
| `debug_capture.py`               | Debug helper: saves the page + its data requests          |
| `state.json`                     | What the last run saw (written by the bot, don't edit)    |
| `requirements.txt`               | Python packages (`requests`, `playwright`)                |
| `.github/workflows/monitor.yml`  | The GitHub Actions schedule and steps                     |

---

## 8. Settings you can change

| What                          | Where                                   | Default                |
|-------------------------------|-----------------------------------------|------------------------|
| How often it checks           | `cron` in `.github/workflows/monitor.yml` | `3-59/10` (every 10 min) |
| "Soon available" window       | `SOON_MONTHS` in `monitor.py`           | `6` months             |
| Max single messages per run   | `MAX_SINGLE_ALERTS` in `monitor.py`     | `5`                    |
| Which statuses alert you      | `WANTED` in `monitor.py`                | `{"available", "soon"}` |

**BUT:** `SOON_MONTHS = 6` copies the website's rule. Change it only if you want a different
rule than the site shows.

---

## 9. Troubleshooting & good to know

- **No messages at all?** Check the secrets are set, you pressed **Start** on your bot, and the
  run log in the Actions tab.
- **Runs come late.** GitHub's schedule isn't exact: runs can be 5–15 minutes late at busy times.
- **60-day pause.** GitHub pauses schedules in repos with no commits for 60 days. The script
  saves a weekly `heartbeat` in `state.json`, so there is at least one commit per week.
- **Start fresh.** Delete `state.json` and commit. The next run sends the "started" summary again.
- **Got the ⚠️ warning?** The site is down or changed. It keeps trying every run. If it doesn't
  fix itself, run **debug mode** and look at the files on the `debug` branch.

---

## The pattern to remember

```
fetch     → headless browser grabs the /api/apartments/ JSON
status    → empty date = Available, < 6 months = Soon, else Taken
compare   → new or changed to Available/Soon? → alert
message   → Telegram (≤5 single, >5 combined, ⚠️ on errors)
save      → state.json committed after messages are sent
```

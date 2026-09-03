# Install FSignal

This is the click-by-click version. It assumes no Python experience. Follow it top to
bottom and you will have the bot posting into your own Slack in about twenty minutes.

If you are comfortable with a terminal, the four-line version in
[`../README.md`](../README.md#setup) is the same thing, faster.

**You need two things, both free:** a Slack workspace where you are allowed to install an
app, and a [serper.dev](https://serper.dev) account. Nothing else is required — an X
developer account is optional, and the bot still monitors X without it.

---

## 1. Install Python

FSignal needs **Python 3.12 or newer**.

| | |
|---|---|
| **Windows** | [python.org/downloads](https://www.python.org/downloads/) → download → run the installer → **tick "Add python.exe to PATH"** on the first screen. That tickbox is the single most common thing to miss. |
| **macOS** | [python.org/downloads](https://www.python.org/downloads/), or `brew install python@3.12` if you use Homebrew. |
| **Linux** | `sudo apt install python3.12 python3.12-venv` (Debian/Ubuntu), or your distribution's equivalent. |

Check it worked. Open a terminal — **Terminal** on macOS/Linux, **PowerShell** on Windows
— and run:

```
python --version
```

You want `Python 3.12.x` or higher. If Windows says the command is not found, the PATH
tickbox was missed; re-run the installer and choose *Modify*.

> Prefer containers? Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
> instead and skip to [Running it permanently](#5-running-it-permanently). You will still
> need sections 2 and 3.

## 2. Get the code and its dependencies

```bash
git clone https://github.com/Rzbyte/FSignal.git
cd FSignal
```

Create an isolated environment so this project's libraries do not touch the rest of your
machine, then install into it:

**macOS / Linux**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Your prompt now starts with `(.venv)`. That means the environment is active. **Every
command below assumes it is** — if you close the terminal, re-run the `activate` line to
get it back.

> PowerShell may refuse with *"running scripts is disabled on this system"*. Run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y`, and try again.

## 3. Create your settings file

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Open `.env` in any text editor. It has about thirty lines; **only three need your
attention**, and they are each marked `>>> REQUIRED <<<`. Leave everything else alone —
the defaults are the tuned ones.

`.env` holds your passwords. It is already listed in `.gitignore`, so it will not be
committed. Do not paste these values into any other file.

### 3a. The search key — `SERPER_API_KEY`

One key powers both LinkedIn discovery and the X fallback.

1. Sign up at [serper.dev](https://serper.dev). The free tier is enough to run the bot.
2. Copy the API key from your dashboard.
3. Paste it after `SERPER_API_KEY=` in `.env`, with no spaces and no quotes.

### 3b. The Slack app — `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID`

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an
   app manifest** → pick your workspace.
2. When it asks for the manifest, delete what is in the box and paste the contents of
   [`../slack-app-manifest.yml`](../slack-app-manifest.yml). This pre-configures the bot's
   name and its one permission. → **Next** → **Create**.
3. In the left sidebar open **OAuth & Permissions** → **Install to Workspace** → **Allow**.
4. Still on that page, copy the **Bot User OAuth Token**. It starts with `xoxb-`. Paste it
   after `SLACK_BOT_TOKEN=` in `.env`.
5. In Slack, go to the channel you want alerts in (or create one, e.g. `#launch-signals`)
   and type:
   ```
   /invite @FSignal
   ```
   **Do not skip this.** A bot that is not in the channel cannot post to it, and this is
   the most common first-run failure.
6. Get the channel ID: click the channel name at the top → the **About** tab → scroll to
   the very bottom. The ID starts with `C`. Copy it after `SLACK_CHANNEL_ID=` in `.env`.

   For a DM instead of a channel, open the DM, click the person's name → the ID starts
   with `D`.

### 3c. Optional extras

Everything below is genuinely optional. Skip the lot and the bot still monitors all four
sources.

- **`X_BEARER_TOKEN`** — X's own recent search, from
  [developer.x.com](https://developer.x.com). It carries better metadata (author bio,
  profile URL, exact post time) but **recent search is not on the free tier**; without a
  paid plan the API answers `402 credits depleted`. FSignal then falls back to indexed
  public X search using the Serper key you already have, and labels every such alert
  `Source: X (indexed search)` so you always know which you are reading.
- **`POND_ACCESS_KEY`** and **`PUBLIC_BASE_URL`** — only needed to publish the agent on
  Pond. See [`POND.md`](POND.md).

## 4. Check it and start it

```bash
python scripts/preflight.py
```

Everything under **REQUIRED** should say `PASS`. `SKIP` under **OPTIONAL** is fine and
expected — it lists the features you chose not to switch on.

Send yourself one real message to prove the Slack half works:

```bash
python scripts/send_test_alert.py
```

Check Slack. If it arrived, start the bot:

```bash
python -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000/health>. You should see all four sources and a snapshot of
about 6,200 YC companies. The bot scans immediately on startup and then keeps running.

Leave that terminal open — closing it stops the bot. The next section fixes that.

## 5. Running it permanently

A laptop that sleeps is not a monitor. Pick one:

### Docker on your own machine

```bash
docker compose up -d
```

Restarts with your machine, keeps its database in `./data`, and has a healthcheck. Logs:
`docker compose logs -f`. Stop: `docker compose down`.

### Railway — free tier, nothing to maintain

This is what the reference deployment runs on
(<https://fsignal-production.up.railway.app>).

1. Push your fork to GitHub.
2. At [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** →
   pick it. Railway builds it and redeploys on every push to your default branch.

   Take the GitHub path, not `railway up` from your laptop. A service created by
   uploading a local snapshot stays pinned to that snapshot: pushing to GitHub changes
   nothing, and **Redeploy** rebuilds the same old code. If a service is already in that
   state, open **Settings** → **Source** → connect the repository.
3. Open the service → **Variables** → add the same three required values from your `.env`.
4. **Settings** → **Networking** → **Generate Domain**. That URL is your bot's home.
5. **Do not skip this one.** Add a **Volume** mounted at `/app/data`, and set
   `DATABASE_PATH=/app/data/ghost_radar.db` so the database is written inside it.

   Without a volume the database sits on the container's own disk, which Railway
   replaces on every deploy. The bot then forgets every company it has already alerted
   on and re-alerts the lot — the one behaviour a persistent monitor is not allowed to
   have. It is easy to miss because nothing fails: the service is healthy, `/health`
   returns 200, and the only symptom is a `/ledger` that is empty again after a deploy.

Any host that runs a Dockerfile works the same way — Fly, Render, a VPS. Wherever you
put it, the rule is the same: `DATABASE_PATH` must point inside a mount that outlives
the container.

## Troubleshooting

Matched on the exact text you will see.

| What you see | What to do |
|---|---|
| `Slack API error: not_in_channel` | The bot is not in the channel. `/invite @FSignal` in Slack. |
| `Slack API error: channel_not_found` | `SLACK_CHANNEL_ID` is wrong. Re-copy it from the bottom of the channel's **About** tab. |
| `Slack API error: invalid_auth` | `SLACK_BOT_TOKEN` is wrong. Re-copy the **Bot User OAuth Token** (`xoxb-…`). |
| `Slack API error: missing_scope` | Add `chat:write` under **OAuth & Permissions**, then **Reinstall to Workspace**. A scope added without reinstalling does nothing. |
| `not_configured: SERPER_API_KEY is missing` | The key is not in `.env`, or you edited `.env.example` by mistake. |
| `billing_blocked: X API credits are depleted` | Expected without a paid X plan. The X source falls back to indexed search on its own; nothing to fix. |
| `waiting: no active accelerator batch known yet` | Normal for the first minute. The social sources wait for the first directory snapshot. |
| `serper: API Error 429` | Out of search credits. Check your serper.dev dashboard, or raise the two `*_SCAN_INTERVAL_MINUTES` values in `.env`. |
| `python: command not found` (Windows) | Python was installed without **Add python.exe to PATH**. Re-run the installer → *Modify*. |
| `running scripts is disabled` (PowerShell) | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| No alerts after an hour | Not necessarily wrong. Open `/ledger`: if candidates are being evaluated and suppressed with reasons, the bot is working and there is genuinely nothing early right now. |

## What it costs to run

| | |
|---|---|
| Serper | ~70 search credits/day at the shipped intervals. The free tier lasts weeks. |
| YC + Speedrun | Free — public endpoints, no key. |
| Slack | Free. |
| X native API | Optional, and the only paid item. Skip it; the fallback covers X. |
| Hosting | Free tier on Railway, or your own machine. |

Scanning more often costs proportionally more search credits and finds very little extra —
directory listings move on the order of hours, not minutes. The defaults are set where
they are on purpose; see the comments in `.env.example`.

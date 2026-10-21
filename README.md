# nba-data

Local NBA research database: NBA.com stats → DuckDB, in three layers (raw → clean → analysis).
Seasons 2000-01 through the current 2026-27 season (set in `config.toml`), regular season + play-in + playoffs.

> **Status: Stage 2 of 4.** Pull → raw → clean → data checks work for any season. 2021-22 through 2024-25 are built
> with every error check passing; older seasons are pulled one at a time with `run_next_season.bat`.
> Still to come: analysis views, data dictionary, export (Stage 3), `update` command (Stage 4).
> Sections marked *(coming)* don't work yet.

Data stays local and out of git. Everything in `data/` can be rebuilt from the scripts.

---

## Setup

You need **Python 3.12** (3.13 also works). This project uses `tomllib`, so 3.10 and older won't work.

### macOS

1. Install Python from <https://www.python.org/downloads/macos/> (the "macOS 64-bit universal2 installer").
   After installing, double-click **Install Certificates.command** in `/Applications/Python 3.12/`. Without it, HTTPS requests fail with SSL errors.
2. In Terminal:

```bash
cd ~/Research/nba-data          # wherever you put the folder
python3.12 -m venv .venv        # create an isolated Python environment for this project
source .venv/bin/activate       # turn it on (your prompt now starts with "(.venv)")
pip install -r requirements.txt
```

### Windows (PowerShell)

1. Install Python 3.12 from <https://www.python.org/downloads/windows/>. In the installer, tick **"Add python.exe to PATH"**.
2. In PowerShell:

```powershell
cd $HOME\Research\nba-data
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   (once)
pip install -r requirements.txt
```

**What's a venv?** It's a private folder of Python packages just for this project. Other projects can't
break it, and it can't break them. Activate it each time you open a new terminal for this project.

From here on, the commands are the same on both systems (with the venv active, `python` means the right Python).

---

## Commands

| Command | What it does |
|---|---|
| `python -m pytest` | Offline tests. No network needed. |
| `python -m nbadata smoke` | Stage 0: one live call per NBA.com endpoint we use. Writes `data/smoke_report.json`. |
| `python -m nbadata cache-info` | How many responses are cached, and how big. |
| `python -m nbadata progress` | How many games are fully downloaded, per season. Safe to run in a second tab during a pull. |
| `python -m nbadata next` | Pull + build the next season that isn't downloaded yet (newest first). Same as double-clicking `run_next_season.bat`. |
| `python -m nbadata build --season 2024-25` | Pull that season (only what isn't cached yet), rebuild the database, run checks. `--all` = every season. |
| `python -m nbadata pull --season 2024-25` | Just the downloading part. |
| `python -m nbadata build-db` | Just the database part: rebuild everything from the cache (minutes, no internet needed). |
| `python -m nbadata check` | Re-run the data-quality checks on the existing database. |
| `python -m nbadata query "SELECT ..."` | Run SQL and print the result. |
| `python -m nbadata update` *(coming)* | Pull only new games (plus re-pull the last 3 days for stat corrections), rebuild, check. |
| `python -m nbadata export` *(coming)* | Export any SQL query to CSV/JSON. |

### How long things take

| What | Requests | Time |
|---|---|---|
| One full season, first time (2017-18 on) | ~5,500 | ~2.2 hours (~41 requests/min) if NBA.com doesn't block you |
| One full season, 2007-08 to 2016-17 (no matchups) | ~4,200 | ~1.7 hours |
| One full season, before 2007-08 (no matchups, lineups or on/off) | ~3,800 | ~1.5 hours |
| Same thing again (all cached) | 0 | seconds |
| `build-db` (rebuild database from cache) | 0 | a few minutes |

2024-25 took ~21 hours start to finish: ~2.5 h of downloading, the rest stuck on blocked connections and
restarts. Two fixes since then (a fresh connection every 450 requests, and right after any timeout) should
keep a season close to the 2.5 h.

### Adding the next season

**Double-click `run_next_season.bat`** in the project folder (or run `python -m nbadata next`). It picks the
newest season that isn't downloaded yet, pulls it, rebuilds the database, runs the checks, and ends with a box
that says whether it's safe to close, whether anything needs a look, and which season is next. One season per
run, newest first, down to `oldest` in `config.toml`. `python -m nbadata progress` shows where every season stands.

A season counts as done when every game is downloaded, apart from games listed as known NBA.com gaps.

While a pull runs in a terminal you get a live dashboard: a progress bar per step with an ETA, requests per
minute, retries and failures, a countdown during breaks and retry waits, and the latest events. At the end
it prints a summary and how far along every season is. **Ctrl+C stops safely**; run the same command to continue.

**The computer stays awake by itself** while `build`, `next`, `pull` or `build-db` runs (the screen may still turn
off), and normal sleep settings come back when it ends. Closing a laptop lid still puts it to sleep.
If a run stops or dies for any reason, just run the same command again. It picks up where it left off.

### What NBA.com has for older seasons

Checked with test requests on 2026-10-21. Earlier seasons simply skip a dataset: it isn't downloaded and
the checks don't expect it (the first seasons are in `config.toml` under `[datasets]`).

| Dataset | First season |
|---|---|
| Game lists, box scores (traditional + advanced), play-by-play, shot charts, standings | 1996-97 or earlier |
| 5-man lineups, player on/off | 2007-08 |
| Matchups (who guarded whom) | 2017-18 |
| Play-in games | 2019-20 (the season type is just empty before) |

Conference and division come from each season's standings, so pre-2004 divisions (Midwest, old Central)
and moved teams (Vancouver, Seattle, New Jersey, Charlotte) are right for their season.

## Data-quality checks

Every build ends with a table of checks: PASS / FAIL (data is wrong or missing) / WARN (a known quirk worth knowing).
Each non-passing check writes **every** offending row to `data/dq/<check_name>.csv`, and results are stored
in the database in `meta.dq_results`.

Some failures are gaps in NBA.com's own data (e.g. a game it has no matchup data for). Those exact rows are
listed, with the reason, in `nbadata/known_gaps.toml`: they no longer fail the check, but the check still
shows `(+N known NBA.com gaps)` and writes them to `data/dq/<check>.known.csv`. A game NBA.com answers
with empty matchup data is added to that list automatically during the pull; anything else new shows up under
"Needs review" in the box at the end of the run. Highlights:

- sum of player stats = team box score; team box score = NBA.com's official game log line
- final score in play-by-play = official score; made shots / 3s / free throws counted from play-by-play = box score
- shot chart attempts per player per game = box score; each shot links to its play-by-play event
- 5-man lineup minutes add up to team minutes; on-court + off-court minutes = team minutes
- every game has all four per-game datasets; every team has shots, lineups and on/off data
- matchup offensive player and defender are on opposite teams in that game

## How requests to NBA.com work

Every request goes through `nbadata/http.py`:

- **Cache first.** Every response is saved gzipped under `data/cache/<endpoint>/`, exactly as received. That
  folder *is* the raw layer. A request that's already cached is never sent again. So if a long pull
  dies halfway, re-running it continues from where it stopped.
- **Rate limit.** At least 1.0–1.6 s from the start of one live request to the start of the next, plus a 30 s break and a fresh connection every 450 requests.
- **Retries.** Timeouts, connection errors, 429/5xx and junk bodies get retried with backoff (5, 10, 20, 40, 80 s).
  A timeout (15 s) means the connection is probably blocked, so the retry goes out on a fresh connection.
- **Stops if blocked.** After 3 requests in a row fail completely, the run stops and tells you to wait.
  Hammering NBA.com while it's throttling you gets you blocked for longer.

All of these settings live in `config.toml`.

**The cache is portable.** Copy `data/cache/` between Mac and PC and nothing gets re-downloaded.

## Example queries

```bash
# Top scorers in a season
python -m nbadata query "SELECT player_name, count(*) AS games, round(avg(pts), 1) AS ppg FROM clean.player_box WHERE season = '2024-25' AND season_type = 'Regular Season' AND played GROUP BY 1 HAVING games >= 50 ORDER BY ppg DESC LIMIT 10"

# A team's results
python -m nbadata query "SELECT game_date, home_pts, away_pts, periods FROM clean.games WHERE home_team_id = 1610612738 ORDER BY game_date LIMIT 10"

# Which checks didn't pass
python -m nbadata query "SELECT check_name, severity, n_offending FROM meta.dq_results WHERE NOT passed"
```

Tables available: `clean.games`, `clean.teams`, `clean.players`, `clean.player_box`, `clean.team_box`,
`clean.pbp_events`, `clean.shots`, `clean.lineups`, `clean.player_on_off`, `clean.matchups`.

## Project layout

```
nbadata/          Python package
  http.py         the only code that talks to NBA.com (cache, rate limit, retries)
  ui.py           the live dashboard shown during a pull, and the box at the end
  awake.py        keeps the computer from sleeping during long commands
  known_gaps.toml rows of NBA.com's own data errors that the checks accept (with reasons)
  pull/           what to download for a season
  raw_load.py     cache -> raw.* tables (all text, exactly as received)
  clean/          one SQL file per clean.* table, run in number order
  checks.py       data-quality checks
  db.py           build orchestration
  analysis/       Stage 3: SQL views
tests/            offline tests
docs/             DATA_DICTIONARY.md, LATER.md
config.toml       seasons, which datasets exist from when, rate limits, paths
run_next_season.bat  double-click: pull + build the next season
data/             (gitignored) cache, database, exports, logs
```

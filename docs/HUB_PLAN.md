# Hub v1 plan

A local website for browsing, searching and sorting `data/nba.duckdb`. It runs on this PC only.

**Status: approved 2026-11-03. Built in 3 phases.** After each phase you look and test it yourself, then say go
for the next one. Each phase ends with a commit on `main`.

Decided: **Flask + server-rendered HTML**, **a game box-score page in v1**, **this PC only (127.0.0.1)**.

---

## Background: what shapes the plan

| Finding | Consequence |
|---|---|
| `build-db` builds into `nba.building.duckdb`, then `os.replace`s it onto `nba.duckdb`. Tested on this PC: **while any process has `nba.duckdb` open, even read-only, the swap fails with `PermissionError`** (the build exits with code 3). | The hub must never keep the file open (see "Reading the DB safely"). |
| `python -m nbadata check` opens `nba.duckdb` **read-write**. While that runs, a read-only open fails with `IOException`. | The hub retries briefly, then shows "being updated, refresh in a moment". |
| Read-only open of the 517 MB file takes ~30 ms. | Opening the file once per page is cheap. |
| Sizes at 26 seasons: `player_box` ~900k rows, `team_box` ~70k, `games` ~35k, `players` 5.2k. | v1 only touches these tables, and DuckDB handles them in milliseconds. No precomputed tables needed. `pbp_events` (~17M) and `shots` are v2. |
| `clean.players` is the all-time list (5,209 players), but only players from downloaded seasons have games. | Every player gets a page. Those without games yet show their bio plus "no games in the database yet". |
| `clean.teams` is per season; `team_id` stays the same through moves (SEA→OKC, NJN→BKN). | One page per franchise with a season picker, using that season's name. Logos can be overridden per season. |
| Names have accents (`Nikola Jokić`). | Search ignores accents and case. |
| `meta.datasets` holds each dataset's first season (lineups/on-off 2007-08, matchups 2017-18). | One `available(dataset, season)` helper drives every "not available for this season" message, ready for v2. |

## Reading the DB safely (applies to every phase)

1. **Open per request, close straight after.** Read-only, so between page loads the hub holds no handle on the file.
2. **Patient swap in `nbadata/db.py`.** `build_db` retries `os.replace` for up to 60 s before giving the existing
   code-3 message. `check` retries its read-write open the same way.
3. **The hub retries too.** If it can't open the file, it retries for ~2 s, then shows "The database is being
   updated, refresh in a moment."
4. **New seasons appear on their own.** The season list and search names are kept in memory and refreshed when
   the file's modified time changes.
5. **Read-only, always.** It never touches `nba.building.duckdb` or the cache.

## Shared rules (in the queries)

- GP counts `played` rows only; GS uses `is_starter`; MIN comes from `seconds_played`.
- Shooting percentages come from summed makes/attempts, not averages of per-game %.
- A traded player gets one row per team plus a **TOT** row.
- Leader minimums follow NBA.com's current rules, prorated for short seasons. Per-game boards need 70% of the
  season's games. FG% needs 300 FGM, 3P% 82 3PM and FT% 125 FTM at 82 games. The minimum is shown under each board.
- Sortable tables use `?sort=&dir=` with a fixed list of allowed columns per table (no SQL injection). Big
  tables show 50 rows per page.
- Season-aware pages have a season picker (newest first, taken from the DB) and Regular Season / Playoffs /
  Play-In tabs, defaulting to Regular Season.

## Headshots and logos (you add them)

```
data/images/players/203999.png             <- player_id (shown on the player page)
data/images/teams/1610612760.png           <- team_id (franchise)
data/images/teams/1610612760_2007-08.png   <- optional: this season only (e.g. a Sonics logo)
```

`.png`, `.jpg`, `.webp` or `.svg`. No file → a placeholder (grey silhouette / team abbreviation in a circle).
New files show up on refresh. `data/` is gitignored, so the images stay out of git.

## Repo layout added by the hub

```
nbadata/hub/
  app.py         Flask app + routes (read params, call queries, render)
  data.py        open-per-request connection, retries, mtime cache, available()
  queries.py     every SQL query as a function returning plain rows
  images.py      find a player/team image, or the placeholder
  templates/     base layout, one template per page, macros (sortable table, season picker)
  static/        style.css, search.js, placeholder SVGs
nbadata/__main__.py   + `hub` command
nbadata/db.py         + swap/open retries
run_hub.bat
tests/test_hub.py     offline tests on a tiny DB built from the existing fake cache
```

**Testing split:** I write and run the offline `pytest` tests (cheap, and they guard the pipeline). Clicking
through pages, checking numbers against what you know, and trying it during a live pull is **yours** after
each phase. I won't drive a browser or test against the real DB myself.

---

## Phase 1: Foundation, search and players

**Built:**
- `db.py`: patient swap (60 s retry around `os.replace`) and retry on `check`'s read-write open, with tests.
  This goes first because it protects your background runs.
- `hub/data.py`: per-request read-only connection, retries, "being updated" page, mtime cache, `available()`.
- Flask app, base layout: top bar (**Hub · Players · Teams · Leaders · search box**) and a footer showing the
  seasons in the DB and the build time. The Teams and Leaders links show "coming in phase 2/3" until then.
- `/` **Home** (phase-1 version): search box, the seasons in the DB with game counts, and data-check status from
  `meta.dq_results`.
- `/search?q=` plus `/api/search` with search-as-you-type: players, accent-insensitive. One exact match goes
  straight to the page.
- `/player/<id>` **Player**: headshot, bio (position, height, weight, college, country, draft, years) and
  per-season tables for Regular Season and Playoffs (Season, Team, GP, GS, MIN, PTS, REB, AST, STL, BLK, TOV,
  FG%, 3P%, FT%, TS%). Per game / Totals toggle, a career row, and TOT rows for traded players.
- `/player/<id>/games?season=` **Game log**: date, opponent, W/L and score, started, MIN, PTS, REB, AST, STL,
  BLK, TOV, FG, 3P, FT, +/-. DNPs show their status comment, and the columns are sortable. Game links become
  live in phase 2.
- Player images + placeholder. The `hub` command, `run_hub.bat`, and `flask` pinned in `requirements.txt`.
- Tests: player stats math (incl. a traded player), accent search, sort whitelist, a player with no games,
  the file-lock test (a page load doesn't block `os.replace`), and image lookup.

**You check:**
- [ ] Double-click `run_hub.bat`: the browser opens on the home page.
- [ ] Search `jokic`, `lebron`, a partial last name, and a player with no games yet.
- [ ] A player page: do the season lines look right against what you know or NBA.com? Try a traded player
      (TOT row) and the Per game / Totals toggle.
- [ ] Game log: switch seasons and sort a few columns.
- [ ] Drop a headshot into `data/images/players/<id>.png` and refresh.
- [ ] Leave the hub open while `run_next_season.bat` finishes a season: the end box should say the database
      was rebuilt (not "saved as nba.building.duckdb"), and the new season should show in the hub on refresh.

## Phase 2: Teams and games

**Built:**
- `/teams?season=` **Teams**: standings by conference/division (W-L, win %, home, away, PTS/OPP per game,
  net rating), with logos.
- `/team/<id>?season=` **Team**: logo, that season's name, conference/division, record, playoff result.
  **Results** (linked to games), **Roster & player stats** (sortable), **Team per-game vs opponents**,
  **Franchise history** (a row per season in the DB, each linked to that season).
- `/game/<game_id>` **Box score**: score line, date, season type, OT, each team's player lines (starters
  first, DNPs last) and team totals. Game log and results links go live.
- Search now finds teams too, including old names ("Sonics" → the OKC franchise in its Seattle seasons).
- Team logos + per-season overrides + placeholder. Home gains standings for the latest season.
- Tests: standings math, a relocated franchise across seasons, box score totals = team box, unknown IDs → 404.

**You check:**
- [ ] Standings for a season you know.
- [ ] A team page in a few seasons; franchise history for OKC/Seattle or BKN/New Jersey once those seasons are in.
- [ ] Click from results → box score → player → game log → box score.
- [ ] Add a logo, plus a per-season override.

## Phase 3: Leaders, stats table and docs

**Built:**
- `/leaders?season=&type=` **Leaders**: top-10 boards for PTS, REB, AST, STL, BLK and 3PM per game, plus FG%,
  3P%, FT% and TS%, each showing its minimum and linking to the full table.
- `/players?season=&type=&sort=&min_gp=` **Player stats**: every player's per-game line, sortable on any column,
  50 per page, with a minimum-games filter.
- Home gains the top-5 PTS/REB/AST for the latest season.
- README: a "Browsing the data (hub)" section, plus the commands table and project layout updated.
- `docs/LATER.md`: "Any UI or dashboard" becomes "Hosted/public UI (the local hub is in, see
  docs/HUB_PLAN.md)", and a **Hub v2** list is added (below).
- Tests: leader minimums for a full and a short season, pagination, and the min-GP filter.

**You check:**
- [ ] Leaders for a season you know: do the names and minimums look right?
- [ ] Sort the stats table by several columns, page through it, and change the min-GP filter.
- [ ] Read the README section and check it matches how you actually use it.

---

## Hub v2 (not in v1, room left for it)

- Shot charts (player and team, by season and by game), from `clean.shots`.
- Lineups and on/off on team/player pages (2007-08+, via `available()`).
- Matchups, "who guarded whom" (2017-18+).
- Play-by-play view on the game page.
- An "Advanced" toggle on existing tables (usage, net rating, PIE...). The data is already in `player_box`.
- Head-to-head, player comparison, splits (home/away, month, opponent).
- CSV download of any table (ties into the planned `export` command).
- A full data-quality page, and Stage 3 analysis views as pages.

Each v2 section = one query function + one template block. "Not available for this season" comes from
`meta.datasets` automatically.

# Later / parked

Ideas that are deliberately **not** in v1. Add to this list instead of growing v1.

## Out of scope (decided)
- Any UI or dashboard
- Predictive models, betting
- Video and player-tracking data (incl. tracking/hustle box scores)
- Other sports or topics
- Publishing raw NBA data publicly (the code can go public, the data stays local)

## Possible v2 data
- **Per-game lineup stints** (which 5 were on the floor, when) via `GameRotation` + play-by-play.
  v1 lineups/on-off are season-level only, so they can't be sliced by date range or opponent.
  Cost: +1 call per game, plus non-trivial derivation logic.
- **NBA Cup final** (game IDs starting `006`). It doesn't count toward official regular-season stats.
- Seasons before 2021-22
- Officials, arenas, attendance (`BoxScoreSummaryV3`)
- Hustle, misc, scoring and usage box scores

## Tried and rejected
- **Advanced box score from season-wide `PlayerGameLogs`/`TeamGameLogs` (Advanced)** instead of one
  `BoxScoreAdvancedV3` call per game (would save ~1,300 requests a season). Checked against 2024-25
  on 2026-10-20: ratings, usage, TS%, eFG%, possessions match exactly, but every estimated column
  (E_OFF_RATING, E_PACE, E_USG_PCT, ...) is different, the team TM_TOV_PCT is a different stat, and a
  few hundred player rows differ on rebound %, pace and PIE. Kept the per-game calls.

## When there are many seasons
- **Rebuild only the season that changed.** `build-db` rebuilds every season from the cache: ~1 min
  per season, so ~30 min once all of 2000-01 onward is in. Loading just the new season's raw rows and
  re-running the clean SQL for it would keep a build at a few minutes.
- **Opt-in parallel downloads** (`workers = 2` in config.toml): each worker on its own connection with
  the full gap, ~2x faster. Risk: NBA.com may limit the whole computer, not just one connection.

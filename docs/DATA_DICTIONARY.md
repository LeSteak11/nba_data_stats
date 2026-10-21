# Data dictionary

> Filled in during Stage 2–3, table by table, from the real columns NBA.com returns.
> Every table and column in `clean.*` and `analysis.*` will be listed here.

## Conventions (apply to every clean table)

| Thing | Rule | Example |
|---|---|---|
| `game_id` | 10-character string, leading zeros kept | `'0022400061'` |
| `player_id`, `team_id` | integer (NBA.com's ids) | `2544`, `1610612747` |
| `season` | string, start-end years | `'2024-25'` |
| `season_type` | one of `Regular Season`, `PlayIn`, `Playoffs` | |
| dates | `DATE` type, in US local game date | `2024-10-22` |
| minutes played | stored as integer **seconds** in `*_seconds` columns | `2052` (= 34:12) |
| percentages | fraction 0–1, not 0–100 | `0.456` |

## Schemas (layers)
- `raw.*`: NBA.com responses flattened into rows. Column names as NBA.com sends them. Never edited.
- `clean.*`: standardized, typed, joinable tables. **Use these for research.**
- `analysis.*`: derived metrics and saved views built only on `clean.*`.
- `meta.*`: pull log and data-quality results.

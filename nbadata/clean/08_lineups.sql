-- clean.lineups: one row per 5-man unit per team per season per season type (season totals).
CREATE TABLE clean.lineups AS
WITH base AS (
    SELECT *, req(_params, 'Season') AS season, req(_params, 'SeasonType') AS season_type
    FROM raw.leaguedashlineups__lineups WHERE req(_params, 'MeasureType') = 'Base'
      -- Only per-team pulls. League-wide responses are capped at 2000 rows and would duplicate these.
      AND coalesce(req(_params, 'TeamID'), '') NOT IN ('', '0')
),
adv AS (
    SELECT *, req(_params, 'Season') AS season, req(_params, 'SeasonType') AS season_type
    FROM raw.leaguedashlineups__lineups WHERE req(_params, 'MeasureType') = 'Advanced'
      AND coalesce(req(_params, 'TeamID'), '') NOT IN ('', '0')
)
SELECT
    b.season, b.season_type,
    nint(b.TEAM_ID)              AS team_id,
    b.TEAM_ABBREVIATION          AS team_abbreviation,
    b.GROUP_ID                   AS lineup_id,
    b.GROUP_NAME                 AS lineup_name,
    list_sort(list_transform(string_split(trim(b.GROUP_ID, '-'), '-'), x -> CAST(x AS INTEGER))) AS player_ids,
    nint(b.GP) AS gp, nint(b.W) AS w, nint(b.L) AS l,
    ndbl(b.MIN)                  AS minutes,
    nint(b.FGM) AS fgm, nint(b.FGA) AS fga, nint(b.FG3M) AS fg3m, nint(b.FG3A) AS fg3a,
    nint(b.FTM) AS ftm, nint(b.FTA) AS fta, nint(b.OREB) AS oreb, nint(b.DREB) AS dreb,
    nint(b.REB) AS reb, nint(b.AST) AS ast, nint(b.TOV) AS tov, nint(b.STL) AS stl,
    nint(b.BLK) AS blk, nint(b.BLKA) AS blka, nint(b.PF) AS pf, nint(b.PFD) AS pfd,
    nint(b.PTS) AS pts, ndbl(b.PLUS_MINUS) AS plus_minus,
    ndbl(a.OFF_RATING) AS off_rating, ndbl(a.DEF_RATING) AS def_rating, ndbl(a.NET_RATING) AS net_rating,
    ndbl(a.AST_PCT) AS ast_pct, ndbl(a.AST_TO) AS ast_to, ndbl(a.AST_RATIO) AS ast_ratio,
    ndbl(a.OREB_PCT) AS oreb_pct, ndbl(a.DREB_PCT) AS dreb_pct, ndbl(a.REB_PCT) AS reb_pct,
    ndbl(a.TM_TOV_PCT) AS tov_pct, ndbl(a.EFG_PCT) AS efg_pct, ndbl(a.TS_PCT) AS ts_pct,
    ndbl(a.PACE) AS pace, ndbl(a.POSS) AS possessions, ndbl(a.PIE) AS pie
FROM base b
LEFT JOIN adv a
    ON a.season = b.season AND a.season_type = b.season_type
   AND a.TEAM_ID = b.TEAM_ID AND a.GROUP_ID = b.GROUP_ID
ORDER BY b.season, b.season_type, team_abbreviation, minutes DESC;

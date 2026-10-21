-- clean.games: one row per game. Official final score comes from LeagueGameLog.
-- Home team: the game log row whose MATCHUP says 'vs.'. Neutral-site games (Paris, Mexico City,
-- NBA Cup semifinals in Las Vegas) list BOTH teams as '@', so for those we use the designated
-- home team from the box score instead (raw.boxscoretraditionalv3__game_header).
CREATE TABLE clean.games AS
WITH gl AS (
    SELECT DISTINCT
        GAME_ID                    AS game_id,
        req(_params, 'Season')     AS season,
        req(_params, 'SeasonType') AS season_type,
        CAST(GAME_DATE AS DATE)    AS game_date,
        nint(TEAM_ID)              AS team_id,
        MATCHUP                    AS matchup,
        nint(PTS)                  AS pts
    FROM raw.leaguegamelog__league_game_log
),
neutral AS (
    SELECT game_id, count(*) FILTER (WHERE matchup LIKE '% vs. %') = 0 AS is_neutral_site
    FROM gl GROUP BY 1
),
box_home AS (
    SELECT DISTINCT gameId AS game_id, nint(homeTeamId) AS home_team_id
    FROM raw.boxscoretraditionalv3__game_header
),
periods AS (
    SELECT gameId AS game_id, max(nint(period)) AS periods
    FROM raw.playbyplayv3__play_by_play GROUP BY 1
)
SELECT
    h.game_id, h.season, h.season_type, h.game_date,
    h.team_id AS home_team_id,
    a.team_id AS away_team_id,
    h.pts     AS home_pts,
    a.pts     AS away_pts,
    CASE WHEN h.pts > a.pts THEN h.team_id WHEN a.pts > h.pts THEN a.team_id END AS winner_team_id,
    n.is_neutral_site,
    p.periods,
    p.periods > 4 AS is_overtime
FROM gl h
JOIN gl a ON a.game_id = h.game_id AND a.team_id <> h.team_id
JOIN neutral n ON n.game_id = h.game_id
LEFT JOIN box_home b ON b.game_id = h.game_id
LEFT JOIN periods p ON p.game_id = h.game_id
-- A neutral-site game without a downloaded box score has no home team yet: it's left out,
-- and the games_paired check reports it.
WHERE (NOT n.is_neutral_site AND h.matchup LIKE '% vs. %')
   OR (n.is_neutral_site AND h.team_id = b.home_team_id)
ORDER BY h.game_date, h.game_id;

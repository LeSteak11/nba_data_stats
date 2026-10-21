-- clean.team_box: one row per team per game. Traditional + advanced.
-- Turnovers: the box score's `turnovers` counts only players' turnovers (so tov = sum of player rows).
-- Team turnovers (shot clock, 8/5-second, too many players...) exist only in play-by-play.
-- tov_total = tov + team_tov is what NBA.com's game log reports; use it for possessions and TOV%.
CREATE TABLE clean.team_box AS
WITH pbp_games AS (
    SELECT DISTINCT gameId AS game_id FROM raw.playbyplayv3__play_by_play
),
team_tov AS (
    SELECT gameId AS game_id, nint(personId) AS team_id, count(*) AS n
    FROM raw.playbyplayv3__play_by_play
    WHERE actionType = 'Turnover' AND is_team_event(teamId, personId)
    GROUP BY ALL
)
SELECT
    t.gameId                                         AS game_id,
    g.season, g.season_type, g.game_date,
    nint(t.teamId)                                   AS team_id,
    CASE WHEN nint(t.teamId) = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id,
    nint(t.teamId) = g.home_team_id                  AS is_home,
    t.teamTricode                                    AS team_abbreviation,
    -- Sum of all players' minutes (240:00 = regulation). Divide by 5 for game length.
    CAST(round(clock_seconds(t.minutes)) AS INTEGER) AS player_seconds_total,
    nint(t.points)                  AS pts,
    nint(t.fieldGoalsMade)          AS fgm,
    nint(t.fieldGoalsAttempted)     AS fga,
    ndbl(t.fieldGoalsPercentage)    AS fg_pct,
    nint(t.threePointersMade)       AS fg3m,
    nint(t.threePointersAttempted)  AS fg3a,
    ndbl(t.threePointersPercentage) AS fg3_pct,
    nint(t.freeThrowsMade)          AS ftm,
    nint(t.freeThrowsAttempted)     AS fta,
    ndbl(t.freeThrowsPercentage)    AS ft_pct,
    nint(t.reboundsOffensive)       AS oreb,
    nint(t.reboundsDefensive)       AS dreb,
    nint(t.reboundsTotal)           AS reb,
    nint(t.assists)                 AS ast,
    nint(t.steals)                  AS stl,
    nint(t.blocks)                  AS blk,
    nint(t.turnovers)               AS tov,
    -- NULL (unknown) when the game's play-by-play isn't downloaded, never a guessed 0
    CASE WHEN pg.game_id IS NOT NULL THEN coalesce(tt.n, 0) END                     AS team_tov,
    CASE WHEN pg.game_id IS NOT NULL THEN nint(t.turnovers) + coalesce(tt.n, 0) END AS tov_total,
    nint(t.foulsPersonal)           AS pf,
    CAST(round(ndbl(t.plusMinusPoints)) AS INTEGER) AS plus_minus,
    ndbl(a.offensiveRating)                 AS off_rating,
    ndbl(a.defensiveRating)                 AS def_rating,
    ndbl(a.netRating)                       AS net_rating,
    ndbl(a.estimatedOffensiveRating)        AS e_off_rating,
    ndbl(a.estimatedDefensiveRating)        AS e_def_rating,
    ndbl(a.estimatedNetRating)              AS e_net_rating,
    ndbl(a.assistPercentage)                AS ast_pct,
    ndbl(a.assistToTurnover)                AS ast_to,
    ndbl(a.assistRatio)                     AS ast_ratio,
    ndbl(a.offensiveReboundPercentage)      AS oreb_pct,
    ndbl(a.defensiveReboundPercentage)      AS dreb_pct,
    ndbl(a.reboundPercentage)               AS reb_pct,
    ndbl(a.estimatedTeamTurnoverPercentage) AS e_tov_pct,
    ndbl(a.turnoverRatio)                   AS tov_ratio,
    ndbl(a.effectiveFieldGoalPercentage)    AS efg_pct,
    ndbl(a.trueShootingPercentage)          AS ts_pct,
    ndbl(a.pace)                            AS pace,
    ndbl(a.estimatedPace)                   AS e_pace,
    ndbl(a.pacePer40)                       AS pace_per40,
    ndbl(a.possessions)                     AS possessions,
    ndbl(a.PIE)                             AS pie
FROM raw.boxscoretraditionalv3__team_stats t
LEFT JOIN clean.games g ON g.game_id = t.gameId
LEFT JOIN raw.boxscoreadvancedv3__team_stats a ON a.gameId = t.gameId AND a.teamId = t.teamId
LEFT JOIN pbp_games pg ON pg.game_id = t.gameId
LEFT JOIN team_tov tt ON tt.game_id = t.gameId AND tt.team_id = nint(t.teamId)
ORDER BY game_id, team_id;

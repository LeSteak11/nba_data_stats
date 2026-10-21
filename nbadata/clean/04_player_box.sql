-- clean.player_box: one row per player per game. Traditional + advanced stats side by side.
-- Players who were listed but didn't play are kept (played = false, reason in status_comment).
CREATE TABLE clean.player_box AS
WITH adv AS (
    -- NBA.com's advanced box sometimes lists a player twice: once with stats, once under another
    -- spelling with blank minutes ('Portis' / 'Portis Jr.'). Keep one row, preferring the one with minutes.
    SELECT * FROM raw.boxscoreadvancedv3__player_stats
    QUALIFY row_number() OVER (PARTITION BY gameId, personId
                               ORDER BY coalesce(trim(minutes), '') = '', familyName) = 1
)
SELECT
    t.gameId                                         AS game_id,
    g.season, g.season_type, g.game_date,
    nint(t.teamId)                                   AS team_id,
    CASE WHEN nint(t.teamId) = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id,
    nint(t.teamId) = g.home_team_id                  AS is_home,
    nint(t.personId)                                 AS player_id,
    trim(t.firstName || ' ' || t.familyName)         AS player_name,
    coalesce(t."position", '') <> ''                 AS is_starter,
    NULLIF(t."position", '')                         AS starting_position,
    NULLIF(trim(t."comment"), '')                    AS status_comment,
    CAST(round(coalesce(clock_seconds(t.minutes), 0)) AS INTEGER) AS seconds_played,
    coalesce(clock_seconds(t.minutes), 0) > 0        AS played,
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
    nint(t.foulsPersonal)           AS pf,
    CAST(round(ndbl(t.plusMinusPoints)) AS INTEGER) AS plus_minus,
    -- advanced
    ndbl(a.offensiveRating)              AS off_rating,
    ndbl(a.defensiveRating)              AS def_rating,
    ndbl(a.netRating)                    AS net_rating,
    ndbl(a.estimatedOffensiveRating)     AS e_off_rating,
    ndbl(a.estimatedDefensiveRating)     AS e_def_rating,
    ndbl(a.estimatedNetRating)           AS e_net_rating,
    ndbl(a.assistPercentage)             AS ast_pct,
    ndbl(a.assistToTurnover)             AS ast_to,
    ndbl(a.assistRatio)                  AS ast_ratio,
    ndbl(a.offensiveReboundPercentage)   AS oreb_pct,
    ndbl(a.defensiveReboundPercentage)   AS dreb_pct,
    ndbl(a.reboundPercentage)            AS reb_pct,
    ndbl(a.turnoverRatio)                AS tov_ratio,
    ndbl(a.effectiveFieldGoalPercentage) AS efg_pct,
    ndbl(a.trueShootingPercentage)       AS ts_pct,
    ndbl(a.usagePercentage)              AS usg_pct,
    ndbl(a.estimatedUsagePercentage)     AS e_usg_pct,
    ndbl(a.pace)                         AS pace,
    ndbl(a.estimatedPace)                AS e_pace,
    ndbl(a.pacePer40)                    AS pace_per40,
    ndbl(a.possessions)                  AS possessions,
    ndbl(a.PIE)                          AS pie
FROM raw.boxscoretraditionalv3__player_stats t
LEFT JOIN clean.games g ON g.game_id = t.gameId
LEFT JOIN adv a ON a.gameId = t.gameId AND a.personId = t.personId
ORDER BY game_id, team_id, player_id;

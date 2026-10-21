-- clean.matchups: who guarded whom, per game. One row per offensive player x defender pair.
-- Stats are what the OFFENSIVE player did while guarded by that defender.
CREATE TABLE clean.matchups AS
SELECT
    m.gameId                               AS game_id,
    g.season, g.season_type, g.game_date,
    nint(m.teamId)                         AS off_team_id,
    nint(m.personIdOff)                    AS off_player_id,
    trim(m.firstNameOff || ' ' || m.familyNameOff) AS off_player_name,
    nint(m.personIdDef)                    AS def_player_id,
    trim(m.firstNameDef || ' ' || m.familyNameDef) AS def_player_name,
    CAST(round(clock_seconds(m.matchupMinutes)) AS INTEGER) AS matchup_seconds,
    ndbl(m.partialPossessions)             AS partial_possessions,
    ndbl(m.percentageDefenderTotalTime)    AS pct_defender_total_time,
    ndbl(m.percentageOffensiveTotalTime)   AS pct_offense_total_time,
    ndbl(m.percentageTotalTimeBothOn)      AS pct_time_both_on,
    nint(m.switchesOn)                     AS switches_on,
    nint(m.playerPoints)                   AS player_pts,
    nint(m.teamPoints)                     AS team_pts,
    nint(m.matchupAssists)                 AS ast,
    nint(m.matchupPotentialAssists)        AS potential_ast,
    nint(m.matchupTurnovers)               AS tov,
    nint(m.matchupBlocks)                  AS blk,
    nint(m.matchupFieldGoalsMade)          AS fgm,
    nint(m.matchupFieldGoalsAttempted)     AS fga,
    nint(m.matchupThreePointersMade)       AS fg3m,
    nint(m.matchupThreePointersAttempted)  AS fg3a,
    nint(m.matchupFreeThrowsMade)          AS ftm,
    nint(m.matchupFreeThrowsAttempted)     AS fta,
    nint(m.helpBlocks)                     AS help_blk,
    nint(m.helpFieldGoalsMade)             AS help_fgm,
    nint(m.helpFieldGoalsAttempted)        AS help_fga,
    nint(m.shootingFouls)                  AS shooting_fouls
FROM raw.boxscorematchupsv3__player_stats m
LEFT JOIN clean.games g ON g.game_id = m.gameId
ORDER BY game_id, off_player_id, matchup_seconds DESC;

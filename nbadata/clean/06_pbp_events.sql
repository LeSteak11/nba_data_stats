-- clean.pbp_events: one row per play-by-play event (PlayByPlayV3).
-- Unique key: (game_id, action_id). action_number is NOT unique: a steal shares it with the
-- turnover, a block with the missed shot. Shot charts link via action_number (= GAME_EVENT_ID).
-- Team events (team rebound/turnover, timeout) get team_id from personId; see is_team_event.
CREATE TABLE clean.pbp_events AS
SELECT
    p.gameId                         AS game_id,
    g.season, g.season_type,
    nint(p.actionId)                 AS action_id,
    nint(p.actionNumber)             AS action_number,
    nint(p."period")                 AS period,
    clock_seconds(p.clock)           AS clock_seconds_remaining,
    CASE WHEN nint(p."period") <= 4
         THEN (nint(p."period") - 1) * 720 + 720 - clock_seconds(p.clock)
         ELSE 2880 + (nint(p."period") - 5) * 300 + 300 - clock_seconds(p.clock)
    END                              AS game_seconds_elapsed,
    is_team_event(p.teamId, p.personId) AS is_team_event,
    CASE WHEN is_team_event(p.teamId, p.personId) THEN nint(p.personId)
         ELSE NULLIF(nint(p.teamId), 0) END                          AS team_id,
    NULLIF(p.teamTricode, '')        AS team_abbreviation,
    CASE WHEN NOT is_team_event(p.teamId, p.personId) THEN NULLIF(nint(p.personId), 0) END AS player_id,
    NULLIF(p.playerName, '')         AS player_name,
    NULLIF(p.actionType, '')         AS action_type,
    NULLIF(p.subType, '')            AS sub_type,
    NULLIF(p.description, '')        AS description,
    NULLIF(p."location", '')         AS location,
    nint(p.isFieldGoal) = 1          AS is_field_goal,
    NULLIF(p.shotResult, '')         AS shot_result,
    NULLIF(nint(p.shotValue), 0)     AS shot_value,
    ndbl(p.shotDistance)             AS shot_distance_ft,
    ndbl(p.xLegacy)                  AS x_legacy,
    ndbl(p.yLegacy)                  AS y_legacy,
    nint(p.scoreHome)                AS score_home,
    nint(p.scoreAway)                AS score_away
FROM raw.playbyplayv3__play_by_play p
LEFT JOIN clean.games g ON g.game_id = p.gameId
ORDER BY game_id, action_id;

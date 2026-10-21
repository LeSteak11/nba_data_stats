-- clean.shots: one row per field goal attempt, with court location (ShotChartDetail).
-- loc_x / loc_y are in tenths of a foot, basket at (0, 0), loc_y increasing away from the basket.
CREATE TABLE clean.shots AS
SELECT
    s.GAME_ID                                       AS game_id,
    req(s._params, 'Season')                        AS season,
    req(s._params, 'SeasonType')                    AS season_type,
    CAST(strptime(s.GAME_DATE, '%Y%m%d') AS DATE)   AS game_date,
    nint(s.GAME_EVENT_ID)                           AS game_event_id,
    nint(s.PLAYER_ID)                               AS player_id,
    s.PLAYER_NAME                                   AS player_name,
    nint(s.TEAM_ID)                                 AS team_id,
    nint(s.PERIOD)                                  AS period,
    nint(s.MINUTES_REMAINING) * 60 + nint(s.SECONDS_REMAINING) AS clock_seconds_remaining,
    nint(s.SHOT_MADE_FLAG) = 1                      AS made,
    CASE WHEN s.SHOT_TYPE LIKE '3PT%' THEN 3 ELSE 2 END AS shot_value,
    s.ACTION_TYPE                                   AS action_type,
    s.SHOT_TYPE                                     AS shot_type,
    s.SHOT_ZONE_BASIC                               AS zone_basic,
    s.SHOT_ZONE_AREA                                AS zone_area,
    s.SHOT_ZONE_RANGE                               AS zone_range,
    nint(s.SHOT_DISTANCE)                           AS shot_distance_ft,
    nint(s.LOC_X)                                   AS loc_x,
    nint(s.LOC_Y)                                   AS loc_y
FROM raw.shotchartdetail__shot_chart_detail s
-- Only whole-season, per-team pulls. Ignores narrower requests (e.g. the smoke test's single-game call),
-- which would otherwise duplicate shots.
WHERE coalesce(req(s._params, 'GameID'), '') = ''
  AND coalesce(req(s._params, 'TeamID'), '') NOT IN ('', '0')
ORDER BY game_id, game_event_id;

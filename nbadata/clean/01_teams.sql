-- clean.teams: one row per team per season.
-- Conference/division come from that season's standings (LeagueStandingsV3), because they changed
-- over the years (realignment in 2004-05; Charlotte, New Orleans and Memphis moved).
-- Seasons downloaded before standings were added fall back to this hand-written table, which is
-- right from 2004-05 on. A check verifies every team gets one.
CREATE TABLE clean.teams AS
WITH conf(team_id, conference, division) AS (VALUES
    (1610612737, 'East', 'Southeast'), (1610612738, 'East', 'Atlantic'),  (1610612739, 'East', 'Central'),
    (1610612740, 'West', 'Southwest'), (1610612741, 'East', 'Central'),   (1610612742, 'West', 'Southwest'),
    (1610612743, 'West', 'Northwest'), (1610612744, 'West', 'Pacific'),   (1610612745, 'West', 'Southwest'),
    (1610612746, 'West', 'Pacific'),   (1610612747, 'West', 'Pacific'),   (1610612748, 'East', 'Southeast'),
    (1610612749, 'East', 'Central'),   (1610612750, 'West', 'Northwest'), (1610612751, 'East', 'Atlantic'),
    (1610612752, 'East', 'Atlantic'),  (1610612753, 'East', 'Southeast'), (1610612754, 'East', 'Central'),
    (1610612755, 'East', 'Atlantic'),  (1610612756, 'West', 'Pacific'),   (1610612757, 'West', 'Northwest'),
    (1610612758, 'West', 'Pacific'),   (1610612759, 'West', 'Southwest'), (1610612760, 'West', 'Northwest'),
    (1610612761, 'East', 'Atlantic'),  (1610612762, 'West', 'Northwest'), (1610612763, 'West', 'Southwest'),
    (1610612764, 'East', 'Southeast'), (1610612765, 'East', 'Central'),   (1610612766, 'East', 'Southeast')
),
ts AS (
    SELECT DISTINCT
        req(_params, 'Season') AS season,
        nint(TEAM_ID)          AS team_id,
        TEAM_ABBREVIATION      AS team_abbreviation,
        TEAM_NAME              AS team_name
    FROM raw.leaguegamelog__league_game_log
),
standings AS (
    SELECT DISTINCT req(_params, 'Season') AS season, nint(TeamID) AS team_id,
           Conference AS conference, Division AS division
    FROM raw.leaguestandingsv3__standings
)
SELECT ts.season, ts.team_id, ts.team_abbreviation, ts.team_name,
       coalesce(st.conference, CASE WHEN ts.season >= '2004-05' THEN conf.conference END) AS conference,
       coalesce(st.division,   CASE WHEN ts.season >= '2004-05' THEN conf.division END)   AS division
FROM ts
LEFT JOIN standings st USING (season, team_id)
LEFT JOIN conf USING (team_id)
ORDER BY ts.season, ts.team_abbreviation;

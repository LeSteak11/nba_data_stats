-- Helper functions ("macros") used by the clean SQL files.
CREATE SCHEMA IF NOT EXISTS clean;

-- Text number -> number. Empty string -> NULL. A value that isn't a number makes the build FAIL
-- (on purpose: we want to hear about it, not get a silent NULL).
CREATE OR REPLACE MACRO nint(s) AS CAST(NULLIF(trim(s), '') AS INTEGER);
CREATE OR REPLACE MACRO ndbl(s) AS CAST(NULLIF(trim(s), '') AS DOUBLE);

-- Clock/minutes text -> seconds (DOUBLE). Handles '34:12', '34:12.40', 'PT11M43.00S', ''.
CREATE OR REPLACE MACRO clock_seconds(s) AS CASE
    WHEN s IS NULL OR trim(s) = '' THEN NULL
    WHEN s LIKE 'PT%' THEN
        coalesce(CAST(NULLIF(regexp_extract(s, 'PT(\d+)M', 1), '') AS DOUBLE), 0) * 60
      + coalesce(CAST(NULLIF(regexp_extract(s, '([\d.]+)S', 1), '') AS DOUBLE), 0)
    WHEN s LIKE '%:%' THEN CAST(split_part(s, ':', 1) AS DOUBLE) * 60 + CAST(split_part(s, ':', 2) AS DOUBLE)
    ELSE CAST(s AS DOUBLE) * 60
END;

-- Play-by-play team events (team rebounds, team turnovers, timeouts): NBA.com leaves teamId = 0
-- and puts the TEAM's id in personId. All 30 team ids are between 1610612737 and 1610612766.
CREATE OR REPLACE MACRO is_team_event(team_id_text, person_id_text) AS
    coalesce(nint(team_id_text) = 0 AND nint(person_id_text) BETWEEN 1610612737 AND 1610612766, false);

-- Request parameter stored with every raw row (e.g. req(_params, 'Season')).
CREATE OR REPLACE MACRO req(p, k) AS json_extract_string(p, '$."' || k || '"');

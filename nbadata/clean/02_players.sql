-- clean.players: one row per player (all-time list), with bio details where NBA.com has them.
-- A few players appear in box scores but in neither player list (e.g. two-way players listed once
-- as DNP). They get a stub row (in_player_list = false, no bio) so every player_id resolves;
-- the players_have_bio warning still reports them.
CREATE TABLE clean.players AS
WITH cap AS (
    SELECT *, row_number() OVER (PARTITION BY PERSON_ID ORDER BY _fetched_at DESC) AS rn
    FROM raw.commonallplayers__common_all_players
),
pix AS (
    SELECT *, row_number() OVER (PARTITION BY PERSON_ID ORDER BY _fetched_at DESC) AS rn
    FROM raw.playerindex__player_index
),
listed AS (
SELECT
    nint(cap.PERSON_ID)          AS player_id,
    cap.DISPLAY_FIRST_LAST       AS player_name,
    pix.PLAYER_FIRST_NAME        AS first_name,
    pix.PLAYER_LAST_NAME         AS last_name,
    cap.PLAYER_SLUG              AS player_slug,
    nint(cap.FROM_YEAR)          AS from_year,
    nint(cap.TO_YEAR)            AS to_year,
    NULLIF(pix.POSITION, '')     AS position,
    CASE WHEN pix.HEIGHT LIKE '%-%'
         THEN nint(split_part(pix.HEIGHT, '-', 1)) * 12 + nint(split_part(pix.HEIGHT, '-', 2)) END
                                 AS height_inches,
    nint(pix.WEIGHT)             AS weight_lbs,
    NULLIF(pix.COLLEGE, '')      AS college,
    NULLIF(pix.COUNTRY, '')      AS country,
    -- Undrafted players have text like 'Undrafted' here, so TRY_CAST (-> NULL) is intended.
    TRY_CAST(pix.DRAFT_YEAR AS INTEGER)   AS draft_year,
    TRY_CAST(pix.DRAFT_ROUND AS INTEGER)  AS draft_round,
    TRY_CAST(pix.DRAFT_NUMBER AS INTEGER) AS draft_number,
    pix.PERSON_ID IS NOT NULL    AS has_bio,
    true                         AS in_player_list
FROM cap
LEFT JOIN pix ON pix.PERSON_ID = cap.PERSON_ID AND pix.rn = 1
WHERE cap.rn = 1
)
SELECT * FROM listed
UNION ALL BY NAME
SELECT
    nint(personId)                                      AS player_id,
    any_value(trim(firstName || ' ' || familyName))     AS player_name,
    false                                               AS has_bio,
    false                                               AS in_player_list
FROM raw.boxscoretraditionalv3__player_stats
WHERE nint(personId) NOT IN (SELECT player_id FROM listed)
GROUP BY 1
ORDER BY player_id;

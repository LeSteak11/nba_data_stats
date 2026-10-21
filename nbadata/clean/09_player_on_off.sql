-- clean.player_on_off: team performance with each player ON vs OFF the court, season totals.
-- Two rows per player per team per season type: on_court = true / false.
CREATE TABLE clean.player_on_off AS
WITH both_sides AS (
    SELECT *, true  AS on_court FROM raw.teamplayeronoffdetails__players_on_court_team_player_on_off_details
    UNION ALL BY NAME
    SELECT *, false AS on_court FROM raw.teamplayeronoffdetails__players_off_court_team_player_on_off_details
)
SELECT
    req(_params, 'Season')       AS season,
    req(_params, 'SeasonType')   AS season_type,
    nint(TEAM_ID)                AS team_id,
    TEAM_ABBREVIATION            AS team_abbreviation,
    nint(VS_PLAYER_ID)           AS player_id,
    VS_PLAYER_NAME               AS player_name,
    on_court,
    nint(GP) AS gp, nint(W) AS w, nint(L) AS l,
    ndbl(MIN)                    AS minutes,
    nint(FGM) AS fgm, nint(FGA) AS fga, nint(FG3M) AS fg3m, nint(FG3A) AS fg3a,
    nint(FTM) AS ftm, nint(FTA) AS fta, nint(OREB) AS oreb, nint(DREB) AS dreb,
    nint(REB) AS reb, nint(AST) AS ast, nint(TOV) AS tov, nint(STL) AS stl,
    nint(BLK) AS blk, nint(BLKA) AS blka, nint(PF) AS pf, nint(PFD) AS pfd,
    nint(PTS) AS pts, ndbl(PLUS_MINUS) AS plus_minus
FROM both_sides
ORDER BY season, season_type, team_abbreviation, player_id, on_court DESC;

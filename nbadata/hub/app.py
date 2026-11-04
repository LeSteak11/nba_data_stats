"""The hub's pages. Routes stay thin: read the URL, run queries (queries.py), render a template."""

from datetime import timezone
from html import escape
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, url_for

from . import queries as Q
from .data import Database, DatabaseBusy, DatabaseMissing
from .images import find_image

HERE = Path(__file__).parent
HUB_ID = "nbadata-hub"  # /health answers this, so `python -m nbadata hub` can tell the hub is already running


def create_app(db_path: Path, images_dir: Path, wait_s: float = 2.0) -> Flask:
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"))
    db = Database(db_path, wait_s=wait_s)
    app.config["DB"] = db
    app.config["IMAGES_DIR"] = Path(images_dir)
    _register_filters(app)

    def page(template, con, **ctx):
        footer = db.cached("footer", con, _footer)
        return render_template(template, footer=footer, type_labels=Q.SEASON_TYPE_LABELS, **ctx)

    def player_index(con):
        return db.cached("player_index", con, Q.player_index)

    # --- pages -----------------------------------------------------------------------------------------

    @app.get("/")
    def home():
        with db.connect() as con:
            return page("home.html", con, seasons=db.cached("seasons", con, Q.seasons),
                        dq=db.cached("dq", con, Q.dq_status))

    @app.get("/search")
    def search():
        q = request.args.get("q", "").strip()
        with db.connect() as con:
            index = player_index(con)
            if not q:
                most = sorted(index, key=lambda r: -r["games"])[:25]
                return page("search.html", con, q=q, results=[], most=most)
            results = Q.search_players(index, q)
            exact = [r for r in results if r["rank"] == 0]
            if len(results) == 1 or len(exact) == 1:
                return redirect(url_for("player", player_id=(exact or results)[0]["player_id"]))
            return page("search.html", con, q=q, results=results, most=[])

    @app.get("/api/search")
    def api_search():
        q = request.args.get("q", "").strip()
        with db.connect() as con:
            hits = Q.search_players(player_index(con), q, limit=8)
        return jsonify([{"name": r["player_name"], "url": url_for("player", player_id=r["player_id"]),
                         "detail": _years(r)} for r in hits])

    @app.get("/player/<int:player_id>")
    def player(player_id):
        mode = "totals" if request.args.get("mode") == "totals" else "per_game"
        with db.connect() as con:
            bio = Q.player(con, player_id)
            if bio is None:
                abort(404)
            tables = Q.player_seasons(con, player_id)
            seasons = db.cached("seasons", con, Q.seasons)
            return page("player.html", con, bio=bio, tables=tables, mode=mode, seasons=seasons,
                        games=sum(t["career"]["gp"] for t in tables.values()))

    @app.get("/player/<int:player_id>/games")
    def gamelog(player_id):
        with db.connect() as con:
            bio = Q.player(con, player_id)
            if bio is None:
                abort(404)
            options = Q.player_log_options(con, player_id)
            if not options:
                return page("gamelog.html", con, bio=bio, options=[], seasons=[], types=[], log=[])
            seasons = list(dict.fromkeys(s for s, _ in options))
            season = request.args.get("season")
            season = season if season in seasons else seasons[0]
            types = [t for t in Q.SEASON_TYPES if (season, t) in options]
            stype = request.args.get("type")
            stype = stype if stype in types else types[0]
            sort = request.args.get("sort", "date")
            sort = sort if sort in Q.GAMELOG_SORTS else "date"
            desc = request.args.get("dir") == "desc"
            log = Q.game_log(con, player_id, season, stype, sort, desc)
            played = [dict(r, gp=1, gs=int(bool(r["is_starter"]))) for r in log if r["played"]]
            average = Q.add_up(played) if played else None
            return page("gamelog.html", con, bio=bio, options=options, seasons=seasons, season=season,
                        types=types, stype=stype, log=log, sort=sort, desc=desc, average=average)

    @app.get("/teams")
    def teams():
        with db.connect() as con:
            return page("coming.html", con, what="Teams and standings", phase=2)

    @app.get("/leaders")
    def leaders():
        with db.connect() as con:
            return page("coming.html", con, what="Season leaders and the player stats table", phase=3)

    # --- images ----------------------------------------------------------------------------------------

    @app.get("/img/player/<int:player_id>")
    def player_image(player_id):
        found = find_image(app.config["IMAGES_DIR"], "player", player_id)
        resp = send_file(found) if found else app.send_static_file("placeholder-player.svg")
        resp.headers["Cache-Control"] = "no-cache"  # a newly added file shows on refresh
        return resp

    @app.get("/img/team/<int:team_id>")
    def team_image(team_id):
        season = request.args.get("season")
        found = find_image(app.config["IMAGES_DIR"], "team", team_id, season)
        if found:
            resp = send_file(found)
        else:
            abbr = escape(request.args.get("abbr", "")[:4])
            resp = Response(TEAM_PLACEHOLDER.format(abbr=abbr), mimetype="image/svg+xml")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/health")
    def health():
        return HUB_ID

    # --- errors ----------------------------------------------------------------------------------------

    @app.errorhandler(DatabaseBusy)
    def busy(e):
        return render_template("message.html", footer=None, refresh=5, title="The database is being updated",
                               text="A season run or a check has the database file open right now. "
                                    "This page reloads by itself in a few seconds.", detail=str(e)), 503

    @app.errorhandler(DatabaseMissing)
    def missing(e):
        return render_template("message.html", footer=None, refresh=None, title="No database yet",
                               text="There's no data/nba.duckdb yet. Double-click run_next_season.bat to pull and "
                                    "build the first season, then refresh.", detail=str(e)), 503

    @app.errorhandler(404)
    def not_found(e):
        return render_template("message.html", footer=None, refresh=None, title="Not found",
                               text="Nothing here. Try the search box.", detail=None), 404

    return app


TEAM_PLACEHOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                    '<circle cx="50" cy="50" r="47" fill="#d9dde3" stroke="#aab1bb" stroke-width="3"/>'
                    '<text x="50" y="58" font-family="Segoe UI, Arial, sans-serif" font-size="24" font-weight="700" '
                    'fill="#4a5361" text-anchor="middle">{abbr}</text></svg>')


def _footer(con):
    seasons = [s["season"] for s in Q.seasons(con)]
    built_at = con.execute("SELECT max(ran_at) FROM meta.dq_results").fetchone()[0]
    return {"first": seasons[-1] if seasons else None, "last": seasons[0] if seasons else None,
            "n": len(seasons), "built_at": built_at}


def _years(r):
    if r["games"]:
        return f"{r['first_season']} to {r['last_season']} · {r['games']:,} games"
    if r.get("from_year"):
        return f"{r['from_year']}–{r['to_year']} · no games in the database yet"
    return "no games in the database yet"


def _register_filters(app):
    def dash(v):
        return v is None

    @app.template_filter("f1")
    def f1(v):
        return "—" if dash(v) else f"{v:.1f}"

    @app.template_filter("pct")
    def pct(v):
        return "—" if dash(v) else f"{v * 100:.1f}"

    @app.template_filter("int")
    def int_(v):
        return "—" if dash(v) else f"{int(round(v)):,}"

    @app.template_filter("mmss")
    def mmss(sec):
        return "—" if dash(sec) else f"{int(sec) // 60}:{int(sec) % 60:02d}"

    @app.template_filter("signed")
    def signed(v):
        return "—" if dash(v) else f"{v:+d}" if v else "0"

    @app.template_filter("height")
    def height(inches):
        return None if not inches else f"{inches // 12}′{inches % 12}″"

    @app.template_filter("date")
    def date(d):
        return "—" if dash(d) else f"{d:%b} {d.day}, {d.year}"

    @app.template_filter("utc_local")
    def utc_local(d):
        """meta.dq_results.ran_at is stored in UTC; show it in this PC's time zone."""
        if dash(d):
            return "—"
        d = d.replace(tzinfo=timezone.utc).astimezone()
        return f"{d:%b} {d.day}, {d.year} {d:%H:%M}"

    def with_args(**changes):
        """The current page's URL with some query parameters changed (None removes one)."""
        args = request.args.to_dict()
        args.update(changes)
        args = {k: v for k, v in args.items() if v is not None}
        return url_for(request.endpoint, **(request.view_args or {}), **args)

    app.jinja_env.globals.update(years=_years, with_args=with_args)

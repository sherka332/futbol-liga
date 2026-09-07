import os
import json
import hmac
import hashlib
import sqlite3
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_from_directory

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8943057021:AAFbAh51QqSEN_jDdRhdbTH83y6HOtoezf8")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1342256845"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "liga5.db")

app = Flask(__name__, static_folder="static")


TEAMS = [
    "Athletic Club", "Atlético Madrid", "Barcelona", "Celta Vigo",
    "Deportivo Alavés", "Elche", "Espanyol", "Getafe",
    "Girona", "Levante", "Mallorca", "Osasuna",
    "Rayo Vallecano", "Real Betis", "Real Madrid", "Real Oviedo",
    "Real Sociedad", "Sevilla", "Valencia", "Villarreal"
]


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            team_id INTEGER UNIQUE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            home_team INTEGER NOT NULL,
            away_team INTEGER NOT NULL,
            home_goals INTEGER NOT NULL,
            away_goals INTEGER NOT NULL,
            played_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for name in TEAMS:
        con.execute("INSERT OR IGNORE INTO teams(name) VALUES (?)", (name,))
    con.commit()
    con.close()


def check_init_data(init_data: str):
    """Telegram Mini App initData tekshiruvi."""
    if not init_data or BOT_TOKEN == "PUT_YOUR_NEW_BOT_TOKEN_HERE":
        return None

    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    data_check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, data_check.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        return None

    user_raw = data.get("user")
    if not user_raw:
        return None

    try:
        return json.loads(user_raw)
    except json.JSONDecodeError:
        return None


def current_user():
    user = check_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    if not user:
        return None
    return user


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/teams")
def teams():
    con = db()
    rows = con.execute("""
        SELECT t.id, t.name, u.username, u.first_name
        FROM teams t
        LEFT JOIN users u ON u.team_id = t.id
        ORDER BY t.name
    """).fetchall()
    con.close()

    return jsonify([
        {
            "id": r["id"],
            "name": r["name"],
            "owner": r["username"] or r["first_name"] or None,
            "taken": r["username"] is not None or r["first_name"] is not None
        }
        for r in rows
    ])


@app.post("/api/join")
def join_team():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "Telegram foydalanuvchisi aniqlanmadi"}), 401

    body = request.get_json(silent=True) or {}
    team_id = body.get("team_id")
    if not isinstance(team_id, int):
        return jsonify({"ok": False, "error": "Jamoa tanlanmagan"}), 400

    con = db()
    try:
        con.execute(
            "INSERT OR IGNORE INTO users(telegram_id, username, first_name) VALUES (?, ?, ?)",
            (user["id"], user.get("username"), user.get("first_name"))
        )

        me = con.execute(
            "SELECT team_id FROM users WHERE telegram_id=?", (user["id"],)
        ).fetchone()
        if me and me["team_id"] is not None:
            return jsonify({"ok": False, "error": "Siz allaqachon jamoa tanlagansiz"}), 409

        team = con.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team:
            return jsonify({"ok": False, "error": "Bunday jamoa yo'q"}), 404

        taken = con.execute(
            "SELECT telegram_id FROM users WHERE team_id=?", (team_id,)
        ).fetchone()
        if taken:
            return jsonify({"ok": False, "error": "Bu jamoani boshqa foydalanuvchi tanlagan"}), 409

        con.execute(
            "UPDATE users SET team_id=?, username=?, first_name=? WHERE telegram_id=?",
            (team_id, user.get("username"), user.get("first_name"), user["id"])
        )
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.get("/api/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"ok": False}), 401

    con = db()
    row = con.execute("""
        SELECT u.telegram_id, u.username, u.first_name, t.id AS team_id, t.name AS team_name
        FROM users u
        LEFT JOIN teams t ON t.id=u.team_id
        WHERE u.telegram_id=?
    """, (user["id"],)).fetchone()
    con.close()

    return jsonify({
        "ok": True,
        "user": dict(row) if row else {
            "telegram_id": user["id"],
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "team_id": None,
            "team_name": None
        }
    })


@app.get("/api/standings")
def standings():
    con = db()
    teams = con.execute("SELECT id, name FROM teams ORDER BY name").fetchall()
    result = []

    for t in teams:
        stats = con.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN home_team=? THEN 1 WHEN away_team=? THEN 1 ELSE 0 END),0) AS played,
              COALESCE(SUM(CASE WHEN home_team=? THEN home_goals WHEN away_team=? THEN away_goals ELSE 0 END),0) AS gf,
              COALESCE(SUM(CASE WHEN home_team=? THEN away_goals WHEN away_team=? THEN home_goals ELSE 0 END),0) AS ga
            FROM matches
            WHERE home_team=? OR away_team=?
        """, (t["id"], t["id"], t["id"], t["id"], t["id"], t["id"], t["id"], t["id"])).fetchone()

        wins = draws = losses = points = 0
        matches = con.execute("""
            SELECT home_team, away_team, home_goals, away_goals
            FROM matches WHERE home_team=? OR away_team=?
        """, (t["id"], t["id"])).fetchall()

        for m in matches:
            if m["home_goals"] == m["away_goals"]:
                draws += 1
                points += 1
            else:
                team_goals = m["home_goals"] if m["home_team"] == t["id"] else m["away_goals"]
                opp_goals = m["away_goals"] if m["home_team"] == t["id"] else m["home_goals"]
                if team_goals > opp_goals:
                    wins += 1
                    points += 3
                else:
                    losses += 1

        result.append({
            "id": t["id"], "name": t["name"],
            "played": stats["played"], "wins": wins, "draws": draws,
            "losses": losses, "gf": stats["gf"], "ga": stats["ga"],
            "gd": stats["gf"] - stats["ga"], "points": points
        })

    con.close()
    result.sort(key=lambda x: (-x["points"], -x["gd"], -x["gf"], x["name"]))
    for i, row in enumerate(result, 1):
        row["rank"] = i
    return jsonify(result)


@app.get("/api/results")
def results():
    con = db()
    rows = con.execute("""
        SELECT m.id, h.name AS home, a.name AS away,
               m.home_goals, m.away_goals, m.played_at
        FROM matches m
        JOIN teams h ON h.id=m.home_team
        JOIN teams a ON a.id=m.away_team
        ORDER BY m.id DESC
    """).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/admin/result")
def add_result():
    user = current_user()
    if not user or (ADMIN_ID and user["id"] != ADMIN_ID):
        return jsonify({"ok": False, "error": "Faqat admin uchun"}), 403

    body = request.get_json(silent=True) or {}
    try:
        home = int(body["home_team"])
        away = int(body["away_team"])
        hg = int(body["home_goals"])
        ag = int(body["away_goals"])
        if home == away or min(hg, ag) < 0:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify({"ok": False, "error": "Natija ma'lumotlari noto'g'ri"}), 400

    con = db()
    con.execute(
        "INSERT INTO matches(home_team, away_team, home_goals, away_goals) VALUES (?,?,?,?)",
        (home, away, hg, ag)
    )
    con.commit()
    con.close()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

import os
import json
import hmac
import hashlib
import sqlite3
from datetime import datetime, date, time, timedelta
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from flask import Flask, request, jsonify, send_from_directory

# Tokenni kodga yozmang. Render -> Environment Variables -> BOT_TOKEN orqali bering.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
TZ = ZoneInfo("Asia/Tashkent")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "liga5.db")
app = Flask(__name__, static_folder="static")

TEAMS = [
    "Athletic Club", "Atlético Madrid", "Barcelona", "Celta Vigo",
    "Deportivo Alavés", "Elche", "Espanyol", "Getafe", "Girona", "Levante",
    "Mallorca", "Osasuna", "Rayo Vallecano", "Real Betis", "Real Madrid",
    "Real Oviedo", "Real Sociedad", "Sevilla", "Valencia", "Villarreal"
]

# FotMob team logo ID'lari.
TEAM_LOGOS = {
    "Athletic Club": 8315, "Atlético Madrid": 9906, "Barcelona": 8634,
    "Celta Vigo": 9910, "Deportivo Alavés": 9746, "Elche": 10268,
    "Espanyol": 8558, "Getafe": 8302, "Girona": 7732, "Levante": 7878,
    "Mallorca": 8661, "Osasuna": 8371, "Rayo Vallecano": 8372,
    "Real Betis": 8603, "Real Madrid": 8633, "Real Oviedo": 8678,
    "Real Sociedad": 8560, "Sevilla": 8660, "Valencia": 10267,
    "Villarreal": 10205,
}


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def ensure_column(con, table, column, definition):
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        team_id INTEGER UNIQUE,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_no INTEGER UNIQUE NOT NULL,
        round_date TEXT NOT NULL,
        deadline TEXT NOT NULL DEFAULT '23:30',
        status TEXT NOT NULL DEFAULT 'scheduled',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id INTEGER,
        home_team INTEGER NOT NULL,
        away_team INTEGER NOT NULL,
        home_goals INTEGER NOT NULL DEFAULT 0,
        away_goals INTEGER NOT NULL DEFAULT 0,
        scheduled_at TEXT,
        status TEXT NOT NULL DEFAULT 'official',
        submitted_by INTEGER,
        pending_home_goals INTEGER,
        pending_away_goals INTEGER,
        played_at TEXT,
        FOREIGN KEY(round_id) REFERENCES rounds(id) ON DELETE CASCADE
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
    )""")
    # Ishtirokchilar o'rtasidagi shaxsiy chat.
    con.execute("""CREATE TABLE IF NOT EXISTS direct_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    for name in TEAMS:
        con.execute("INSERT OR IGNORE INTO teams(name) VALUES (?)", (name,))

    # Eski bazadagi jadvallarni yangi maydonlar bilan moslashtirish.
    ensure_column(con, "users", "active", "INTEGER NOT NULL DEFAULT 1")
    for col, definition in [
        ("round_id", "INTEGER"), ("scheduled_at", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'official'"), ("submitted_by", "INTEGER"),
        ("pending_home_goals", "INTEGER"), ("pending_away_goals", "INTEGER"),
        ("played_at", "TEXT"),
    ]:
        ensure_column(con, "matches", col, definition)
    con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('deadline','23:30')")
    con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('season','1')")
    con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('season_finished','0')")
    con.execute("""CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
        season INTEGER NOT NULL, place INTEGER NOT NULL, team_id INTEGER, team_name TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(telegram_id, season, place)
    )""")
    ensure_column(con, "achievements", "team_id", "INTEGER")
    ensure_column(con, "achievements", "team_name", "TEXT")
    con.commit()
    con.close()


def now_local():
    return datetime.now(TZ)


def deadline_dt(round_date, deadline):
    d = date.fromisoformat(round_date)
    h, m = map(int, deadline.split(":"))
    return datetime.combine(d, time(h, m), TZ)


def check_init_data(init_data: str):
    if not init_data or not BOT_TOKEN:
        return None
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
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
    return check_init_data(request.headers.get("X-Telegram-Init-Data", ""))


def require_user():
    user = current_user()
    if not user:
        return None, (jsonify({"ok": False, "error": "Telegram foydalanuvchisi aniqlanmadi"}), 401)
    return user, None


def require_admin():
    user, err = require_user()
    if err:
        return None, err
    if not ADMIN_ID or user["id"] != ADMIN_ID:
        return None, (jsonify({"ok": False, "error": "Faqat admin uchun"}), 403)
    return user, None


def team_logo(name):
    tid = TEAM_LOGOS.get(name)
    return f"https://images.fotmob.com/image_resources/logo/teamlogo/{tid}.png" if tid else ""


def finalize_due_matches(con):
    """Deadline o'tganda pending natija rasmiylashadi, hech kim kiritmasa 0:0."""
    now = now_local()
    rows = con.execute("""SELECT m.id,m.status,m.pending_home_goals,m.pending_away_goals,r.round_date,r.deadline
                         FROM matches m JOIN rounds r ON r.id=m.round_id
                         WHERE m.status IN ('scheduled','pending')""").fetchall()
    changed=False
    for r in rows:
        if now >= deadline_dt(r["round_date"],r["deadline"]):
            if r["status"] == "pending" and r["pending_home_goals"] is not None and r["pending_away_goals"] is not None:
                con.execute("""UPDATE matches SET home_goals=?,away_goals=?,status='official',
                    pending_home_goals=NULL,pending_away_goals=NULL,submitted_by=NULL,played_at=? WHERE id=?""",
                    (r["pending_home_goals"],r["pending_away_goals"],now.isoformat(),r["id"]))
            else:
                con.execute("""UPDATE matches SET home_goals=0,away_goals=0,status='auto',
                    pending_home_goals=NULL,pending_away_goals=NULL,submitted_by=NULL,played_at=? WHERE id=?""",
                    (now.isoformat(),r["id"]))
            changed=True
    if changed: con.commit()

def user_row(con, telegram_id):
    return con.execute("""SELECT u.telegram_id,u.username,u.first_name,u.active,
        t.id team_id,t.name team_name FROM users u LEFT JOIN teams t ON t.id=u.team_id
        WHERE u.telegram_id=?""", (telegram_id,)).fetchone()


def match_for_user(con, match_id, telegram_id):
    row = con.execute("""SELECT m.*, r.round_no,r.round_date,r.deadline,r.status AS round_status,
        ht.name home_name,at.name away_name,
        hu.telegram_id home_user_id,hu.username home_username,hu.first_name home_first_name,
        au.telegram_id away_user_id,au.username away_username,au.first_name away_first_name
        FROM matches m JOIN rounds r ON r.id=m.round_id
        JOIN teams ht ON ht.id=m.home_team JOIN teams at ON at.id=m.away_team
        LEFT JOIN users hu ON hu.team_id=m.home_team AND hu.active=1
        LEFT JOIN users au ON au.team_id=m.away_team AND au.active=1
        WHERE m.id=?""", (match_id,)).fetchone()
    if not row:
        return None
    if telegram_id not in (row["home_user_id"], row["away_user_id"]):
        return None
    return row


def serialize_match(r, viewer_id=None):
    home_user = r["home_username"] or r["home_first_name"] or None
    away_user = r["away_username"] or r["away_first_name"] or None
    my_side = None
    if viewer_id == r["home_user_id"]: my_side = "home"
    if viewer_id == r["away_user_id"]: my_side = "away"
    return {
        "id": r["id"], "round_id": r["round_id"], "round_no": r["round_no"],
        "round_date": r["round_date"], "deadline": r["deadline"], "round_status": r["round_status"],
        "home_team": r["home_team"], "away_team": r["away_team"],
        "home": r["home_name"], "away": r["away_name"],
        "home_logo": team_logo(r["home_name"]), "away_logo": team_logo(r["away_name"]),
        "home_user_id": r["home_user_id"], "away_user_id": r["away_user_id"],
        "home_username": home_user, "away_username": away_user,
        "scheduled_at": r["scheduled_at"], "status": r["status"],
        "home_goals": r["home_goals"], "away_goals": r["away_goals"],
        "pending_home_goals": r["pending_home_goals"],
        "pending_away_goals": r["pending_away_goals"],
        "submitted_by": r["submitted_by"], "my_side": my_side,
        "can_confirm": r["status"] == "pending" and viewer_id is not None and viewer_id != r["submitted_by"],
    }


@app.get("/")
def index():
    if os.path.exists(os.path.join(BASE_DIR, "index.html")):
        return send_from_directory(BASE_DIR, "index.html")
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/me")
def me():
    user, err = require_user()
    if err: return err
    con = db(); row = user_row(con, user["id"]); con.close()
    return jsonify({"ok": True, "user": dict(row) if row else {
        "telegram_id": user["id"], "username": user.get("username"),
        "first_name": user.get("first_name"), "team_id": None, "team_name": None, "active": 1
    }, "is_admin": bool(ADMIN_ID and user["id"] == ADMIN_ID)})


@app.get("/api/teams")
def teams():
    con = db()
    rows = con.execute("""SELECT t.id,t.name,u.telegram_id,u.username,u.first_name
        FROM teams t LEFT JOIN users u ON u.team_id=t.id AND u.active=1 ORDER BY t.name""").fetchall()
    con.close()
    return jsonify([{
        "id": r["id"], "name": r["name"], "logo": team_logo(r["name"]),
        "owner_id": r["telegram_id"], "owner": r["username"] or r["first_name"] or None,
        "taken": r["telegram_id"] is not None
    } for r in rows])


@app.post("/api/join")
def join_team():
    user, err = require_user()
    if err: return err
    body = request.get_json(silent=True) or {}
    try: team_id = int(body.get("team_id"))
    except (TypeError, ValueError): return jsonify({"ok":False,"error":"Jamoa tanlanmagan"}),400
    con = db()
    try:
        con.execute("INSERT OR IGNORE INTO users(telegram_id,username,first_name,active) VALUES(?,?,?,1)",
                     (user["id"], user.get("username"), user.get("first_name")))
        me = con.execute("SELECT team_id FROM users WHERE telegram_id=?", (user["id"],)).fetchone()
        if me and me["team_id"] is not None:
            return jsonify({"ok":False,"error":"Siz allaqachon jamoa tanlagansiz"}),409
        if not con.execute("SELECT id FROM teams WHERE id=?", (team_id,)).fetchone():
            return jsonify({"ok":False,"error":"Bunday jamoa yo'q"}),404
        if con.execute("SELECT telegram_id FROM users WHERE team_id=? AND active=1", (team_id,)).fetchone():
            return jsonify({"ok":False,"error":"Bu jamoani boshqa foydalanuvchi tanlagan"}),409
        con.execute("UPDATE users SET team_id=?,username=?,first_name=?,active=1 WHERE telegram_id=?",
                     (team_id,user.get("username"),user.get("first_name"),user["id"]))
        con.commit(); return jsonify({"ok":True})
    finally: con.close()


@app.get("/api/my_matches")
def my_matches():
    user, err = require_user()
    if err: return err
    con = db(); finalize_due_matches(con)
    u = user_row(con,user["id"])
    if not u or not u["team_id"]:
        con.close(); return jsonify({"ok":True,"matches":[],"message":"Avval jamoa tanlang"})
    rows = con.execute("""SELECT m.*,r.round_no,r.round_date,r.deadline,r.status AS round_status,
        ht.name home_name,at.name away_name,
        hu.telegram_id home_user_id,hu.username home_username,hu.first_name home_first_name,
        au.telegram_id away_user_id,au.username away_username,au.first_name away_first_name
        FROM matches m JOIN rounds r ON r.id=m.round_id
        JOIN teams ht ON ht.id=m.home_team JOIN teams at ON at.id=m.away_team
        LEFT JOIN users hu ON hu.team_id=m.home_team AND hu.active=1
        LEFT JOIN users au ON au.team_id=m.away_team AND au.active=1
        WHERE (m.home_team=? OR m.away_team=?) ORDER BY r.round_no,m.id""",
        (u["team_id"],u["team_id"])).fetchall()
    out=[serialize_match(r,user["id"]) for r in rows]
    con.close(); return jsonify({"ok":True,"matches":out})


@app.post("/api/result/submit")
def submit_result():
    user, err = require_user()
    if err: return err
    body=request.get_json(silent=True) or {}
    try:
        match_id=int(body["match_id"]); hg=int(body["home_goals"]); ag=int(body["away_goals"])
        if hg<0 or ag<0 or hg>99 or ag>99: raise ValueError
    except (KeyError,TypeError,ValueError): return jsonify({"ok":False,"error":"Hisob noto'g'ri"}),400
    con=db(); finalize_due_matches(con)
    r=match_for_user(con,match_id,user["id"])
    if not r: con.close(); return jsonify({"ok":False,"error":"O'yin topilmadi"}),404
    now = now_local()
    if r["round_status"] == "closed":
        con.close(); return jsonify({"ok":False,"error":"Bu tur admin tomonidan yopilgan"}),403
    if date.fromisoformat(r["round_date"]) > now.date() and r["round_status"] != "open":
        con.close(); return jsonify({"ok":False,"error":"Bu tur hali ochilmagan"}),403
    if now >= deadline_dt(r["round_date"],r["deadline"]):
        con.close(); return jsonify({"ok":False,"error":"23:30 deadline o'tgan"}),403
    if r["status"] not in ("scheduled","pending"):
        con.close(); return jsonify({"ok":False,"error":"Bu o'yin yopilgan"}),409
    con.execute("""UPDATE matches SET pending_home_goals=?,pending_away_goals=?,submitted_by=?,status='pending'
                   WHERE id=?""",(hg,ag,user["id"],match_id))
    con.commit(); con.close(); return jsonify({"ok":True,"message":"Natija raqib tasdig'ini kutmoqda"})


@app.post("/api/result/confirm")
def confirm_result():
    user, err=require_user()
    if err:return err
    body=request.get_json(silent=True) or {}
    try: match_id=int(body["match_id"])
    except (KeyError,TypeError,ValueError): return jsonify({"ok":False,"error":"O'yin ID noto'g'ri"}),400
    con=db(); finalize_due_matches(con); r=match_for_user(con,match_id,user["id"])
    if not r: con.close(); return jsonify({"ok":False,"error":"O'yin topilmadi"}),404
    if r["status"]!="pending" or r["submitted_by"]==user["id"]:
        con.close(); return jsonify({"ok":False,"error":"Tasdiqlash mumkin emas"}),409
    if r["round_status"] == "closed" or date.fromisoformat(r["round_date"]) > now_local().date():
        con.close(); return jsonify({"ok":False,"error":"Bu tur hozir yopiq"}),403
    if now_local() >= deadline_dt(r["round_date"],r["deadline"]):
        con.close(); return jsonify({"ok":False,"error":"Deadline o'tgan"}),403
    con.execute("""UPDATE matches SET home_goals=?,away_goals=?,status='official',played_at=?,
                   pending_home_goals=NULL,pending_away_goals=NULL,submitted_by=NULL WHERE id=?""",
                (r["pending_home_goals"],r["pending_away_goals"],now_local().isoformat(),match_id))
    con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/result/reject")
def reject_result():
    user, err=require_user()
    if err:return err
    body=request.get_json(silent=True) or {}
    try: match_id=int(body["match_id"])
    except (KeyError,TypeError,ValueError): return jsonify({"ok":False,"error":"O'yin ID noto'g'ri"}),400
    con=db(); finalize_due_matches(con); r=match_for_user(con,match_id,user["id"])
    if not r or r["status"]!="pending" or r["submitted_by"]==user["id"]:
        con.close();return jsonify({"ok":False,"error":"Rad etish mumkin emas"}),409
    if r["round_status"] == "closed" or date.fromisoformat(r["round_date"]) > now_local().date() or now_local() >= deadline_dt(r["round_date"],r["deadline"]):
        con.close();return jsonify({"ok":False,"error":"Bu tur hozir yopiq"}),403
    con.execute("UPDATE matches SET status='scheduled',pending_home_goals=NULL,pending_away_goals=NULL,submitted_by=NULL WHERE id=?",(match_id,))
    con.commit();con.close();return jsonify({"ok":True})


@app.get("/api/standings")
def standings():
    con=db();finalize_due_matches(con)
    rows=con.execute("SELECT id,name FROM teams ORDER BY name").fetchall();result=[]
    for t in rows:
        ms=con.execute("""SELECT home_team,away_team,home_goals,away_goals FROM matches
                         WHERE status IN ('official','auto') AND (home_team=? OR away_team=?)""",(t["id"],t["id"])).fetchall()
        played=wins=draws=losses=gf=ga=points=0
        for m in ms:
            played+=1
            if m["home_team"]==t["id"]: a,b=m["home_goals"],m["away_goals"]
            else: a,b=m["away_goals"],m["home_goals"]
            gf+=a;ga+=b
            if a>b:wins+=1;points+=3
            elif a==b:draws+=1;points+=1
            else:losses+=1
        owner=con.execute("SELECT username,first_name FROM users WHERE team_id=? AND active=1",(t["id"],)).fetchone()
        result.append({"id":t["id"],"name":t["name"],"logo":team_logo(t["name"]),"owner":(owner["username"] or owner["first_name"]) if owner else None,
                       "played":played,"wins":wins,"draws":draws,"losses":losses,"gf":gf,"ga":ga,"gd":gf-ga,"points":points})
    con.close();result.sort(key=lambda x:(-x["points"],-x["gd"],-x["gf"],x["name"]))
    for i,r in enumerate(result,1):r["rank"]=i
    return jsonify(result)


@app.get("/api/results")
def results():
    con=db();finalize_due_matches(con)
    rows=con.execute("""SELECT m.*,r.round_no,r.round_date,r.deadline,ht.name home_name,at.name away_name
        FROM matches m JOIN rounds r ON r.id=m.round_id JOIN teams ht ON ht.id=m.home_team JOIN teams at ON at.id=m.away_team
        WHERE m.status IN ('official','auto') ORDER BY r.round_no DESC,m.id DESC""").fetchall()
    data=[]
    for r in rows:
        x=dict(r);x.update(home=r["home_name"],away=r["away_name"],home_logo=team_logo(r["home_name"]),away_logo=team_logo(r["away_name"]));data.append(x)
    con.close();return jsonify(data)


@app.get("/api/chat/<int:match_id>")
def get_chat(match_id):
    user,err=require_user()
    if err:return err
    con=db();finalize_due_matches(con);m=match_for_user(con,match_id,user["id"])
    if not m:con.close();return jsonify({"ok":False,"error":"Bu chat sizga tegishli emas"}),403
    rows=con.execute("""SELECT x.id,x.sender_id,x.text,x.created_at,u.username,u.first_name
                       FROM messages x LEFT JOIN users u ON u.telegram_id=x.sender_id
                       WHERE x.match_id=? ORDER BY x.id ASC""",(match_id,)).fetchall();con.close()
    return jsonify({"ok":True,"messages":[{"id":r["id"],"sender_id":r["sender_id"],"sender":r["username"] or r["first_name"] or "Foydalanuvchi","text":r["text"],"created_at":r["created_at"]} for r in rows]})


@app.post("/api/chat/<int:match_id>")
def send_chat(match_id):
    user,err=require_user()
    if err:return err
    body=request.get_json(silent=True) or {};text=str(body.get("text","")).strip()
    if not text:return jsonify({"ok":False,"error":"Xabar bo'sh"}),400
    if len(text)>1000:return jsonify({"ok":False,"error":"Xabar juda uzun"}),400
    con=db();m=match_for_user(con,match_id,user["id"])
    if not m:con.close();return jsonify({"ok":False,"error":"Bu chat sizga tegishli emas"}),403
    if m["status"]=="auto":con.close();return jsonify({"ok":False,"error":"O'yin avtomatik yopilgan"}),403
    con.execute("INSERT INTO messages(match_id,sender_id,text) VALUES(?,?,?)",(match_id,user["id"],text));con.commit();con.close();return jsonify({"ok":True})


# ---------------- ISHTIROKCHILAR SHAXSIY CHATI ----------------
def participant_exists(con, telegram_id):
    return con.execute("SELECT telegram_id FROM users WHERE telegram_id=? AND active=1",(telegram_id,)).fetchone()

@app.get("/api/dm/<int:other_id>")
def get_direct_messages(other_id):
    user,err=require_user()
    if err:return err
    if other_id==user["id"]: return jsonify({"ok":False,"error":"O'zingizga yozib bo'lmaydi"}),400
    con=db()
    if not participant_exists(con,other_id):
        con.close(); return jsonify({"ok":False,"error":"Ishtirokchi topilmadi"}),404
    other=user_row(con,other_id)
    rows=con.execute("""SELECT id,sender_id,receiver_id,text,created_at FROM direct_messages
                        WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
                        ORDER BY id ASC""",(user["id"],other_id,other_id,user["id"])).fetchall()
    con.close()
    return jsonify({"ok":True,"other":{"telegram_id":other_id,"username":other["username"] if other else None,
        "first_name":other["first_name"] if other else "Ishtirokchi","team_name":other["team_name"] if other else None},
        "messages":[dict(r) for r in rows]})

@app.post("/api/dm/<int:other_id>")
def send_direct_message(other_id):
    user,err=require_user()
    if err:return err
    if other_id==user["id"]: return jsonify({"ok":False,"error":"O'zingizga yozib bo'lmaydi"}),400
    body=request.get_json(silent=True) or {}; text=str(body.get("text","")).strip()
    if not text:return jsonify({"ok":False,"error":"Xabar bo'sh"}),400
    if len(text)>1000:return jsonify({"ok":False,"error":"Xabar juda uzun"}),400
    con=db()
    if not participant_exists(con,other_id):
        con.close(); return jsonify({"ok":False,"error":"Ishtirokchi topilmadi"}),404
    con.execute("INSERT INTO direct_messages(sender_id,receiver_id,text) VALUES(?,?,?)",(user["id"],other_id,text))
    con.commit();con.close();return jsonify({"ok":True})


# ---------------- ADMIN ----------------
@app.get("/api/admin/users")
def admin_users():
    _,err=require_admin()
    if err:return err
    con=db();rows=con.execute("""SELECT u.telegram_id,u.username,u.first_name,u.active,t.id team_id,t.name team_name
        FROM users u LEFT JOIN teams t ON t.id=u.team_id ORDER BY u.active DESC,u.telegram_id""").fetchall();con.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/admin/user")
def admin_add_user():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:tid=int(b["telegram_id"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"Telegram ID noto'g'ri"}),400
    username=(b.get("username") or "").lstrip("@").strip() or None;first=(b.get("first_name") or "Foydalanuvchi").strip()
    team_id=b.get("team_id"); team_id=int(team_id) if team_id not in (None,"") else None
    con=db()
    if team_id and con.execute("SELECT telegram_id FROM users WHERE team_id=? AND active=1 AND telegram_id!=?",(team_id,tid)).fetchone():
        con.close();return jsonify({"ok":False,"error":"Bu jamoa band"}),409
    con.execute("""INSERT INTO users(telegram_id,username,first_name,team_id,active) VALUES(?,?,?,?,1)
                 ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,team_id=excluded.team_id,active=1""",
                 (tid,username,first,team_id));con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/admin/user/team")
def admin_team():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:tid=int(b["telegram_id"]);team_id=int(b["team_id"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"Ma'lumot noto'g'ri"}),400
    con=db()
    old=con.execute("SELECT team_id FROM users WHERE telegram_id=?",(tid,)).fetchone()
    if not old:con.close();return jsonify({"ok":False,"error":"Ishtirokchi topilmadi"}),404
    if con.execute("SELECT telegram_id FROM users WHERE team_id=? AND active=1 AND telegram_id!=?",(team_id,tid)).fetchone():con.close();return jsonify({"ok":False,"error":"Bu jamoa band"}),409
    con.execute("UPDATE users SET team_id=?,active=1 WHERE telegram_id=?",(team_id,tid));con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/admin/user/remove")
def admin_remove_user():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:tid=int(b["telegram_id"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"ID noto'g'ri"}),400
    con=db();con.execute("UPDATE users SET active=0,team_id=NULL WHERE telegram_id=?",(tid,));con.commit();con.close();return jsonify({"ok":True})


@app.get("/api/admin/rounds")
def admin_rounds():
    _,err=require_admin()
    if err:return err
    con=db();rows=con.execute("""SELECT r.*,COUNT(m.id) match_count FROM rounds r LEFT JOIN matches m ON m.round_id=r.id GROUP BY r.id ORDER BY r.round_no""").fetchall();con.close();return jsonify([dict(r) for r in rows])


def round_robin(team_ids):
    ids=list(team_ids)
    if len(ids)<2:return []
    if len(ids)%2:ids.append(None)
    n=len(ids);out=[]
    for rnd in range(n-1):
        pairs=[]
        for i in range(n//2):
            a,b=ids[i],ids[n-1-i]
            if a is not None and b is not None:
                pairs.append((a,b) if rnd%2==0 else (b,a))
        out.append(pairs)
        ids=[ids[0]]+[ids[-1]]+ids[1:-1]
    return out


@app.post("/api/admin/generate_rounds")
def admin_generate_rounds():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {};start=b.get("start_date") or now_local().date().isoformat()
    try:start_d=date.fromisoformat(start)
    except ValueError:return jsonify({"ok":False,"error":"Sana noto'g'ri"}),400
    con=db()
    if con.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"]:
        con.close();return jsonify({"ok":False,"error":"Turlar allaqachon yaratilgan. Mavjud turlarni o'chirmasdan qayta yaratmaymiz."}),409
    teams=[r["id"] for r in con.execute("SELECT id FROM teams t WHERE EXISTS(SELECT 1 FROM users u WHERE u.team_id=t.id AND u.active=1) ORDER BY id").fetchall()]
    pairs=round_robin(teams)
    if len(teams)<2:con.close();return jsonify({"ok":False,"error":"Kamida 2 ta faol ishtirokchi kerak"}),400
    # Birinchi 19 tur + javob o'yinlari = aynan 38 tur.
    all_rounds = pairs + [[(away, home) for home, away in pairset] for pairset in pairs]
    for idx,pairset in enumerate(all_rounds,1):
        rd=start_d+timedelta(days=(idx-1)//2)
        con.execute("INSERT INTO rounds(round_no,round_date,deadline,status) VALUES(?,?,?,'scheduled')",(idx,rd.isoformat(),"23:30"))
        rid=con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for home,away in pairset:
            con.execute("INSERT INTO matches(round_id,home_team,away_team,home_goals,away_goals,status) VALUES(?,?,?,0,0,'scheduled')",(rid,home,away))
    con.commit();con.close();return jsonify({"ok":True,"rounds_created":len(all_rounds),"teams":len(teams)})


@app.post("/api/admin/round")
def admin_create_round():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:rn=int(b["round_no"]);rd=date.fromisoformat(b["round_date"]).isoformat();dl=b.get("deadline","23:30")
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"Tur ma'lumotlari noto'g'ri"}),400
    con=db()
    try:con.execute("INSERT INTO rounds(round_no,round_date,deadline,status) VALUES(?,?,?,'scheduled')",(rn,rd,dl));con.commit()
    except sqlite3.IntegrityError:con.close();return jsonify({"ok":False,"error":"Bu tur mavjud"}),409
    con.close();return jsonify({"ok":True})


@app.post("/api/admin/round/action")
def admin_round_action():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:rid=int(b["round_id"]);action=b["action"]
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"Ma'lumot noto'g'ri"}),400
    if action not in ("open","close"):return jsonify({"ok":False,"error":"Action noto'g'ri"}),400
    con=db();con.execute("UPDATE rounds SET status=? WHERE id=?",("open" if action=="open" else "closed",rid));con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/admin/round/delete")
def admin_round_delete():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:rid=int(b["round_id"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"ID noto'g'ri"}),400
    con=db();con.execute("DELETE FROM rounds WHERE id=?",(rid,));con.commit();con.close();return jsonify({"ok":True})


@app.get("/api/admin/matches")
def admin_matches():
    _,err=require_admin()
    if err:return err
    con=db();finalize_due_matches(con)
    rows=con.execute("""SELECT m.*,r.round_no,r.round_date,r.deadline,ht.name home_name,at.name away_name
        FROM matches m JOIN rounds r ON r.id=m.round_id JOIN teams ht ON ht.id=m.home_team JOIN teams at ON at.id=m.away_team ORDER BY r.round_no,m.id""").fetchall();con.close()
    return jsonify([dict(r,home_logo=team_logo(r["home_name"]),away_logo=team_logo(r["away_name"])) for r in rows])


@app.post("/api/admin/match")
def admin_create_match():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:rid=int(b["round_id"]);home=int(b["home_team"]);away=int(b["away_team"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"O'yin ma'lumotlari noto'g'ri"}),400
    if home==away:return jsonify({"ok":False,"error":"Bir xil jamoalar bo'lmaydi"}),400
    con=db();con.execute("INSERT INTO matches(round_id,home_team,away_team,scheduled_at,status,home_goals,away_goals) VALUES(?,?,?,?, 'scheduled',0,0)",(rid,home,away,b.get("scheduled_at")));con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/admin/match/update")
def admin_update_match():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:mid=int(b["match_id"]);hg=int(b["home_goals"]);ag=int(b["away_goals"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"Hisob noto'g'ri"}),400
    if hg<0 or ag<0:return jsonify({"ok":False,"error":"Manfiy hisob bo'lmaydi"}),400
    con=db();con.execute("""UPDATE matches SET home_goals=?,away_goals=?,status='official',played_at=?,scheduled_at=COALESCE(?,scheduled_at),
        pending_home_goals=NULL,pending_away_goals=NULL,submitted_by=NULL WHERE id=?""",(hg,ag,now_local().isoformat(),b.get("scheduled_at"),mid));con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/admin/match/delete")
def admin_delete_match():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {}
    try:mid=int(b["match_id"])
    except (KeyError,TypeError,ValueError):return jsonify({"ok":False,"error":"ID noto'g'ri"}),400
    con=db();con.execute("DELETE FROM matches WHERE id=?",(mid,));con.commit();con.close();return jsonify({"ok":True})


@app.post("/api/admin/deadline")
def admin_deadline():
    _,err=require_admin()
    if err:return err
    b=request.get_json(silent=True) or {};dl=str(b.get("deadline","23:30"))
    try:h,m=map(int,dl.split(":"));assert 0<=h<=23 and 0<=m<=59
    except Exception:return jsonify({"ok":False,"error":"Vaqt HH:MM bo'lishi kerak"}),400
    con=db();con.execute("INSERT INTO settings(key,value) VALUES('deadline',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dl,));con.execute("UPDATE rounds SET deadline=? WHERE status!='closed'",(dl,));con.commit();con.close();return jsonify({"ok":True,"deadline":dl})


@app.get("/api/admin/summary")
def admin_summary():
    _,err=require_admin()
    if err:return err
    con=db();finalize_due_matches(con)
    finish_season_if_needed(con)
    x={"participants":con.execute("SELECT COUNT(*) c FROM users WHERE active=1 AND team_id IS NOT NULL").fetchone()["c"],
       "rounds":con.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"],
       "matches":con.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"],
       "official":con.execute("SELECT COUNT(*) c FROM matches WHERE status IN ('official','auto')").fetchone()["c"],
       "deadline":con.execute("SELECT value FROM settings WHERE key='deadline'").fetchone()["value"]}
    con.close();return jsonify(x)


@app.get("/api/schedule")
def schedule():
    con=db(); finalize_due_matches(con)
    rows=con.execute("""SELECT m.*,r.round_no,r.round_date,r.deadline,r.status round_status,ht.name home_name,at.name away_name
        FROM matches m JOIN rounds r ON r.id=m.round_id JOIN teams ht ON ht.id=m.home_team JOIN teams at ON at.id=m.away_team ORDER BY r.round_no,m.id""").fetchall()
    out={}
    for r in rows:
        x={"id":r["id"],"home":r["home_name"],"away":r["away_name"],"home_logo":team_logo(r["home_name"]),"away_logo":team_logo(r["away_name"]),"home_goals":r["home_goals"],"away_goals":r["away_goals"],"status":r["status"]}
        key=str(r["round_no"])
        out.setdefault(key,{"round_no":r["round_no"],"round_date":r["round_date"],"deadline":r["deadline"],"matches":[]})["matches"].append(x)
    con.close(); return jsonify(list(out.values()))

@app.get("/api/top-scorers")
def top_scorers():
    table=standings().get_json()
    return jsonify(sorted([{"rank":i+1,"name":x["name"],"logo":x["logo"],"goals":x["gf"]} for i,x in enumerate(sorted(table,key=lambda z:(-z["gf"],-z["gd"],z["name"])))], key=lambda z:z["rank"]))

@app.get("/api/profile/<int:telegram_id>")
def public_profile(telegram_id):
    con=db(); u=user_row(con,telegram_id)
    if not u: con.close(); return jsonify({"ok":False,"error":"Profil topilmadi"}),404
    ach=con.execute("SELECT place,COUNT(*) c FROM achievements WHERE telegram_id=? GROUP BY place",(telegram_id,)).fetchall()
    cups={str(r["place"]):r["c"] for r in ach}; con.close()
    return jsonify({"ok":True,"user":dict(u),"cups":{"1":cups.get("1",0),"2":cups.get("2",0),"3":cups.get("3",0)}})

def finish_season_if_needed(con):
    total=con.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"]
    done=con.execute("SELECT COUNT(*) c FROM matches WHERE status IN ('official','auto')").fetchone()["c"]
    allm=con.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    if total==38 and allm and done==allm and con.execute("SELECT value FROM settings WHERE key='season_finished'").fetchone()["value"]!='1':
        rows=standings().get_json()
        season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])
        for x in rows[:3]:
            owner=con.execute("SELECT telegram_id FROM users WHERE team_id=? AND active=1",(x["id"],)).fetchone()
            if owner: con.execute("INSERT OR IGNORE INTO achievements(telegram_id,season,place) VALUES(?,?,?)",(owner["telegram_id"],season,x["rank"]))
        con.execute("UPDATE settings SET value='1' WHERE key='season_finished'"); con.commit()

@app.post("/api/admin/start_next_season")
def start_next_season():
    _,err=require_admin()
    if err:return err
    con=db(); con.execute("DELETE FROM matches"); con.execute("DELETE FROM rounds")
    season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])+1
    con.execute("UPDATE settings SET value=? WHERE key='season'",(str(season),)); con.execute("UPDATE settings SET value='0' WHERE key='season_finished'")
    con.commit(); con.close(); return jsonify({"ok":True,"season":season})


def compute_standings(con):
    rows=con.execute("SELECT id,name FROM teams ORDER BY name").fetchall()
    result=[]
    for t in rows:
        ms=con.execute("""SELECT home_team,away_team,home_goals,away_goals FROM matches
                          WHERE status IN ('official','auto')
                          AND (home_team=? OR away_team=?)""",(t["id"],t["id"])).fetchall()
        played=wins=draws=losses=gf=ga=points=0
        for m in ms:
            played += 1
            if m["home_team"] == t["id"]:
                a,b=m["home_goals"],m["away_goals"]
            else:
                a,b=m["away_goals"],m["home_goals"]
            gf += a; ga += b
            if a>b:
                wins += 1; points += 3
            elif a==b:
                draws += 1; points += 1
            else:
                losses += 1
        owner=con.execute("SELECT telegram_id,username,first_name FROM users WHERE team_id=? AND active=1",(t["id"],)).fetchone()
        result.append({
            "id":t["id"],"name":t["name"],"logo":team_logo(t["name"]),
            "owner_id":owner["telegram_id"] if owner else None,
            "owner":(owner["username"] or owner["first_name"]) if owner else None,
            "played":played,"wins":wins,"draws":draws,"losses":losses,
            "gf":gf,"ga":ga,"gd":gf-ga,"points":points
        })
    result.sort(key=lambda x:(-x["points"],-x["gd"],-x["gf"],x["name"]))
    for i,x in enumerate(result,1):
        x["rank"]=i
    return result


def save_season_awards(con):
    total_rounds=con.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"]
    total_matches=con.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    done=con.execute("SELECT COUNT(*) c FROM matches WHERE status IN ('official','auto')").fetchone()["c"]
    sf=con.execute("SELECT value FROM settings WHERE key='season_finished'").fetchone()
    if total_rounds!=38 or total_matches==0 or done!=total_matches or (sf and sf["value"]=="1"):
        return False
    season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])
    table=compute_standings(con)
    for x in table[:3]:
        owner=con.execute("SELECT telegram_id FROM users WHERE team_id=? AND active=1",(x["id"],)).fetchone()
        if owner:
            con.execute("""INSERT OR IGNORE INTO achievements
                         (telegram_id,season,place,team_id,team_name)
                         VALUES(?,?,?,?,?)""",
                        (owner["telegram_id"],season,x["rank"],x["id"],x["name"]))
    con.execute("UPDATE settings SET value='1' WHERE key='season_finished'")
    con.commit()
    return True


@app.get("/api/season")
def season_info_new():
    con=db()
    finalize_due_matches(con)
    save_season_awards(con)
    season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])
    finished=con.execute("SELECT value FROM settings WHERE key='season_finished'").fetchone()["value"]=="1"
    rounds=con.execute("SELECT COUNT(*) c FROM rounds").fetchone()["c"]
    con.close()
    return jsonify({"ok":True,"season":season,"finished":finished,"rounds":rounds})


@app.get("/api/season/winners")
def season_winners_new():
    con=db()
    finalize_due_matches(con)
    save_season_awards(con)
    season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])
    finished=con.execute("SELECT value FROM settings WHERE key='season_finished'").fetchone()["value"]=="1"
    rows=con.execute("""SELECT a.place,a.telegram_id,a.team_id,a.team_name,u.username,u.first_name
                        FROM achievements a LEFT JOIN users u ON u.telegram_id=a.telegram_id
                        WHERE a.season=? ORDER BY a.place""",(season,)).fetchall()
    winners=[]
    for r in rows:
        team=r["team_name"]
        if not team and r["team_id"]:
            tr=con.execute("SELECT name FROM teams WHERE id=?",(r["team_id"],)).fetchone()
            team=tr["name"] if tr else None
        winners.append({"place":r["place"],"telegram_id":r["telegram_id"],
                        "team":team or "Jamoa","logo":team_logo(team) if team else "",
                        "username":r["username"],"name":r["first_name"] or r["username"] or "Ishtirokchi"})
    con.close()
    return jsonify({"ok":True,"season":season,"finished":finished,"winners":winners})


@app.get("/api/participants")
def participants_new():
    con=db()
    rows=con.execute("""SELECT u.telegram_id,u.username,u.first_name,t.id team_id,t.name team_name
                        FROM users u LEFT JOIN teams t ON t.id=u.team_id
                        WHERE u.active=1 ORDER BY t.name,u.first_name""").fetchall()
    out=[]
    for r in rows:
        a=con.execute("SELECT place,COUNT(*) c FROM achievements WHERE telegram_id=? GROUP BY place",(r["telegram_id"],)).fetchall()
        cups={str(x["place"]):x["c"] for x in a}
        out.append({"telegram_id":r["telegram_id"],"username":r["username"],"first_name":r["first_name"],
                    "team_id":r["team_id"],"team_name":r["team_name"],
                    "logo":team_logo(r["team_name"]) if r["team_name"] else "",
                    "cups":{"1":cups.get("1",0),"2":cups.get("2",0),"3":cups.get("3",0)}})
    con.close()
    return jsonify(out)


@app.get("/api/profile_full/<int:telegram_id>")
def profile_full(telegram_id):
    con=db()
    finalize_due_matches(con)
    save_season_awards(con)
    u=user_row(con,telegram_id)
    if not u:
        con.close()
        return jsonify({"ok":False,"error":"Profil topilmadi"}),404
    cups_rows=con.execute("SELECT place,COUNT(*) c FROM achievements WHERE telegram_id=? GROUP BY place",(telegram_id,)).fetchall()
    cups={str(x["place"]):x["c"] for x in cups_rows}
    st={"played":0,"wins":0,"draws":0,"losses":0,"gf":0,"ga":0,"gd":0,"points":0}
    matches=[]
    if u["team_id"]:
        rows=con.execute("""SELECT m.*,r.round_no,r.round_date,ht.name home_name,at.name away_name
                            FROM matches m JOIN rounds r ON r.id=m.round_id
                            JOIN teams ht ON ht.id=m.home_team JOIN teams at ON at.id=m.away_team
                            WHERE m.home_team=? OR m.away_team=? ORDER BY r.round_no,m.id""",
                         (u["team_id"],u["team_id"])).fetchall()
        for m in rows:
            matches.append({"id":m["id"],"round_no":m["round_no"],"round_date":m["round_date"],
                            "home":m["home_name"],"away":m["away_name"],
                            "home_goals":m["home_goals"],"away_goals":m["away_goals"],"status":m["status"]})
            if m["status"] in ("official","auto"):
                st["played"]+=1
                gf,ga=(m["home_goals"],m["away_goals"]) if m["home_team"]==u["team_id"] else (m["away_goals"],m["home_goals"])
                st["gf"]+=gf; st["ga"]+=ga
                if gf>ga: st["wins"]+=1; st["points"]+=3
                elif gf==ga: st["draws"]+=1; st["points"]+=1
                else: st["losses"]+=1
    st["gd"]=st["gf"]-st["ga"]
    season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])
    team_name=u["team_name"]
    con.close()
    return jsonify({"ok":True,"season":season,"user":dict(u),
                    "logo":team_logo(team_name) if team_name else "",
                    "cups":{"1":cups.get("1",0),"2":cups.get("2",0),"3":cups.get("3",0)},
                    "stats":st,"matches":matches})


@app.post("/api/admin/start_next_season_safe")
def start_next_season_safe():
    _,err=require_admin()
    if err:return err
    con=db()
    finalize_due_matches(con)
    save_season_awards(con)
    finished=con.execute("SELECT value FROM settings WHERE key='season_finished'").fetchone()["value"]=="1"
    if not finished:
        con.close()
        return jsonify({"ok":False,"error":"Avval joriy 38 turdagi barcha o'yinlar yakunlanishi kerak"}),409
    con.execute("DELETE FROM matches")
    con.execute("DELETE FROM rounds")
    season=int(con.execute("SELECT value FROM settings WHERE key='season'").fetchone()["value"])+1
    con.execute("UPDATE settings SET value=? WHERE key='season'",(str(season),))
    con.execute("UPDATE settings SET value='0' WHERE key='season_finished'")
    con.commit(); con.close()
    return jsonify({"ok":True,"season":season})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))

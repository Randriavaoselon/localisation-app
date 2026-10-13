#!/usr/bin/env python3
"""
server.py - Wifi Tracker multi-utilisateurs.

Chaque utilisateur crée un compte, se connecte, puis crée ses propres « clients » (un par appareil).
Chaque client reçoit un jeton unique. Le programme client.py tourne sur l'appareil, lit ses
ordres sur le serveur (Démarrer / Arrêter depuis le tableau de bord) et envoie ses positions.
Un utilisateur ne voit jamais les clients d'un autre.

Test local :
    pip install -r requirements.txt
    python server.py                    # http://127.0.0.1:5000

Production (voir README.md) :
    export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
    export COOKIE_SECURE=1 DB_PATH=/data/wifi_reports.db
    gunicorn -w 1 -b 0.0.0.0:8000 server:app     # derrière HTTPS (Caddy / nginx / PaaS)

Variables d'environnement :
    SECRET_KEY           clé de signature des sessions (sinon générée et gardée à côté de la base)
    COOKIE_SECURE        1 = cookie de session réservé à HTTPS (OBLIGATOIRE en ligne)
    ALLOW_SIGNUP         0 = fermer la création de comptes (défaut 1)
    MAX_CLIENTS_PER_USER nombre max de clients par compte (défaut 10)
    DB_PATH              fichier SQLite (défaut : wifi_reports.db)
    ONLINE_SECONDS       délai avant de marquer une position « hors ligne » (défaut 120)
    AGENT_SECONDS        délai avant de marquer un agent « non connecté » (défaut 45)
    REPORT_INTERVAL      secondes entre deux envois d'un client (défaut 30)
    RETENTION_DAYS       durée de conservation de l'historique (défaut 30)
    STOP_RADIUS_M        distance (m) en dessous de laquelle des positions comptent comme le même lieu (défaut 150)
    TRUST_PROXY          1 = lire l'IP réelle dans X-Forwarded-For (derrière Render, Caddy, nginx...)
    TILE_URL / TILE_ATTR fond de carte (défaut OpenStreetMap ; voir README pour un autre fournisseur)
    HOST / PORT          adresse d'écoute pour 'python server.py'
"""

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
from datetime import timedelta
from functools import wraps

from flask import Flask, Response, g, jsonify, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.getenv("DB_PATH", "wifi_reports.db")
ONLINE_SECONDS = int(os.getenv("ONLINE_SECONDS", "120"))
AGENT_SECONDS = int(os.getenv("AGENT_SECONDS", "45"))
REPORT_INTERVAL = int(os.getenv("REPORT_INTERVAL", "30"))
POLL_SECONDS = 10
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))
STOP_RADIUS_M = int(os.getenv("STOP_RADIUS_M", "150"))
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "1") != "0"
MAX_CLIENTS = int(os.getenv("MAX_CLIENTS_PER_USER", "10"))
TRUST_PROXY = os.getenv("TRUST_PROXY") == "1"
COOKIE_SECURE = os.getenv("COOKIE_SECURE") == "1"
TILE_URL = os.getenv("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_ATTR = os.getenv("TILE_ATTR", "&copy; contributeurs OpenStreetMap")

USERNAME_RE = re.compile(r"[A-Za-z0-9_.\-]{3,32}")
CLIENT_NAME_RE = re.compile(r"[\w .\-@]{1,64}")


def load_secret_key():
    """SECRET_KEY de l'environnement, sinon une clé générée une fois et gardée à côté de la base."""
    key = os.getenv("SECRET_KEY")
    if key:
        return key
    path = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), ".secret_key")
    for _ in range(3):
        try:
            with open(path) as f:
                value = f.read().strip()
            if value:
                return value
        except FileNotFoundError:
            pass
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            time.sleep(0.2)
    raise RuntimeError("Impossible de créer .secret_key : définissez SECRET_KEY")


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    MAX_CONTENT_LENGTH=64 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL COLLATE NOCASE,
  token_hash TEXT NOT NULL UNIQUE,
  desired TEXT NOT NULL DEFAULT 'running',
  created_at REAL NOT NULL,
  last_poll REAL,
  UNIQUE (user_id, name)
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device TEXT NOT NULL,
  received_at REAL NOT NULL,
  client_time TEXT,
  ssid TEXT, bssid TEXT, signal_dbm INTEGER, neighbors INTEGER,
  lat REAL NOT NULL, lon REAL NOT NULL,
  accuracy_m REAL, altitude REAL, altitude_accuracy REAL,
  place TEXT, method TEXT, approx INTEGER DEFAULT 0,
  neighbor_list TEXT,
  client_id INTEGER
);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_reports_client ON reports(client_id, id);
CREATE INDEX IF NOT EXISTS idx_reports_time ON reports(received_at);
CREATE INDEX IF NOT EXISTS idx_clients_user ON clients(user_id);
"""


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    cols = [r[1] for r in con.execute("PRAGMA table_info(reports)")]
    if "client_id" not in cols:  # ancienne base (avant les comptes) : on ajoute la colonne
        con.execute("ALTER TABLE reports ADD COLUMN client_id INTEGER")
    con.executescript(INDEXES)
    con.commit()
    con.close()


init_db()
_insert_count = 0
DUMMY_HASH = generate_password_hash("mot-de-passe-factice")  # égalise le temps de réponse au login

POS_FIELDS = ("lat", "lon", "accuracy_m", "altitude", "ssid", "bssid", "signal_dbm",
              "place", "method", "client_time")


def report_fields(r):
    """Dernière position d'un client, prête pour le JSON (sans données de voisinage)."""
    d = {k: r[k] for k in POS_FIELDS}
    age = time.time() - r["received_at"]
    d["age_s"] = max(0, int(age))
    d["online"] = age <= ONLINE_SECONDS
    d["approx"] = bool(r["approx"])
    return d


# --------------------------------------------------------------------------
# Sécurité : IP, limitation des tentatives, sessions, CSRF
# --------------------------------------------------------------------------
def client_ip():
    if TRUST_PROXY:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return request.remote_addr or "?"


_attempts = {}


def throttled(key, limit, window):
    now = time.time()
    recent = [t for t in _attempts.get(key, []) if now - t < window]
    _attempts[key] = recent
    return len(recent) >= limit


def note_attempt(key):
    if len(_attempts) > 5000:  # nettoyage
        cutoff = time.time() - 3600
        for k in [k for k, v in _attempts.items() if not v or v[-1] < cutoff]:
            del _attempts[k]
    _attempts.setdefault(key, []).append(time.time())


def _eq(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    row = get_db().execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        session.clear()
    return row


def start_session(uid):
    session.clear()
    session["uid"] = uid
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True


def api_user(f):
    """Route API réservée à un utilisateur connecté ; les écritures exigent le jeton CSRF."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return jsonify(error="non connecté"), 401
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-CSRF-Token", "")
            if not session.get("csrf") or not _eq(sent, session["csrf"]):
                return jsonify(error="jeton CSRF invalide, rechargez la page"), 403
        return f(*args, **kwargs)
    return wrapper


def own_client(cid):
    """Client appartenant à l'utilisateur connecté (None sinon : un autre compte = introuvable)."""
    return get_db().execute("SELECT * FROM clients WHERE id = ? AND user_id = ?",
                            (cid, session["uid"])).fetchone()


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def agent_client():
    """Client identifié par son jeton (en-tête Authorization: Bearer ...)."""
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer ") or len(h) < 20:
        return None
    return get_db().execute("SELECT * FROM clients WHERE token_hash = ?",
                            (hash_token(h[7:].strip()),)).fetchone()


@app.after_request
def secure_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    # Les serveurs de tuiles (OpenStreetMap...) exigent un Referer : ne PAS mettre "no-referrer".
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def num(v, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return x


def txt(v, n=120):
    return None if v is None else str(v)[:n]


def json_body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


# --------------------------------------------------------------------------
# API : comptes
# --------------------------------------------------------------------------
@app.post("/api/signup")
def api_signup():
    if not ALLOW_SIGNUP:
        return jsonify(error="la création de comptes est fermée"), 403
    key = "signup:" + client_ip()
    if throttled(key, 10, 3600):
        return jsonify(error="trop de tentatives, réessayez plus tard"), 429
    note_attempt(key)
    d = json_body()
    username, password = str(d.get("username", "")).strip(), str(d.get("password", ""))
    if not USERNAME_RE.fullmatch(username):
        return jsonify(error="nom d'utilisateur : 3 à 32 caractères (lettres, chiffres, . _ -)"), 400
    if not 8 <= len(password) <= 128:
        return jsonify(error="mot de passe : 8 à 128 caractères"), 400
    db = get_db()
    try:
        cur = db.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                         (username, generate_password_hash(password), time.time()))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="ce nom d'utilisateur est déjà pris"), 409
    start_session(cur.lastrowid)
    return jsonify(ok=True, username=username), 201


@app.post("/api/login")
def api_login():
    d = json_body()
    username, password = str(d.get("username", "")).strip()[:64], str(d.get("password", ""))[:128]
    keys = ["login-ip:" + client_ip(), "login-user:" + username.lower()]
    if any(throttled(k, 8, 300) for k in keys):
        return jsonify(error="trop de tentatives, réessayez dans quelques minutes"), 429
    row = get_db().execute("SELECT id, password_hash FROM users WHERE username = ?",
                           (username,)).fetchone()
    ok = check_password_hash(row["password_hash"] if row else DUMMY_HASH, password)
    if not (row and ok):
        for k in keys:
            note_attempt(k)
        return jsonify(error="identifiants incorrects"), 401
    _attempts.pop(keys[1], None)
    start_session(row["id"])
    return jsonify(ok=True, username=username)


@app.post("/api/logout")
@api_user
def api_logout():
    session.clear()
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# API : clients de l'utilisateur connecté
# --------------------------------------------------------------------------
@app.get("/api/clients")
@api_user
def api_clients():
    rows = get_db().execute(
        """SELECT c.id AS cid, c.name AS cname, c.desired, c.last_poll, r.*
           FROM clients c
           LEFT JOIN reports r ON r.id = (SELECT MAX(id) FROM reports WHERE client_id = c.id)
           WHERE c.user_id = ? ORDER BY c.name""", (session["uid"],)).fetchall()
    now, out = time.time(), []
    for r in rows:
        seen = r["last_poll"]
        d = {"id": r["cid"], "name": r["cname"], "desired": r["desired"],
             "agent_online": seen is not None and now - seen <= AGENT_SECONDS,
             "has_position": r["lat"] is not None}
        if d["has_position"]:
            d.update(report_fields(r))
        out.append(d)
    return jsonify(out)


@app.post("/api/clients")
@api_user
def api_client_create():
    name = str(json_body().get("name", "")).strip()
    if not CLIENT_NAME_RE.fullmatch(name):
        return jsonify(error="nom invalide (1 à 64 caractères : lettres, chiffres, espace . - _ @)"), 400
    db = get_db()
    n = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (session["uid"],)).fetchone()[0]
    if n >= MAX_CLIENTS:
        return jsonify(error=f"limite de {MAX_CLIENTS} clients atteinte"), 400
    token = secrets.token_urlsafe(32)
    try:
        cur = db.execute(
            "INSERT INTO clients (user_id, name, token_hash, created_at) VALUES (?,?,?,?)",
            (session["uid"], name, hash_token(token), time.time()))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="vous avez déjà un client portant ce nom"), 409
    return jsonify(id=cur.lastrowid, name=name, token=token), 201


@app.post("/api/clients/<int:cid>/command")
@api_user
def api_client_command(cid):
    if not own_client(cid):
        return jsonify(error="client introuvable"), 404
    action = json_body().get("action")
    if action not in ("start", "stop"):
        return jsonify(error="action attendue : start ou stop"), 400
    db = get_db()
    db.execute("UPDATE clients SET desired = ? WHERE id = ?",
               ("running" if action == "start" else "stopped", cid))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/clients/<int:cid>/token")
@api_user
def api_client_token(cid):
    """Nouveau jeton (l'ancien cesse de fonctionner : à utiliser si le jeton a fuité)."""
    if not own_client(cid):
        return jsonify(error="client introuvable"), 404
    token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute("UPDATE clients SET token_hash = ? WHERE id = ?", (hash_token(token), cid))
    db.commit()
    return jsonify(token=token)


@app.delete("/api/clients/<int:cid>")
@api_user
def api_client_delete(cid):
    if not own_client(cid):
        return jsonify(error="client introuvable"), 404
    db = get_db()
    db.execute("DELETE FROM reports WHERE client_id = ?", (cid,))
    db.execute("DELETE FROM clients WHERE id = ?", (cid,))
    db.commit()
    return jsonify(ok=True)


@app.get("/api/history/<int:cid>")
@api_user
def api_history(cid):
    if not own_client(cid):
        return jsonify(error="client introuvable"), 404
    limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
    rows = get_db().execute(
        "SELECT * FROM reports WHERE client_id = ? ORDER BY id DESC LIMIT ?", (cid, limit)
    ).fetchall()
    return jsonify([report_fields(r) for r in reversed(rows)])


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def build_stops(rows):
    """Regroupe les positions successives proches en 'étapes' (un lieu = une étape).
    Si l'appareil revient plus tard dans un lieu déjà visité, cela crée une nouvelle étape."""
    stops = []
    for r in rows:
        acc = min(r["accuracy_m"] or 0, 1000)
        radius = max(STOP_RADIUS_M, acc)
        cur = stops[-1] if stops else None
        if cur and haversine_m(cur["lat"], cur["lon"], r["lat"], r["lon"]) <= radius:
            n = cur["count"]
            cur["lat"] = (cur["lat"] * n + r["lat"]) / (n + 1)  # centre moyen du lieu
            cur["lon"] = (cur["lon"] * n + r["lon"]) / (n + 1)
            cur["count"] = n + 1
            cur["last_seen"] = r["received_at"]
            if r["ssid"] and r["ssid"] not in cur["ssids"]:
                cur["ssids"].append(r["ssid"])
            if r["place"]:
                cur["place"] = r["place"]
            if r["altitude"] is not None:
                cur["altitude"] = r["altitude"]
        else:
            stops.append({
                "lat": r["lat"], "lon": r["lon"], "count": 1,
                "first_seen": r["received_at"], "last_seen": r["received_at"],
                "ssids": [r["ssid"]] if r["ssid"] else [],
                "place": r["place"] or "", "altitude": r["altitude"],
            })
    return stops


@app.get("/api/trajectory/<int:cid>")
@api_user
def api_trajectory(cid):
    """Trajectoire d'un client : étapes chronologiques, de la 1re à la dernière connexion.
    Paramètres : hours (0 = tout l'historique), approx=1 pour inclure les positions IP."""
    client = own_client(cid)
    if not client:
        return jsonify(error="client introuvable"), 404
    hours = max(request.args.get("hours", 0, type=float), 0)
    sql, params = "SELECT * FROM reports WHERE client_id = ?", [cid]
    if hours > 0:
        sql += " AND received_at >= ?"
        params.append(time.time() - hours * 3600)
    if request.args.get("approx") != "1":   # on écarte les positions IP / trop imprécises
        sql += " AND approx = 0 AND (accuracy_m IS NULL OR accuracy_m <= 2000)"
    sql += " ORDER BY id DESC LIMIT 20000"
    rows = list(reversed(get_db().execute(sql, params).fetchall()))
    stops = build_stops(rows)
    dist = sum(haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
               for a, b in zip(stops, stops[1:]))
    out = dict(device=client["name"], points=len(rows), stops=stops, distance_m=round(dist),
               first_seen=stops[0]["first_seen"] if stops else None,
               last_seen=stops[-1]["last_seen"] if stops else None)
    if request.args.get("points") == "1":   # toutes les mesures (les 2000 dernières)
        out["track"] = [[r["lat"], r["lon"], r["received_at"], r["accuracy_m"], r["ssid"],
                         bool(r["approx"])] for r in rows[-2000:]]
    return jsonify(out)


# --------------------------------------------------------------------------
# API : agent (client.py sur l'appareil) — authentifié par jeton, sans cookie
# --------------------------------------------------------------------------
def touch_agent(cid):
    get_db().execute("UPDATE clients SET last_poll = ? WHERE id = ?", (time.time(), cid))


@app.get("/api/agent/poll")
def api_agent_poll():
    """L'agent demande ce qu'il doit faire : 'running' (suivre) ou 'stopped' (attendre)."""
    c = agent_client()
    if not c:
        return jsonify(error="jeton invalide"), 401
    touch_agent(c["id"])
    get_db().commit()
    return jsonify(desired=c["desired"], name=c["name"], interval=REPORT_INTERVAL,
                   poll=POLL_SECONDS)


@app.post("/api/agent/report")
def api_agent_report():
    global _insert_count
    c = agent_client()
    if not c:
        return jsonify(error="jeton invalide"), 401
    db = get_db()
    touch_agent(c["id"])
    if c["desired"] != "running":  # arrêt demandé depuis le tableau de bord : rien n'est enregistré
        db.commit()
        return jsonify(ok=True, stored=False, desired=c["desired"])

    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify(error="JSON attendu"), 400
    pos = d.get("position") if isinstance(d.get("position"), dict) else {}
    lat, lon = num(pos.get("lat"), -90, 90), num(pos.get("lon"), -180, 180)
    if lat is None or lon is None:
        return jsonify(error="position.lat / position.lon invalides"), 400

    wifi = d.get("wifi") if isinstance(d.get("wifi"), dict) else {}
    sig = num(wifi.get("signal_dbm"), -150, 0)
    nb = num(d.get("neighbors"), 0, 10000)
    nlist = d.get("neighbor_list")
    nlist = json.dumps(nlist[:50])[:8000] if isinstance(nlist, list) else None

    cur = db.execute(
        """INSERT INTO reports (device, client_id, received_at, client_time, ssid, bssid, signal_dbm,
             neighbors, lat, lon, accuracy_m, altitude, altitude_accuracy, place, method,
             approx, neighbor_list)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (c["name"], c["id"], time.time(), txt(d.get("client_time"), 40),
         txt(wifi.get("ssid"), 64), txt(wifi.get("bssid"), 17),
         int(sig) if sig is not None else None, int(nb) if nb is not None else None,
         lat, lon, num(pos.get("accuracy_m"), 0, 1e7), num(pos.get("altitude"), -1000, 100000),
         num(pos.get("altitude_accuracy"), 0, 1e6), txt(pos.get("place"), 200),
         txt(pos.get("method"), 120), 1 if pos.get("approx") else 0, nlist),
    )
    _insert_count += 1
    if _insert_count % 200 == 0:  # purge périodique
        db.execute("DELETE FROM reports WHERE received_at < ?",
                   (time.time() - RETENTION_DAYS * 86400,))
    db.commit()
    return jsonify(ok=True, stored=True, id=cur.lastrowid, desired=c["desired"])


@app.get("/health")
def health():
    return jsonify(status="ok")


# --------------------------------------------------------------------------
# Pages : connexion et tableau de bord
# --------------------------------------------------------------------------
LOGIN_PAGE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wifi Tracker · Connexion</title>
<style>
 body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  font-family:system-ui,sans-serif;background:#f2f5f9;color:#222}
 .card{background:#fff;width:340px;max-width:calc(100% - 32px);padding:24px;border-radius:10px;
  box-shadow:0 4px 20px rgba(0,0,0,.12)}
 h1{font-size:20px;margin:0 0 14px}
 .tabs{display:flex;gap:6px;margin-bottom:14px}
 .tabs button{flex:1;padding:8px;border:0;border-bottom:2px solid #ddd;background:none;cursor:pointer;font-size:14px}
 .tabs button.on{border-color:#0b6bcb;font-weight:600}
 label{display:block;font-size:13px;margin:10px 0 3px}
 input{width:100%;box-sizing:border-box;padding:9px;font-size:14px;border:1px solid #ccc;border-radius:6px}
 .go{width:100%;margin-top:16px;padding:10px;border:0;border-radius:6px;background:#0b6bcb;color:#fff;
  font-size:15px;cursor:pointer}
 .go:disabled{opacity:.6}
 #err{color:#c0392b;font-size:13px;min-height:18px;margin-top:10px}
 small{color:#666}
</style></head><body>
<div class="card">
  <h1>📡 Wifi Tracker</h1>
  <div class="tabs">
    <button id="tabLogin" class="on" type="button">Connexion</button>
    <button id="tabSignup" type="button">Créer un compte</button>
  </div>
  <div>
    <label for="u">Nom d'utilisateur</label>
    <input id="u" autocomplete="username" maxlength="32" autofocus>
    <label for="p">Mot de passe</label>
    <input id="p" type="password" autocomplete="current-password" maxlength="128">
    <div id="hint"><small></small></div>
    <button class="go" id="go" type="button">Se connecter</button>
    <div id="err"></div>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
const SIGNUP = __SIGNUP__;
let mode = "login";
function setMode(m) {
  mode = m;
  $("tabLogin").className = m === "login" ? "on" : "";
  $("tabSignup").className = m === "signup" ? "on" : "";
  $("go").textContent = m === "login" ? "Se connecter" : "Créer mon compte";
  $("p").autocomplete = m === "login" ? "current-password" : "new-password";
  $("hint").firstChild.textContent = m === "signup" ? "Mot de passe : 8 caractères minimum." : "";
  $("err").textContent = "";
}
$("tabLogin").onclick = () => setMode("login");
if (SIGNUP) $("tabSignup").onclick = () => setMode("signup"); else $("tabSignup").hidden = true;
async function submit() {
  $("go").disabled = true; $("err").textContent = "";
  try {
    const r = await fetch(mode === "login" ? "/api/login" : "/api/signup", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: $("u").value, password: $("p").value})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "Erreur " + r.status);
    location.href = "/";
  } catch (e) { $("err").textContent = e.message; $("go").disabled = false; }
}
$("go").onclick = submit;
$("p").onkeydown = e => { if (e.key === "Enter") submit(); };
</script></body></html>
"""

DASHBOARD = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wifi Tracker</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
 html,body{height:100%;margin:0;font-family:system-ui,sans-serif;color:#222}
 #side{position:absolute;top:0;bottom:0;left:0;width:340px;background:#fff;z-index:1000;
  box-shadow:2px 0 10px rgba(0,0,0,.2);display:flex;flex-direction:column}
 #top{display:flex;justify-content:space-between;align-items:center;padding:12px 14px 4px;gap:8px}
 #top h1{font-size:16px;margin:0}
 #me{font-size:12px;color:#555;display:flex;align-items:center;gap:6px}
 #me button{border:0;background:#eee;border-radius:4px;cursor:pointer;padding:3px 8px}
 #status{font-size:12px;color:#666;padding:0 14px 8px}
 #bar{padding:0 14px 10px;border-bottom:1px solid #eee}
 button.primary{background:#0b6bcb;color:#fff;border:0;border-radius:6px;padding:7px 12px;cursor:pointer;font-size:13px}
 button.primary:disabled{opacity:.6}
 #list{overflow:auto;flex:1 1 auto;min-height:90px;margin:0;padding:0;list-style:none}
 #list li{padding:10px 14px;border-bottom:1px solid #f0f0f0;cursor:pointer;font-size:13px}
 #list li:hover,#list li.sel{background:#eef5ff}
 #list .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
 #list small{color:#666;display:block}
 .acts{margin-top:6px;display:flex;gap:6px}
 .acts button{border:1px solid #ccd;background:#fff;border-radius:4px;cursor:pointer;padding:2px 8px;font-size:12px}
 .acts button:hover{background:#f3f6fb}
 .empty{padding:14px;color:#666;font-size:13px;cursor:default}
 #traj{flex:0 1 50%;overflow:auto;border-top:2px solid #0b6bcb;padding:10px 14px;font-size:12px}
 #traj[hidden]{display:none}
 .trajhead{display:flex;justify-content:space-between;align-items:center;gap:8px}
 .trajhead button{border:0;background:#eee;border-radius:4px;cursor:pointer;padding:2px 8px}
 #traj select{width:100%;margin:8px 0 4px;padding:4px}
 #trajsummary{color:#555;margin:6px 0}
 #stops{list-style:none;margin:0;padding:0}
 #stops li{padding:7px 4px;border-bottom:1px solid #f0f0f0;cursor:pointer}
 #stops li:hover{background:#eef5ff}
 #stops small{display:block;color:#666}
 .stop{width:26px;height:26px;border-radius:50%;color:#fff;font:700 12px system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;border:2px solid #fff;
  box-shadow:0 1px 4px rgba(0,0,0,.4);box-sizing:border-box}
 .stop.start{background:#1a9850}.stop.end{background:#d73027}.stop.mid{background:#0b6bcb}
 .arrow{color:#0b6bcb;font-size:16px;line-height:18px;width:18px;height:18px;text-align:center;
  text-shadow:0 0 3px #fff}
 .pop div{line-height:1.5}.warn{color:#a15c00;margin-top:4px}
 #map{position:absolute;top:0;bottom:0;left:340px;right:0}
 #modal{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:3000;display:flex;
  align-items:center;justify-content:center}
 #modal[hidden]{display:none}
 #modalbox{background:#fff;border-radius:10px;padding:20px;width:520px;max-width:calc(100% - 32px);
  max-height:90%;overflow:auto;font-size:14px}
 #modalbox h2{margin:0 0 10px;font-size:17px}
 #modalbox input{width:100%;box-sizing:border-box;padding:9px;font-size:14px;border:1px solid #ccc;border-radius:6px}
 #modalbox pre{background:#f4f4f4;padding:10px;border-radius:6px;white-space:pre-wrap;word-break:break-all;
  font-size:12px;margin:8px 0}
 #modalbox .row{display:flex;gap:8px;margin-top:14px;justify-content:flex-end}
 #modalbox .row button{padding:7px 14px;border-radius:6px;border:1px solid #ccc;background:#fff;cursor:pointer}
 #modalbox .row button.primary{border:0;background:#0b6bcb}
 .err{color:#c0392b;font-size:13px;min-height:16px;margin-top:8px}
 .note{color:#555;font-size:13px}
 @media (max-width:700px){#side{width:100%;height:52%;bottom:auto}#map{left:0;top:52%}}
</style></head><body>
<div id="side">
  <div id="top">
    <h1>📡 Wifi Tracker</h1>
    <div id="me"><span id="who"></span><button id="logout" type="button">Déconnexion</button></div>
  </div>
  <div id="status">Chargement…</div>
  <div id="bar"><button class="primary" id="newbtn" type="button">➕ Nouveau client</button></div>
  <ul id="list"></ul>
  <div id="traj" hidden>
    <div class="trajhead"><b id="trajtitle"></b><button id="trajclose" title="Fermer">✕</button></div>
    <select id="period">
      <option value="1">Dernière heure</option>
      <option value="24">24 dernières heures</option>
      <option value="168">7 derniers jours</option>
      <option value="0" selected>Tout l'historique</option>
    </select>
    <label><input type="checkbox" id="trailToggle" checked> Afficher la trajectoire sur la carte</label><br>
    <label><input type="checkbox" id="pointsToggle"> Afficher toutes les mesures (petits points)</label><br>
    <label><input type="checkbox" id="approxToggle"> Inclure les positions approximatives (IP)</label>
    <div id="trajsummary"></div>
    <ol id="stops"></ol>
  </div>
</div>
<div id="map"></div>
<div id="modal" hidden><div id="modalbox"></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const $ = id => document.getElementById(id);
const CSRF = __CSRF__, USER = __USER__;
const TILE_URL = __TILE_URL__, TILE_ATTR = __TILE_ATTR__;
const map = L.map("map").setView([0, 20], 2);
L.tileLayer(TILE_URL, {maxZoom: 19, attribution: TILE_ATTR}).addTo(map);
const layers = {};
const trailLayer = L.layerGroup().addTo(map);
let selected = null, selectedName = "", firstFit = true, showTrail = true, showPoints = false, withApprox = false, hours = 0;
let stopMarkers = [], lastSig = "";
$("who").textContent = "👤 " + USER;

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
async function api(path, method, body) {
  const opt = {method: method || "GET", headers: {"X-CSRF-Token": CSRF}};
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/login"; throw new Error("session expirée"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
  return j;
}
function fmtAge(s) {
  if (s < 60) return s + " s";
  if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) return Math.floor(s / 3600) + " h";
  return Math.floor(s / 86400) + " j";
}
function fmtDate(t) {
  return new Date(t * 1000).toLocaleString([], {day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit"});
}
function fmtDur(s) {
  if (s < 60) return "< 1 min";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) {
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return h + " h" + (m ? " " + m + " min" : "");
  }
  return Math.floor(s / 86400) + " j " + Math.floor((s % 86400) / 3600) + " h";
}
function fmtDist(m) { return m >= 1000 ? (m / 1000).toFixed(1) + " km" : Math.round(m) + " m"; }
function bearing(a, b) {
  const r = Math.PI / 180, dl = (b[1] - a[1]) * r;
  const y = Math.sin(dl) * Math.cos(b[0] * r);
  const x = Math.cos(a[0] * r) * Math.sin(b[0] * r) - Math.sin(a[0] * r) * Math.cos(b[0] * r) * Math.cos(dl);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}
function addRows(box, rows) {
  for (const [k, v] of rows) {
    const d = el("div"); d.appendChild(el("b", k + " : ")); d.appendChild(document.createTextNode(v));
    box.appendChild(d);
  }
}
function details(c) {
  const box = el("div", undefined, "pop");
  addRows(box, [
    ["Appareil", c.name], ["Wi-Fi", c.ssid || "n/a"], ["BSSID", c.bssid || "n/a"],
    ["Signal", c.signal_dbm == null ? "n/a" : c.signal_dbm + " dBm"],
    ["Latitude", c.lat.toFixed(6)], ["Longitude", c.lon.toFixed(6)],
    ["Altitude", c.altitude == null ? "non disponible" : c.altitude.toFixed(1) + " m"],
    ["Précision", "±" + Math.round(c.accuracy_m || 0) + " m"],
    ["Lieu", c.place || "n/a"], ["Source", c.method || "n/a"],
    ["Dernier envoi", "il y a " + fmtAge(c.age_s)],
  ]);
  if (c.approx) box.appendChild(el("div", "⚠️ Position approximative (IP)", "warn"));
  return box;
}

/* ---------- Fenêtre modale ---------- */
function showModal(build) {
  const box = $("modalbox"); box.replaceChildren(); build(box); $("modal").hidden = false;
}
function closeModal() { $("modal").hidden = true; }
function modalButtons(box, buttons) {
  const row = el("div", undefined, "row");
  for (const b of buttons) row.appendChild(b);
  box.appendChild(row);
}
function btn(label, onclick, primary) {
  const b = el("button", label, primary ? "primary" : ""); b.type = "button"; b.onclick = onclick; return b;
}
async function copyText(text, button) {
  try { await navigator.clipboard.writeText(text); button.textContent = "✅ Copié"; }
  catch (e) { button.textContent = "Sélectionnez et copiez à la main"; }
}
function showCredentials(name, token, isNew) {
  const cmd = "python3 client.py --server " + location.origin + " --token " + token + " --save";
  showModal(box => {
    box.appendChild(el("h2", (isNew ? "✅ Client « " + name + " » créé" : "🔑 Nouveau jeton pour « " + name + " »")));
    box.appendChild(el("p", "Sur l'appareil à suivre, lancez cette commande une seule fois :", "note"));
    box.appendChild(el("pre", cmd));
    box.appendChild(el("p", "⚠️ Ce jeton ne sera plus affiché : copiez la commande maintenant. " +
      "Il est personnel, ne le partagez pas. Ensuite, vous pouvez vous déconnecter : le client " +
      "obéit aux boutons ▶ Démarrer / ⏹ Arrêter du tableau de bord.", "note"));
    const copy = btn("Copier la commande", () => copyText(cmd, copy));
    modalButtons(box, [copy, btn("Fermer", closeModal, true)]);
  });
}
function newClientDialog() {
  showModal(box => {
    box.appendChild(el("h2", "➕ Nouveau client"));
    box.appendChild(el("p", "Donnez un nom à l'appareil à suivre (ex. PC-Majunga, Téléphone-Alice).", "note"));
    const input = el("input"); input.maxLength = 64; input.placeholder = "Nom du client";
    box.appendChild(input);
    const err = el("div", "", "err"); box.appendChild(err);
    const create = btn("Créer", async () => {
      create.disabled = true; err.textContent = "";
      try {
        const j = await api("/api/clients", "POST", {name: input.value});
        showCredentials(j.name, j.token, true); refresh();
      } catch (e) { err.textContent = e.message; create.disabled = false; }
    }, true);
    input.onkeydown = e => { if (e.key === "Enter") create.click(); };
    modalButtons(box, [btn("Annuler", closeModal), create]);
    setTimeout(() => input.focus(), 0);
  });
}
$("newbtn").onclick = newClientDialog;
$("modal").onclick = e => { if (e.target === $("modal")) closeModal(); };
$("logout").onclick = async () => { try { await api("/api/logout", "POST", {}); } catch (e) {} location.href = "/login"; };

/* ---------- Trajectoire d'un client ---------- */
function stopKind(i, n) {
  if (n === 1) return ["start", "●", "Position unique"];
  if (i === 0) return ["start", "D", "Première connexion"];
  if (i === n - 1) return ["end", "F", "Dernière connexion"];
  return ["mid", String(i + 1), "Étape " + (i + 1)];
}
function stopPopup(s, i, n) {
  const box = el("div", undefined, "pop");
  const title = el("div"); title.appendChild(el("b", stopKind(i, n)[2] + (n > 1 ? "  (" + (i + 1) + "/" + n + ")" : "")));
  box.appendChild(title);
  const rows = [
    ["Lieu", s.place || "inconnu"],
    ["Wi-Fi", s.ssids.length ? s.ssids.join(", ") : "n/a"],
    ["Arrivée", fmtDate(s.first_seen)],
    ["Dernière mesure", fmtDate(s.last_seen)],
    ["Durée sur place", fmtDur(s.last_seen - s.first_seen)],
    ["Mesures", String(s.count)],
    ["Coordonnées", s.lat.toFixed(5) + ", " + s.lon.toFixed(5)],
  ];
  if (s.altitude != null) rows.push(["Altitude", s.altitude.toFixed(1) + " m"]);
  addRows(box, rows);
  return box;
}
async function drawTrajectory(force, fit) {
  const panel = $("traj");
  if (selected === null) { panel.hidden = true; trailLayer.clearLayers(); stopMarkers = []; return; }
  panel.hidden = false;
  $("trajtitle").textContent = "Trajectoire · " + selectedName;
  let data;
  try {
    data = await api("/api/trajectory/" + selected + "?hours=" + hours +
      (showPoints ? "&points=1" : "") + (withApprox ? "&approx=1" : ""));
  } catch (e) { $("trajsummary").textContent = "⚠️ " + e.message; return; }

  const stops = data.stops, n = stops.length;
  const ol = $("stops"); ol.replaceChildren();
  if (!n) {
    trailLayer.clearLayers(); stopMarkers = []; lastSig = "";
    $("trajsummary").textContent = "Aucune position fiable sur cette période.";
    return;
  }
  const first = stops[0], last = stops[n - 1];
  $("trajsummary").textContent = n + " étape(s) · " + fmtDist(data.distance_m) +
    " · du " + fmtDate(first.first_seen) + " au " + fmtDate(last.last_seen);

  const sig = [selected, hours, showTrail, showPoints, withApprox, n, data.points, last.first_seen].join("|");
  const redraw = force || sig !== lastSig;
  if (redraw) { trailLayer.clearLayers(); stopMarkers = []; }
  if (redraw && showTrail) {
    const pts = stops.map(s => [s.lat, s.lon]);
    if (n > 1) {
      L.polyline(pts, {color: "#0b6bcb", weight: 3, opacity: .75, dashArray: "6 6"}).addTo(trailLayer);
      for (let i = 1; i < n; i++) {   // flèches de direction au milieu de chaque trajet
        if (map.distance(pts[i - 1], pts[i]) < 30) continue;
        const mid = [(pts[i - 1][0] + pts[i][0]) / 2, (pts[i - 1][1] + pts[i][1]) / 2];
        const a = el("div", "➤", "arrow");
        a.style.transform = "rotate(" + (bearing(pts[i - 1], pts[i]) - 90) + "deg)";
        L.marker(mid, {icon: L.divIcon({html: a, className: "", iconSize: [18, 18]}),
                       interactive: false, keyboard: false}).addTo(trailLayer);
      }
    }
    if (showPoints && data.track) {          // chaque mesure reçue, en petit point
      for (const [lat, lon, t, acc, ssid, approx] of data.track) {
        L.circleMarker([lat, lon], {radius: 3, weight: 1, color: approx ? "#a15c00" : "#0b6bcb",
          fillColor: approx ? "#f0ad4e" : "#7fb2e5", fillOpacity: .9})
          .bindTooltip(el("span", fmtDate(t) + (ssid ? " · " + ssid : "") + (approx ? " · approx." : "")))
          .addTo(trailLayer);
      }
    }
    stops.forEach((s, i) => {
      const [cls, label, tip] = stopKind(i, n);
      const icon = L.divIcon({html: el("div", label, "stop " + cls), className: "",
                              iconSize: [26, 26], iconAnchor: [13, 13]});
      const m = L.marker([s.lat, s.lon], {icon: icon, zIndexOffset: cls === "mid" ? 0 : 1000})
        .bindPopup(stopPopup(s, i, n)).bindTooltip(el("span", tip)).addTo(trailLayer);
      stopMarkers.push(m);
    });
    if (fit) {
      if (n === 1) map.setView(pts[0], 16);
      else map.fitBounds(L.latLngBounds(pts), {padding: [60, 60], maxZoom: 16});
    }
  } else if (showTrail && stopMarkers.length === n) {
    stopMarkers[n - 1].setPopupContent(stopPopup(last, n - 1, n));   // durée à jour sans tout redessiner
  }
  lastSig = sig;

  stops.forEach((s, i) => {                // chronologie dans le panneau
    const [cls] = stopKind(i, n);
    const mark = cls === "start" ? "🟢 " : cls === "end" ? "🔴 " : "🔵 ";
    const li = el("li");
    li.append(el("b", mark + (i + 1) + ". " + (s.place || (s.ssids[0] || "Lieu inconnu"))),
      el("small", fmtDate(s.first_seen) + " → " + fmtDate(s.last_seen) + " · " + fmtDur(s.last_seen - s.first_seen)),
      el("small", s.ssids.length ? "Wi-Fi : " + s.ssids.join(", ") : "Wi-Fi : n/a"));
    li.onclick = () => { map.setView([s.lat, s.lon], 16); if (stopMarkers[i]) stopMarkers[i].openPopup(); };
    ol.appendChild(li);
  });
}
function select(c) {
  if (!c.has_position) { alert("Aucune position reçue pour l'instant : lancez le client sur l'appareil."); return; }
  selected = c.id; selectedName = c.name;
  map.setView([c.lat, c.lon], Math.max(map.getZoom(), 15));
  layers[c.id].dot.openPopup();
  lastSig = "";
  drawTrajectory(true, true);
  refresh();
}

/* ---------- Clients (liste, commandes, positions actuelles) ---------- */
function agentLine(c) {
  if (!c.agent_online) return c.desired === "running"
    ? "⚪ client non connecté (lancez client.py sur l'appareil)" : "⚪ client non connecté · suivi arrêté";
  return c.desired === "running" ? "🟢 client connecté · suivi actif" : "⏸ client connecté · suivi arrêté";
}
async function command(c, action) {
  try { await api("/api/clients/" + c.id + "/command", "POST", {action}); refresh(); }
  catch (e) { alert(e.message); }
}
function regenToken(c) {
  if (!confirm("Générer un nouveau jeton pour « " + c.name + " » ? L'ancien cessera de fonctionner.")) return;
  api("/api/clients/" + c.id + "/token", "POST", {}).then(j => showCredentials(c.name, j.token, false))
    .catch(e => alert(e.message));
}
function removeClient(c) {
  if (!confirm("Supprimer « " + c.name + " » et tout son historique ? Cette action est définitive.")) return;
  api("/api/clients/" + c.id, "DELETE").then(() => {
    if (selected === c.id) { selected = null; lastSig = ""; drawTrajectory(true, false); }
    refresh();
  }).catch(e => alert(e.message));
}
function actionButton(label, title, fn) {
  const b = el("button", label); b.title = title;
  b.onclick = e => { e.stopPropagation(); fn(); };
  return b;
}
async function refresh() {
  let list;
  try { list = await api("/api/clients"); }
  catch (e) { $("status").textContent = "⚠️ Serveur injoignable : " + e.message; return; }
  $("status").textContent = list.length + " client(s) · mis à jour " + new Date().toLocaleTimeString();
  const ids = new Set(list.map(c => c.id));
  for (const id of Object.keys(layers)) {          // clients supprimés : on retire leur marqueur
    if (!ids.has(Number(id))) { layers[id].dot.remove(); layers[id].ring.remove(); delete layers[id]; }
  }
  const ul = $("list"); ul.replaceChildren();
  if (!list.length) ul.appendChild(el("li", "Aucun client. Cliquez sur « Nouveau client » pour commencer.", "empty"));
  for (const c of list) {
    const color = c.has_position && c.online ? "#1a9850" : "#888";
    if (c.has_position) {
      let l = layers[c.id];
      if (!l) {
        l = layers[c.id] = {
          dot: L.circleMarker([c.lat, c.lon], {radius: 9, weight: 2, fillOpacity: .9}).addTo(map),
          ring: L.circle([c.lat, c.lon], {weight: 1, fillOpacity: .1}).addTo(map)};
      }
      l.dot.setLatLng([c.lat, c.lon]).setStyle({color: "#fff", fillColor: color});
      l.ring.setLatLng([c.lat, c.lon]).setRadius(c.accuracy_m || 50).setStyle({color: color});
      l.dot.bindPopup(details(c));
      l.dot.bindTooltip(el("span", c.name + (c.ssid ? " · " + c.ssid : "")));
    }
    if (c.id === selected) selectedName = c.name;

    const li = el("li"); if (c.id === selected) li.className = "sel";
    const dot = el("span", undefined, "dot"); dot.style.background = color;
    li.append(dot, el("b", c.name),
      el("small", c.has_position
        ? (c.ssid || "Wi-Fi inconnu") + " · " + (c.place || "lieu inconnu")
        : "Aucune position reçue"),
      el("small", agentLine(c) + (c.has_position ? " · dernier envoi il y a " + fmtAge(c.age_s) : "")));
    const acts = el("div", undefined, "acts");
    acts.append(
      c.desired === "running"
        ? actionButton("⏹ Arrêter", "Arrêter le suivi à distance", () => command(c, "stop"))
        : actionButton("▶ Démarrer", "Démarrer le suivi à distance", () => command(c, "start")),
      actionButton("🔑 Jeton", "Générer un nouveau jeton", () => regenToken(c)),
      actionButton("🗑", "Supprimer ce client", () => removeClient(c)));
    li.appendChild(acts);
    li.onclick = () => select(c);
    ul.appendChild(li);
  }
  const located = list.filter(c => c.has_position);
  if (firstFit && located.length) {
    map.fitBounds(located.map(c => [c.lat, c.lon]), {maxZoom: 15, padding: [60, 60]});
    firstFit = false;
  }
  if (selected !== null) drawTrajectory(false, false);
}
$("period").onchange = e => { hours = Number(e.target.value); drawTrajectory(true, true); };
$("trailToggle").onchange = e => { showTrail = e.target.checked; drawTrajectory(true, false); };
$("pointsToggle").onchange = e => { showPoints = e.target.checked; drawTrajectory(true, false); };
$("approxToggle").onchange = e => { withApprox = e.target.checked; drawTrajectory(true, true); };
$("trajclose").onclick = () => { selected = null; lastSig = ""; drawTrajectory(true, false); refresh(); };
refresh();
setInterval(refresh, 5000);
</script></body></html>
"""


def js(value):
    """Valeur Python -> littéral JS sûr à insérer dans une balise <script>."""
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


@app.get("/login")
def login_page():
    if current_user():
        return redirect("/")
    return Response(LOGIN_PAGE.replace("__SIGNUP__", "true" if ALLOW_SIGNUP else "false"),
                    mimetype="text/html")


@app.get("/")
def dashboard():
    user = current_user()
    if user is None:
        return redirect("/login")
    page = (DASHBOARD
            .replace("__CSRF__", js(session["csrf"]))
            .replace("__USER__", js(user["username"]))
            .replace("__TILE_URL__", js(TILE_URL))
            .replace("__TILE_ATTR__", js(TILE_ATTR)))
    return Response(page, mimetype="text/html")


if __name__ == "__main__":
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "5000"))
    if not COOKIE_SECURE:
        print("⚠️  COOKIE_SECURE=1 est obligatoire en ligne (HTTPS) ; OK en test local.")
    print(f"🌐 Tableau de bord : http://{host}:{port}/  (créez votre compte sur la page de connexion)")
    app.run(host=host, port=port, debug=False)
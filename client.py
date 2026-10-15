#!/usr/bin/env python3
"""
client.py - agent Wifi Tracker : tourne sur l'appareil à suivre et obéit au tableau de bord.

1. Connectez-vous au tableau de bord du serveur et créez un client (donnez-lui un nom) :
   le serveur affiche une commande avec un jeton unique.
2. Lancez cette commande UNE fois sur l'appareil (le jeton est mémorisé avec --save) :
       python3 client.py --server https://mon-serveur.com --token MON_JETON --save
3. Ensuite, le client reste en attente. Les boutons ▶ Démarrer / ⏹ Arrêter du tableau de bord
   le pilotent à distance, même si vous vous êtes déconnecté de votre compte.

Données envoyées : nom du client, SSID, BSSID, signal, latitude, longitude, précision,
altitude (si disponible), quartier/ville, source de la position.

Position : fournie par le navigateur (le client ouvre une page locale au démarrage), ce qui
est bien plus fiable que l'IP. Sans navigateur, le client se rabat sur les routeurs voisins
(BeaconDB / Mylnikov / WiGLE) puis sur l'IP.

Utilisation :
    pip install requests
    python3 client.py                         # relance avec la configuration mémorisée
    python3 client.py --once                  # un seul envoi puis quitte (test)

Variables d'environnement : WIFI_SERVER, WIFI_TOKEN, WIGLE_AUTH (optionnel)
"""

import argparse
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

USER_AGENT = "wifi-tracker-client/2.0 (script personnel)"
DEBUG = False
IS_WINDOWS = platform.system() == "Windows"
FROZEN = getattr(sys, "frozen", False)   # True dans l'exécutable créé par PyInstaller
APP_NAME = "WifiTracker"
CREATE_NO_WINDOW = 0x08000000            # Windows : lancer une commande sans fenêtre noire


def app_dir():
    """Dossier (par utilisateur) de la configuration, du journal et du verrou."""
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    return os.path.join(os.path.expanduser("~"), ".config", "wifi-tracker")


CONFIG_PATH = os.path.join(app_dir(), "config.json")
LOG_PATH = os.path.join(app_dir(), "client.log")
LOCK_PATH = os.path.join(app_dir(), "client.lock")

# Port fixe par défaut pour la page locale de géolocalisation. Le navigateur mémorise
# l'autorisation de position PAR ORIGINE (schéma + hôte + port) : avec un port qui change
# à chaque lancement (l'ancien défaut, --port 0 = port aléatoire), l'origine change aussi et
# le navigateur redemande l'autorisation à chaque fois. En gardant toujours le même port,
# l'origine (http://127.0.0.1:8973) reste identique et le choix "Autoriser" reste valable.
DEFAULT_LOCAL_PORT = 8973

# Changement de réseau Wi-Fi (lieu A -> lieu B)
FALLBACK_TTL_S = 300        # durée de vie du cache des positions de secours (routeurs voisins / IP)
NET_WAIT_S = 15             # après un changement de réseau, attente max d'une NOUVELLE position navigateur
BAD_BROWSER_ACC_M = 2000    # au-delà, la position du navigateur est jugée trop imprécise (souvent l'IP)


# --------------------------------------------------------------------------
# 1. Détection du Wi-Fi
# --------------------------------------------------------------------------
def run(cmd):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    kwargs, encoding, errors = {}, "utf-8", "ignore"
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW   # sinon une fenêtre noire clignote à chaque mesure
        encoding, errors = "oem", "replace"          # netsh écrit dans la page de code de la console
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding=encoding,
                             errors=errors, env=env, **kwargs).stdout
    except OSError:
        out = ""
    if DEBUG:
        print(f"\n[DEBUG] $ {' '.join(cmd)}\n{out or '(vide / commande introuvable)'}")
    return out


def pct_to_dbm(pct):
    return int(pct) // 2 - 100


def is_local_mac(bssid):
    try:
        return bool(int(bssid.split(":")[0], 16) & 0x02)
    except (ValueError, AttributeError):
        return False


_win_hint_shown = False


def scan_windows():
    global _win_hint_shown
    out = run(["netsh", "wlan", "show", "interfaces"])
    info = {}
    for key in ("SSID", "BSSID", "Signal"):
        m = re.search(rf"^\s*(?:AP\s+)?{key}\s*:\s*(.+)$", out, re.M)   # « AP BSSID » sur Windows 11 récent
        if m:
            info[key] = m.group(1).strip()
    if "BSSID" not in info and not _win_hint_shown and re.search(
            r"locali[sz]ation|location|emplacement", out, re.I):
        _win_hint_shown = True
        print("⚠️  Windows bloque la lecture du Wi-Fi sans l'autorisation de localisation : activez "
              "Paramètres > Confidentialité et sécurité > Localisation (et « Autoriser les "
              "applications de bureau »).")
    connected = None
    if "BSSID" in info:
        connected = {"ssid": info.get("SSID", ""), "bssid": info["BSSID"].lower(),
                     "signal_dbm": pct_to_dbm(info.get("Signal", "0%").strip("%"))}
    neighbors, cur = [], None
    for line in run(["netsh", "wlan", "show", "networks", "mode=bssid"]).splitlines():
        m = re.match(r"\s*BSSID\s+\d+\s*:\s*(\S+)", line)
        if m:
            cur = m.group(1).lower()
            continue
        m = re.match(r"\s*Signal\s*:\s*(\d+)%", line)
        if m and cur:
            neighbors.append({"bssid": cur, "signal_dbm": pct_to_dbm(m.group(1))})
            cur = None
    return connected, neighbors


def scan_linux_nmcli():
    out = run(["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,SIGNAL",
               "dev", "wifi", "list", "--rescan", "auto"])
    connected, neighbors = None, []
    for line in out.splitlines():
        parts = [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line)]
        if len(parts) < 4 or not parts[3].isdigit():
            continue
        in_use, ssid, bssid, sig = parts[:4]
        entry = {"ssid": ssid, "bssid": bssid.lower(), "signal_dbm": pct_to_dbm(sig)}
        if in_use.strip() == "*":
            connected = entry
        else:
            neighbors.append(entry)
    return connected, neighbors


def scan_linux_iw():
    ifaces = re.findall(r"Interface\s+(\S+)", run(["iw", "dev"]))
    connected, neighbors = None, []
    for iface in ifaces:
        link = run(["iw", "dev", iface, "link"])
        m = re.search(r"Connected to\s+([0-9a-fA-F:]{17})", link)
        if not m:
            continue
        ssid = re.search(r"SSID:\s*(.*)", link)
        sig = re.search(r"signal:\s*(-?\d+)", link)
        connected = {"ssid": ssid.group(1).strip() if ssid else "",
                     "bssid": m.group(1).lower(),
                     "signal_dbm": int(sig.group(1)) if sig else -70}
        for block in run(["iw", "dev", iface, "scan", "dump"]).split("\nBSS ")[1:]:
            mb = re.match(r"([0-9a-fA-F:]{17})", block)
            ms = re.search(r"signal:\s*(-?\d+)", block)
            if mb and ms and mb.group(1).lower() != connected["bssid"]:
                neighbors.append({"bssid": mb.group(1).lower(), "signal_dbm": int(ms.group(1))})
        break
    return connected, neighbors


def scan_linux():
    connected, neighbors = scan_linux_nmcli()
    if not connected:
        connected, neighbors = scan_linux_iw()
    return connected, neighbors


def scan_macos():
    out = run(["system_profiler", "SPAirPortDataType"])
    m = re.search(r"Current Network Information:\s*\n\s*(.+):", out)
    ssid = m.group(1).strip() if m else ""
    m = re.search(r"Signal / Noise:\s*(-?\d+)\s*dBm", out)
    return ({"ssid": ssid, "bssid": None, "signal_dbm": int(m.group(1)) if m else -70}
            if ssid else None), []


def get_wifi_info():
    return {"Windows": scan_windows, "Linux": scan_linux, "Darwin": scan_macos}.get(
        platform.system(), lambda: (None, []))()


# --------------------------------------------------------------------------
# 2. Positions de secours (routeurs voisins, IP) + nom du quartier
# --------------------------------------------------------------------------
def dist_m(a, b):
    dlat = (a[0] - b[0]) * 111_320
    dlon = (a[1] - b[1]) * 111_320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def beacondb_lookup(aps):
    if len(aps) < 2:
        return None
    body = {"considerIp": False,
            "wifiAccessPoints": [{"macAddress": a["bssid"], "signalStrength": a["signal_dbm"]}
                                 for a in aps]}
    r = requests.post("https://api.beacondb.net/v1/geolocate", json=body, timeout=10,
                      headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        return None
    d = r.json()
    return d["location"]["lat"], d["location"]["lng"], d.get("accuracy") or 300


def mylnikov_lookup(bssid):
    r = requests.get("https://api.mylnikov.org/geolocation/wifi",
                     params={"v": "1.1", "data": "open", "bssid": bssid.upper()}, timeout=10)
    if r.status_code != 200:
        return None
    d = r.json().get("data") or {}
    return (float(d["lat"]), float(d["lon"])) if "lat" in d and "lon" in d else None


def wigle_lookup(bssid):
    token = os.getenv("WIGLE_AUTH")
    if not token:
        return None
    r = requests.get("https://api.wigle.net/api/v2/network/search",
                     params={"netid": bssid.upper()},
                     headers={"Authorization": f"Basic {token}"}, timeout=15)
    if r.status_code != 200:
        return None
    res = r.json().get("results") or []
    return (res[0]["trilat"], res[0]["trilong"]) if res else None


def locate_from_wifi(connected, neighbors):
    cands = ([connected] if connected else []) + sorted(neighbors, key=lambda n: -n["signal_dbm"])
    cands = [c for c in cands if c.get("bssid") and not is_local_mac(c["bssid"])][:10]
    if not cands:
        return None
    try:
        bp = beacondb_lookup(cands)
        if bp:
            return {"lat": bp[0], "lon": bp[1], "accuracy_m": bp[2], "approx": False,
                    "method": f"Wi-Fi voisins via BeaconDB ({len(cands)} routeurs)"}
    except (requests.RequestException, KeyError, ValueError):
        pass
    points = []
    for c in cands:
        for fn in (wigle_lookup, mylnikov_lookup):
            try:
                pt = fn(c["bssid"])
            except (requests.RequestException, KeyError, ValueError):
                pt = None
            if pt:
                points.append(pt)
                break
    if not points:
        return None
    med = (statistics.median(p[0] for p in points), statistics.median(p[1] for p in points))
    good = [p for p in points if dist_m(p, med) < 5000] or points
    center = (statistics.median(p[0] for p in good), statistics.median(p[1] for p in good))
    return {"lat": center[0], "lon": center[1],
            "accuracy_m": max(150, max(dist_m(p, center) for p in good)), "approx": False,
            "method": f"Wi-Fi voisins ({len(good)}/{len(cands)} routeurs trouvés)"}


def locate_with_ip():
    r = requests.get("https://ipwho.is/", timeout=10)
    r.raise_for_status()
    d = r.json()
    if not d.get("success", True):
        return None
    return {"lat": d["latitude"], "lon": d["longitude"], "accuracy_m": 5000, "approx": True,
            "method": f"IP publique ({d.get('city', '?')}) - souvent fausse avec Starlink"}


def locate_fallback(connected, neighbors):
    try:
        pos = locate_from_wifi(connected, neighbors)
        if pos:
            return pos
    except requests.RequestException:
        pass
    try:
        return locate_with_ip()
    except requests.RequestException:
        return None


_geo_cache = {}


def reverse_geocode(lat, lon):
    """Quartier / ville via OpenStreetMap Nominatim (mis en cache)."""
    key = (round(lat, 3), round(lon, 3))
    if key in _geo_cache:
        return _geo_cache[key]
    try:
        r = requests.get("https://nominatim.openstreetmap.org/reverse",
                         params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16,
                                 "addressdetails": 1, "accept-language": "fr"},
                         headers={"User-Agent": USER_AGENT}, timeout=10)
        j = r.json()
        a = j.get("address", {})
        area = next((a[k] for k in ("neighbourhood", "quarter", "suburb", "city_district",
                                    "hamlet") if k in a), None)
        city = next((a[k] for k in ("city", "town", "village", "municipality", "county")
                     if k in a), None)
        place = ", ".join(x for x in (area, city) if x) or j.get("display_name", "")
    except (requests.RequestException, ValueError):
        return ""
    _geo_cache[key] = place
    return place


# --------------------------------------------------------------------------
# 3. Petit serveur local : reçoit la position du navigateur
# --------------------------------------------------------------------------
LOCAL_PAGE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Client Wifi Tracker</title>
<style>
html, body { margin: 0; height: 100%; overflow: hidden; background: #02030a; }
#bg { position: fixed; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; }
/* Message affiché uniquement en cas de problème de localisation (refus, indisponible...). */
#note { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); max-width: 90%;
        padding: 10px 16px; border-radius: 8px; background: rgba(0, 0, 0, .65); color: #fff;
        font: 14px system-ui, sans-serif; text-align: center; }
#note[hidden] { display: none; }
</style>
</head><body>
<video id="bg" src="/background.mp4" autoplay muted loop playsinline preload="auto"></video>
<div id="note" hidden></div>
<script>
const note = document.getElementById("note");
function warn(msg) { note.textContent = msg || ""; note.hidden = !msg; }
const bg = document.getElementById("bg");
bg.play().catch(() => {});
// gen : numéro de « génération » donné par le client. Il augmente à chaque changement de réseau
// Wi-Fi : la page refait alors tout de suite une mesure, et le client ignore toute position
// demandée avant le changement (donc celle de l'ancien lieu).
let lastGen = null, lastFix = 0, busy = false;
async function tick() {
  let st;
  try { st = await (await fetch("/active")).json(); }
  catch (e) { warn(""); return; }                    // client arrêté : la page n'a plus rien à faire
  if (!st.active) { warn(""); lastGen = null; return; }
  if (!navigator.geolocation) { warn("⚠️ Géolocalisation indisponible dans ce navigateur."); return; }
  if (busy) return;
  const gen = st.gen;
  if (gen === lastGen && Date.now() - lastFix < 15000) return;
  busy = true;
  navigator.geolocation.getCurrentPosition(pos => {
    const c = pos.coords;
    const body = {lat: c.latitude, lon: c.longitude, acc: c.accuracy,
                  alt: c.altitude, altAcc: c.altitudeAccuracy, gen: gen};
    fetch("/position", {method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body)})
      .then(r => r.json())
      .then(j => { lastGen = gen; lastFix = Date.now(); warn(""); })
      .catch(() => { lastFix = Date.now() - 10000; })
      .finally(() => { busy = false; });
  }, err => {
    warn("⚠️ " + err.message);
    lastGen = gen; lastFix = Date.now() - 10000;   // nouvel essai dans ~5 s
    busy = false;
  },
  {enableHighAccuracy: false, timeout: 30000, maximumAge: 0});
}
tick();
setInterval(tick, 3000);
</script></body></html>
"""

VIDEO_NAME = "background.mp4"


def asset_path(name):
    """Fichier du dossier assets/ : à côté de client.py, ou embarqué dans l'exécutable (PyInstaller)."""
    base = getattr(sys, "_MEIPASS", "") if FROZEN else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "assets", name)


browser_state = {"pos": None, "page_seen": 0.0, "gen": 0}
state_lock = threading.Lock()
tracking = threading.Event()   # activé quand le tableau de bord demande « Démarrer »


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_video(self):
        path = asset_path(VIDEO_NAME)
        try:
            size = os.path.getsize(path)
        except OSError:
            self._send(404, "text/plain", b"")      # pas de vidéo : la page reste sur fond sombre
            return
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:                                    # « bytes=-N » : les N derniers octets
                start = max(size - int(m.group(2)), 0)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except OSError:                              # le navigateur a fermé la connexion (lecture en boucle, onglet fermé...)
            pass

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/" + VIDEO_NAME:
            self._send_video()
        elif self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", LOCAL_PAGE.encode("utf-8"))
        elif self.path == "/active":
            with state_lock:
                browser_state["page_seen"] = time.time()
                gen = browser_state["gen"]
            self._send(200, "application/json",
                       json.dumps({"active": tracking.is_set(), "gen": gen}).encode())
        else:
            self._send(404, "text/plain", b"")

    def do_POST(self):
        origin = self.headers.get("Origin")
        own = f"http://127.0.0.1:{self.server.server_port}"
        if self.path != "/position" or (origin and origin != own) or not tracking.is_set():
            self._send(403, "text/plain", b"")
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n))
            lat, lon = float(d["lat"]), float(d["lon"])
            acc = float(d.get("acc") or 0)
            alt = d.get("alt")
            alt_acc = d.get("altAcc")
            alt = float(alt) if alt is not None else None
            alt_acc = float(alt_acc) if alt_acc is not None else None
            gen = int(d.get("gen", -1))
        except (ValueError, KeyError, TypeError):
            self._send(400, "text/plain", b"")
            return
        with state_lock:
            if gen != browser_state["gen"]:
                # Mesure demandée avant un changement de réseau : elle décrit l'ancien lieu.
                stale = True
            else:
                stale = False
                browser_state["pos"] = {"lat": lat, "lon": lon, "accuracy_m": acc,
                                        "altitude": alt, "altitude_accuracy": alt_acc,
                                        "t": time.time(), "gen": gen}
        self._send(200, "application/json", json.dumps({"stale": stale}).encode())


def start_local_server(port):
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), LocalHandler)
    except OSError as e:
        if port != 0:
            # Port fixe indisponible (ex. une autre instance tourne déjà, ou le port est pris
            # par autre chose) : on bascule sur un port aléatoire plutôt que de planter, mais
            # cela signifie que le navigateur redemandera l'autorisation de position cette fois.
            print(f"⚠️  Port {port} indisponible ({e}) : bascule sur un port aléatoire "
                  f"(le navigateur redemandera l'autorisation de position).")
            srv = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        else:
            raise
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def fresh_browser_pos(max_age):
    """Dernière position du navigateur, si elle est récente ET postérieure au dernier
    changement de réseau Wi-Fi (même numéro de génération)."""
    with state_lock:
        p = browser_state["pos"]
        ok = p and p.get("gen") == browser_state["gen"] and time.time() - p["t"] <= max_age
        return dict(p) if ok else None


# --------------------------------------------------------------------------
# Détection d'un changement de réseau Wi-Fi
# --------------------------------------------------------------------------
net_state = {"id": None}


def network_id(connected):
    if not connected:
        return None
    return ((connected.get("ssid") or "").strip(), (connected.get("bssid") or "").lower())


def note_network(connected, fallback_cache):
    """Compare le Wi-Fi actuel au précédent. Si l'appareil a changé de réseau, tout ce qui décrivait
    l'ancien lieu est jeté : cache des positions de secours et position du navigateur.
    Retourne True en cas de changement. Pas de Wi-Fi (coupure entre deux réseaux) : rien ne change."""
    nid = network_id(connected)
    if nid is None or nid == net_state["id"]:
        return False
    previous, net_state["id"] = net_state["id"], nid
    if previous is None:
        return False
    fallback_cache["pos"], fallback_cache["t"] = None, 0
    with state_lock:
        browser_state["pos"] = None
        browser_state["gen"] += 1
    print(f"🔀 Changement de réseau : « {previous[0]} » -> « {nid[0]} » : "
          "nouvelle position demandée.")
    return True


# --------------------------------------------------------------------------
# 4. Configuration mémorisée et dialogue avec le serveur
# --------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(server, token, no_open=False):
    os.makedirs(os.path.dirname(CONFIG_PATH), mode=0o700, exist_ok=True)
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"server": server, "token": token, "no_open": bool(no_open)}, f)
    os.chmod(CONFIG_PATH, 0o600)   # si le fichier existait déjà avec d'autres droits


def api_headers(token):
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def fetch_command(server, token, timeout=15):
    return requests.get(server + "/api/agent/poll", headers=api_headers(token), timeout=timeout)


def send_report(server, token, payload):
    return requests.post(server + "/api/agent/report", json=payload,
                         headers=api_headers(token), timeout=15)


def build_payload(args, connected, neighbors, pos):
    position = {k: pos.get(k) for k in ("lat", "lon", "accuracy_m", "altitude",
                                        "altitude_accuracy", "method", "approx", "place")}
    payload = {
        "client_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wifi": ({k: connected.get(k) for k in ("ssid", "bssid", "signal_dbm")}
                 if connected else None),
        "neighbors": len(neighbors),
        "position": position,
    }
    if args.send_neighbors:
        payload["neighbor_list"] = neighbors[:50]
    return payload


def collect_once(interval, fallback_cache):
    connected, neighbors = get_wifi_info()
    if note_network(connected, fallback_cache):
        # Nouveau réseau : on laisse à la page du navigateur le temps de refaire une mesure
        # (sinon on enverrait l'ancien lieu, ou une position IP souvent fausse avec Starlink).
        with state_lock:
            page_alive = time.time() - browser_state["page_seen"] < 40
        if page_alive:
            end = time.time() + NET_WAIT_S
            while time.time() < end and not fresh_browser_pos(60):
                time.sleep(0.5)
    pos = fresh_browser_pos(max_age=max(120, interval * 4))
    if pos:
        pos["method"], pos["approx"] = "Navigateur (Wi-Fi / GPS)", False
        if (pos.get("accuracy_m") or 0) > BAD_BROWSER_ACC_M:
            # Précision de plusieurs km : le navigateur a en fait localisé l'IP. On essaie les
            # routeurs voisins, sinon on garde la position mais en la marquant approximative.
            try:
                better = locate_from_wifi(connected, neighbors)
            except requests.RequestException:
                better = None
            if better and better["accuracy_m"] < pos["accuracy_m"]:
                pos = better
            else:
                pos["approx"], pos["method"] = True, "Navigateur - position imprécise"
    else:
        c = fallback_cache
        if not c["pos"] or time.time() - c["t"] > FALLBACK_TTL_S:
            c["pos"], c["t"] = locate_fallback(connected, neighbors), time.time()
        pos = dict(c["pos"]) if c["pos"] else None
    if not pos:
        return connected, neighbors, None
    pos["place"] = reverse_geocode(pos["lat"], pos["lon"])
    return connected, neighbors, pos


def report_cycle(args, fallback_cache, interval):
    """Une mesure + un envoi. Retourne l'état demandé par le serveur ('running'/'stopped') ou None."""
    connected, neighbors, pos = collect_once(interval, fallback_cache)
    if connected:
        print(f"📶 {connected['ssid']} | {connected.get('bssid')} | "
              f"{connected['signal_dbm']} dBm | {len(neighbors)} voisin(s)")
    else:
        print("⚠️  Aucun Wi-Fi connecté détecté (--debug pour diagnostiquer).")
    if not pos:
        print("❌ Aucune position disponible, nouvel essai au prochain cycle.")
        return None
    try:
        r = send_report(args.server, args.token, build_payload(args, connected, neighbors, pos))
    except requests.RequestException as e:
        print(f"❌ Serveur injoignable : {e}")
        return None
    if r.status_code in (401, 403):
        fatal(TOKEN_REFUSED)
    if r.status_code != 200:
        print(f"❌ Serveur : HTTP {r.status_code} {r.text[:120]}")
        return None
    j = r.json()
    if j.get("stored"):
        alt = f"{pos['altitude']:.0f} m" if pos.get("altitude") is not None else "n/a"
        print(f"✅ Envoyé : {pos['lat']:.5f}, {pos['lon']:.5f} "
              f"(±{(pos.get('accuracy_m') or 0):.0f} m, alt {alt}) "
              f"- {pos.get('place') or '?'} [{pos['method']}]")
    return j.get("desired")


def start_tracking(args, local_url):
    tracking.set()
    with state_lock:
        page_alive = time.time() - browser_state["page_seen"] < 40
    opened = False
    if not args.no_open and not page_alive:
        try:
            opened = bool(webbrowser.open(local_url))
        except Exception:
            opened = False
    print(f"🌐 Page de position (laissez-la ouverte) : {local_url}")
    if opened or page_alive:
        print(f"⏳ Attente de la position du navigateur (max {args.wait}s)…")
        end = time.time() + args.wait
        while time.time() < end and not fresh_browser_pos(60):
            time.sleep(0.5)
    if not fresh_browser_pos(60):
        print("⚠️  Pas de position navigateur : utilisation des routeurs voisins / IP "
              "(ouvrez la page ci-dessus pour une position précise).")


def stop_tracking():
    tracking.clear()
    with state_lock:
        browser_state["pos"] = None


# --------------------------------------------------------------------------
# 5. Installation : journal, configuration guidée, démarrage automatique, instance unique
# --------------------------------------------------------------------------
TOKEN_REFUSED = ("Jeton refusé (client supprimé ou jeton régénéré). Recréez-en un dans le "
                 "tableau de bord, puis lancez « WifiTracker --setup ».")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class LogFile:
    """Fichier journal : horodate chaque ligne et reste sous ~1 Mo (l'ancien devient .old)."""
    MAX_BYTES = 1_000_000

    def __init__(self, path):
        self.path = path
        self._buf, self._lines, self._lock = "", 0, threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._open()

    def _open(self):
        self.f = open(self.path, "a", encoding="utf-8", errors="replace", buffering=1)

    def _rotate_if_needed(self):
        try:
            if os.path.getsize(self.path) <= self.MAX_BYTES:
                return
            self.f.close()
            os.replace(self.path, self.path + ".old")
        except OSError:
            pass
        finally:
            if self.f.closed:
                self._open()

    def write(self, text):
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if not line.strip():
                    continue
                self.f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
                self._lines += 1
                if self._lines % 100 == 0:
                    self._rotate_if_needed()
        return len(text)

    def flush(self):
        try:
            self.f.flush()
        except (OSError, ValueError):
            pass

    def isatty(self):
        return False


def setup_stdio(background):
    """Sortie en UTF-8 (emojis) ; dans le journal si pas de console (exécutable sans fenêtre) ou --background."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    if background or sys.stdout is None or sys.stderr is None:
        try:
            sys.stdout = sys.stderr = LogFile(LOG_PATH)
        except OSError:
            sys.stdout = sys.stderr = open(os.devnull, "w")


def has_console():
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def notify(message, error=False, gui=True):
    """Affiche un message : console si elle existe, sinon boîte de dialogue (version sans fenêtre)."""
    print(("❌ " if error else "ℹ️  ") + message)
    if has_console() or not gui:
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)("Wifi Tracker", message, parent=root)
        root.destroy()
    except Exception:   # pas d'écran (service Linux...) : le message est déjà dans le journal
        pass


def fatal(message):
    notify(message, error=True, gui="--silent" not in sys.argv and
           ("--background" not in sys.argv or "refusé" in message))
    sys.exit(1)


def normalize_server(value):
    value = (value or "").strip().rstrip("/")
    if value and "://" not in value:
        host = urlparse("//" + value).hostname or ""
        value = ("http://" if host in ("localhost", "::1") or host.startswith("127.") else "https://") + value
    return value


def verify_credentials(server, token):
    """Teste le serveur et le jeton. Retourne (ok, nom du client ou message d'erreur)."""
    try:
        r = fetch_command(server, token, timeout=60)   # un serveur gratuit peut mettre du temps à se réveiller
    except requests.RequestException as e:
        return False, f"Serveur injoignable : {e}"
    if r.status_code in (401, 403):
        return False, "Jeton refusé : recopiez-le depuis le tableau de bord (ou créez-en un nouveau)."
    if r.status_code != 200:
        return False, f"Réponse inattendue du serveur (HTTP {r.status_code})."
    try:
        return True, str(r.json().get("name") or "")
    except (ValueError, AttributeError):
        return False, "Cette adresse ne ressemble pas à un serveur Wifi Tracker."


ENROLL_CODE_RE = re.compile(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}")   # sans I, O, 0, 1


def looks_like_code(value):
    return bool(ENROLL_CODE_RE.fullmatch((value or "").strip().upper()))


def enroll(server, code):
    """Échange un code d'activation (usage unique, durée limitée) contre le jeton du client.
    Retourne (statut, jeton ou message, nom) ; statut : ok / invalid / network / error."""
    try:
        r = requests.post(server + "/api/agent/enroll", json={"code": code.strip().upper()},
                          headers={"User-Agent": USER_AGENT}, timeout=60)   # 60 s : serveur gratuit à réveiller
    except requests.RequestException as e:
        return "network", f"Serveur injoignable : {e}", ""
    if r.status_code == 429:
        return "error", "Trop de tentatives : réessayez dans quelques minutes.", ""
    if r.status_code in (400, 404):
        return "invalid", ("Code d'activation invalide, expiré ou déjà utilisé : générez-en un nouveau "
                           "dans le tableau de bord (bouton 💻 Windows)."), ""
    if r.status_code != 200:
        return "error", f"Réponse inattendue du serveur (HTTP {r.status_code}).", ""
    try:
        d = r.json()
        return "ok", d["token"], str(d.get("name") or "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return "error", "Cette adresse ne ressemble pas à un serveur Wifi Tracker.", ""


def resolve_credentials(server, secret):
    """Accepte un jeton OU un code d'activation. Retourne (ok, jeton, nom du client ou message d'erreur)."""
    secret = secret.strip()
    if looks_like_code(secret):
        status, value, name = enroll(server, secret)
        return (True, value, name) if status == "ok" else (False, "", value)
    ok, msg = verify_credentials(server, secret)
    return ok, secret, msg


def gui_setup(default_server, default_token, default_open, offer_autostart):
    """Fenêtre de configuration (tkinter, fourni avec Python et PyInstaller)."""
    import tkinter as tk
    from tkinter import ttk

    result = {}
    root = tk.Tk()
    root.title("Wifi Tracker - Configuration")
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=16)
    frm.grid()
    server, token = tk.StringVar(value=default_server), tk.StringVar(value=default_token)
    autostart, open_browser, status = tk.BooleanVar(value=True), tk.BooleanVar(value=default_open), tk.StringVar()

    ttk.Label(frm, text="Adresse du serveur (ex. https://mon-serveur.onrender.com)").grid(row=0, sticky="w")
    server_entry = ttk.Entry(frm, textvariable=server, width=54)
    server_entry.grid(row=1, pady=(2, 10))
    ttk.Label(frm, text="Jeton ou code d'activation du client (tableau de bord)").grid(row=2, sticky="w")
    ttk.Entry(frm, textvariable=token, width=54, show="•").grid(row=3, pady=(2, 10))
    row = 4
    if offer_autostart:
        ttk.Checkbutton(frm, text="Démarrer automatiquement avec Windows", variable=autostart).grid(row=row, sticky="w")
        row += 1
    ttk.Checkbutton(frm, text="Ouvrir la page de position dans le navigateur (position plus précise)",
                    variable=open_browser).grid(row=row, sticky="w")
    row += 1
    ttk.Label(frm, textvariable=status, foreground="#c0392b", wraplength=430).grid(row=row, sticky="w", pady=(8, 0))
    row += 1
    buttons = ttk.Frame(frm)
    buttons.grid(row=row, sticky="e", pady=(10, 0))

    def on_ok(_event=None):
        srv, tok = normalize_server(server.get()), token.get().strip()
        if not srv:
            status.set("Indiquez l'adresse du serveur.")
            return
        if len(tok) < 14:
            status.set("Le jeton ou le code d'activation semble incomplet.")
            return
        status.set("Vérification de la connexion (peut durer un moment si le serveur se réveille)…")
        ok_btn.state(["disabled"])
        root.update()
        ok, tok, msg = resolve_credentials(srv, tok)
        if not ok:
            status.set(msg)
            ok_btn.state(["!disabled"])
            return
        result.update(server=srv, token=tok, autostart=autostart.get(),
                      no_open=not open_browser.get(), name=msg)
        root.destroy()

    ok_btn = ttk.Button(buttons, text="Enregistrer", command=on_ok)
    ok_btn.grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Annuler", command=root.destroy).grid(row=0, column=1)
    root.bind("<Return>", on_ok)
    root.attributes("-topmost", True)                       # passe devant les autres fenêtres
    root.after(400, lambda: root.attributes("-topmost", False))
    server_entry.focus_set()
    root.mainloop()
    return result or None


def console_setup(default_server, default_token, default_open, offer_autostart):
    def ask(prompt, default=""):
        return input(prompt + (f" [{default}]" if default else "") + " : ").strip() or default

    def yes(prompt, default=True):
        answer = input(f"{prompt} [{'O/n' if default else 'o/N'}] : ").strip().lower()
        return default if not answer else answer in ("o", "oui", "y", "yes")

    while True:
        server = normalize_server(ask("Adresse du serveur", default_server))
        token = ask("Jeton ou code d'activation du client", default_token)
        print("⏳ Vérification de la connexion…")
        ok, token, msg = resolve_credentials(server, token)
        if ok:
            break
        print(f"❌ {msg}")
        if not yes("Réessayer ?"):
            return None
    return {"server": server, "token": token, "name": msg,
            "autostart": offer_autostart and yes("Démarrer automatiquement avec Windows ?"),
            "no_open": not yes("Ouvrir la page de position dans le navigateur (plus précis) ?", default_open)}


def autostart_command():
    if FROZEN:
        return f'"{sys.executable}" --background'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # pythonw = Python sans fenêtre
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" "{os.path.abspath(__file__)}" --background'


def install_autostart():
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())


def uninstall_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def run_setup(cfg):
    """Configuration guidée (console si disponible, sinon fenêtre). Retourne les choix, ou None."""
    defaults = dict(default_server=cfg.get("server", ""), default_token=cfg.get("token", ""),
                    default_open=not cfg.get("no_open"), offer_autostart=IS_WINDOWS)
    try:
        res = console_setup(**defaults) if has_console() else gui_setup(**defaults)
    except (EOFError, KeyboardInterrupt):
        return None
    except Exception as e:   # tkinter absent, pas d'écran...
        print(f"❌ Configuration impossible : {e}")
        return None
    if not res:
        return None
    save_config(res["server"], res["token"], res["no_open"])
    if IS_WINDOWS:
        (install_autostart if res["autostart"] else uninstall_autostart)()
    return res


_instance_handle = None   # garde le verrou tant que le programme tourne


def acquire_single_instance():
    """Empêche deux clients simultanés (deux envois avec le même jeton). False si déjà lancé."""
    global _instance_handle
    try:
        if IS_WINDOWS:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p
            handle = k32.CreateMutexW(None, False, "Local\\WifiTrackerClient")
            if ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS
                return False
            _instance_handle = handle
        else:
            import fcntl
            os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
            fd = open(LOCK_PATH, "w")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fd.close()
                return False
            _instance_handle = fd
    except Exception:   # verrou impossible : on ne bloque pas le client pour autant
        pass
    return True


def stop_other_instances():
    """Windows (version .exe) : ferme les autres WifiTracker.exe. On épargne ce processus et son parent
    (en mode --onefile, PyInstaller lance le programme via un processus de démarrage de même nom)."""
    if not (IS_WINDOWS and FROZEN):
        return
    try:
        subprocess.run(["taskkill", "/F", "/IM", os.path.basename(sys.executable),
                        "/FI", f"PID ne {os.getpid()}", "/FI", f"PID ne {os.getppid()}"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    except OSError:
        pass


def uninstall(gui=True):
    stop_other_instances()
    if IS_WINDOWS:
        uninstall_autostart()
    try:
        os.remove(CONFIG_PATH)
    except FileNotFoundError:
        pass
    notify("Configuration et démarrage automatique supprimés. Supprimez maintenant le programme "
           "lui-même, et le client dans le tableau de bord si vous ne l'utilisez plus.", gui=gui)


def enroll_command(args):
    """Appelée par l'installateur : échange le code, enregistre la configuration, puis quitte.
    Codes de sortie : 0 ok, 2 code invalide/expiré, 3 serveur injoignable, 4 autre erreur."""
    server = normalize_server(args.server)
    status, value, name = enroll(server, args.enroll)
    if status != "ok":
        notify(value, error=True, gui=not args.silent)
        return {"invalid": 2, "network": 3}.get(status, 4)
    save_config(server, value, args.no_open)
    if IS_WINDOWS:
        (install_autostart if args.autostart else uninstall_autostart)()
    notify(f"Client « {name} » enregistré pour {server}.", gui=not args.silent)
    return 0


def main():
    global DEBUG
    background = "--background" in sys.argv
    setup_stdio(background)    # avant argparse : sans console, ses messages d'erreur iraient dans le vide
    cfg = load_config()
    p = argparse.ArgumentParser(description="Client Wifi Tracker (piloté depuis le tableau de bord)")
    p.add_argument("--server", default=os.getenv("WIFI_SERVER") or cfg.get("server")
                   or "http://127.0.0.1:5000", help="URL du serveur (ou WIFI_SERVER)")
    p.add_argument("--token", default=os.getenv("WIFI_TOKEN") or cfg.get("token"),
                   help="Jeton du client, affiché à sa création (ou WIFI_TOKEN)")
    p.add_argument("--save", action="store_true",
                   help=f"Mémoriser --server et --token ({CONFIG_PATH})")
    p.add_argument("--setup", action="store_true",
                   help="Configuration guidée (serveur, jeton, démarrage automatique)")
    p.add_argument("--uninstall", action="store_true",
                   help="Supprimer la configuration et le démarrage automatique")
    p.add_argument("--enroll", metavar="CODE",
                   help="Échanger un code d'activation contre le jeton, enregistrer, puis quitter (installateur)")
    p.add_argument("--autostart", action="store_true",
                   help="Avec --enroll : démarrer automatiquement avec Windows")
    p.add_argument("--silent", action="store_true", help="Aucune boîte de dialogue (installateur)")
    p.add_argument("--background", action="store_true",
                   help=f"Mode silencieux : sortie dans le journal ({LOG_PATH})")
    p.add_argument("--interval", type=int, default=0,
                   help="Secondes entre deux envois (0 = valeur donnée par le serveur)")
    p.add_argument("--wait", type=int, default=25, help="Attente de la 1re position navigateur (s)")
    p.add_argument("--once", action="store_true", help="Un seul envoi puis quitter (test)")
    p.add_argument("--port", type=int, default=DEFAULT_LOCAL_PORT,
                   help=f"Port de la page locale (0 = aléatoire à chaque lancement : le "
                        f"navigateur redemandera alors l'autorisation de position à chaque "
                        f"fois ; défaut {DEFAULT_LOCAL_PORT}, fixe, pour que l'autorisation "
                        f"reste mémorisée)")
    p.add_argument("--no-open", action="store_true", default=bool(cfg.get("no_open")),
                   help="Ne pas ouvrir le navigateur")
    p.add_argument("--send-neighbors", action="store_true",
                   help="Envoyer aussi la liste des BSSID voisins (désactivé par défaut)")
    p.add_argument("--debug", action="store_true", help="Sortie brute des commandes")
    args = p.parse_args()
    DEBUG = args.debug

    if args.uninstall:
        uninstall(gui=not args.silent)
        return
    if args.enroll:
        args.no_open = "--no-open" in sys.argv    # l'installateur décide : on ignore l'ancienne configuration
        sys.exit(enroll_command(args))

    if args.setup or not args.token:
        if background and not args.setup:   # démarrage automatique sans configuration : ne pas déranger
            fatal("Aucune configuration : lancez « WifiTracker --setup ».")
        res = run_setup(cfg)
        if not res:
            fatal("Configuration annulée ou impossible : lancez « WifiTracker --setup » (ou "
                  "--server ... --token ... --save).")
        args.server, args.token, args.no_open = res["server"], res["token"], res["no_open"]
        if args.setup:
            stop_other_instances()   # l'ancien client tournerait avec l'ancienne configuration
        notify("Configuration enregistrée" + (f" pour le client « {res['name']} »" if res.get("name") else "")
               + ". Le client tourne en arrière-plan"
               + (" et démarrera avec Windows." if IS_WINDOWS and res["autostart"] else "."))

    args.server = normalize_server(args.server)
    host = urlparse(args.server).hostname or ""
    local = host in ("127.0.0.1", "localhost", "::1")
    if not local and not args.server.lower().startswith("https://"):
        print("⚠️  Serveur distant en HTTP : utilisez https:// pour protéger le jeton et les positions.")
    if args.save:
        save_config(args.server, args.token, args.no_open)
        print("💾 Configuration enregistrée : lancez désormais simplement « python3 client.py ».")

    if not args.once:
        for _ in range(6 if args.setup else 1):   # après --setup, laisser le temps à l'ancien client de se fermer
            if acquire_single_instance():
                break
            time.sleep(0.5)
        else:
            notify("Wifi Tracker est déjà en cours d'exécution. Modifier la configuration : "
                   "WifiTracker --setup. Désinstaller : WifiTracker --uninstall.", gui=not background)
            return

    srv = start_local_server(args.port)
    local_url = f"http://127.0.0.1:{srv.server_port}/"
    fallback_cache = {"pos": None, "t": 0}

    try:
        if args.once:
            start_tracking(args, local_url)
            report_cycle(args, fallback_cache, args.interval or 30)
            return

        print(f"📡 Client connecté à {args.server} : en attente des ordres du tableau de bord "
              "(Ctrl+C pour quitter).")
        running, standby_said, online, last_err, next_report = False, False, False, None, 0.0
        while True:
            poll_s, interval = 10, args.interval or 30
            try:
                r = fetch_command(args.server, args.token)
                err = None if r.status_code == 200 else f"HTTP {r.status_code}"
            except requests.RequestException as e:
                r, err = None, f"serveur injoignable ({e})"
            if r is not None and r.status_code in (401, 403):
                fatal(TOKEN_REFUSED)
            if err:
                if err != last_err:
                    print(f"❌ {err}, nouvel essai dans {poll_s} s")
                last_err, online = err, False
                time.sleep(poll_s)
                continue
            last_err = None
            cmd = r.json()
            if not online:
                print(f"🔌 Connecté au serveur en tant que « {cmd.get('name', '?')} »")
                online = True
            poll_s = max(3, int(cmd.get("poll") or poll_s))
            interval = args.interval or int(cmd.get("interval") or 30)
            desired = cmd.get("desired")

            if desired == "running" and not running:
                print("▶️  Démarrage demandé depuis le tableau de bord.")
                start_tracking(args, local_url)
                running, standby_said, next_report = True, False, 0.0
            if desired == "running" and time.time() >= next_report:
                if report_cycle(args, fallback_cache, interval) == "stopped":
                    desired = "stopped"
                next_report = time.time() + interval
            if desired != "running":
                if running:
                    print("⏹️  Arrêt demandé depuis le tableau de bord.")
                    stop_tracking()
                    running, standby_said = False, False
                if not standby_said:
                    print("⏸  En attente : cliquez sur ▶ Démarrer dans le tableau de bord.")
                    standby_said = True
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("\n👋 Arrêt du client.")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()

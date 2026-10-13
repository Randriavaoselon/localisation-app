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
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "wifi-tracker", "config.json")

# Port fixe par défaut pour la page locale de géolocalisation. Le navigateur mémorise
# l'autorisation de position PAR ORIGINE (schéma + hôte + port) : avec un port qui change
# à chaque lancement (l'ancien défaut, --port 0 = port aléatoire), l'origine change aussi et
# le navigateur redemande l'autorisation à chaque fois. En gardant toujours le même port,
# l'origine (http://127.0.0.1:8973) reste identique et le choix "Autoriser" reste valable.
DEFAULT_LOCAL_PORT = 8973


# --------------------------------------------------------------------------
# 1. Détection du Wi-Fi
# --------------------------------------------------------------------------
def run(cmd):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="ignore", env=env).stdout
    except FileNotFoundError:
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


def scan_windows():
    out = run(["netsh", "wlan", "show", "interfaces"])
    info = {}
    for key in ("SSID", "BSSID", "Signal"):
        m = re.search(rf"^\s*{key}\s*:\s*(.+)$", out, re.M)
        if m:
            info[key] = m.group(1).strip()
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
<html lang="fr"><head><meta charset="utf-8"><title>Client Wifi Tracker</title>
<style>body{font-family:system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px}
#s{font-size:18px;margin:16px 0}pre{background:#f4f4f4;padding:10px;border-radius:6px}</style>
</head><body>
<h2>📡 Client Wifi Tracker</h2>
<p>Gardez cet onglet <b>ouvert</b> : il fournit la position de ce navigateur au client,
qui l'envoie ensuite au serveur. Autorisez la localisation si le navigateur le demande.
Quand vous arrêtez le suivi depuis le tableau de bord, cet onglet cesse de localiser l'appareil.</p>
<div id="s">⏳ Connexion au client…</div><pre id="d"></pre>
<script>
const s = document.getElementById("s"), d = document.getElementById("d");
async function poll() {
  let active = false;
  try { active = (await (await fetch("/active")).json()).active; }
  catch (e) { s.textContent = "⏹ Client arrêté : vous pouvez fermer cet onglet."; d.textContent = ""; return; }
  if (!active) {
    s.textContent = "⏸ Suivi arrêté depuis le tableau de bord. Cet onglet reprendra au prochain démarrage.";
    d.textContent = ""; return;
  }
  if (!navigator.geolocation) { s.textContent = "⚠️ Géolocalisation indisponible."; return; }
  navigator.geolocation.getCurrentPosition(pos => {
    const c = pos.coords;
    const body = {lat: c.latitude, lon: c.longitude, acc: c.accuracy,
                  alt: c.altitude, altAcc: c.altitudeAccuracy};
    fetch("/position", {method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body)})
      .then(() => {
        s.textContent = "✅ Position transmise au client (" + new Date().toLocaleTimeString() + ")";
        d.textContent = JSON.stringify(body, null, 2);
      });
  }, err => { s.textContent = "⚠️ " + err.message; },
  {enableHighAccuracy: false, timeout: 30000, maximumAge: 0});
}
poll();
setInterval(poll, 15000);
</script></body></html>
"""

browser_state = {"pos": None, "page_seen": 0.0}
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

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", LOCAL_PAGE.encode("utf-8"))
        elif self.path == "/active":
            with state_lock:
                browser_state["page_seen"] = time.time()
            self._send(200, "application/json", json.dumps({"active": tracking.is_set()}).encode())
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
        except (ValueError, KeyError, TypeError):
            self._send(400, "text/plain", b"")
            return
        with state_lock:
            browser_state["pos"] = {"lat": lat, "lon": lon, "accuracy_m": acc,
                                    "altitude": alt, "altitude_accuracy": alt_acc,
                                    "t": time.time()}
        self._send(200, "application/json", b"{}")


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
    with state_lock:
        p = browser_state["pos"]
        return dict(p) if p and time.time() - p["t"] <= max_age else None


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


def save_config(server, token):
    os.makedirs(os.path.dirname(CONFIG_PATH), mode=0o700, exist_ok=True)
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"server": server, "token": token}, f)
    os.chmod(CONFIG_PATH, 0o600)   # si le fichier existait déjà avec d'autres droits


def api_headers(token):
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


def fetch_command(server, token):
    return requests.get(server + "/api/agent/poll", headers=api_headers(token), timeout=15)


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
    pos = fresh_browser_pos(max_age=max(120, interval * 4))
    if pos:
        pos["method"], pos["approx"] = "Navigateur (Wi-Fi / GPS)", False
    else:
        c = fallback_cache
        if not c["pos"] or time.time() - c["t"] > 600:
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
        sys.exit("❌ Jeton refusé (client supprimé ou jeton régénéré). Recréez la commande "
                 "depuis le tableau de bord.")
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


def main():
    global DEBUG
    cfg = load_config()
    p = argparse.ArgumentParser(description="Client Wifi Tracker (piloté depuis le tableau de bord)")
    p.add_argument("--server", default=os.getenv("WIFI_SERVER") or cfg.get("server")
                   or "http://127.0.0.1:5000", help="URL du serveur (ou WIFI_SERVER)")
    p.add_argument("--token", default=os.getenv("WIFI_TOKEN") or cfg.get("token"),
                   help="Jeton du client, affiché à sa création (ou WIFI_TOKEN)")
    p.add_argument("--save", action="store_true",
                   help="Mémoriser --server et --token (~/.config/wifi-tracker/config.json)")
    p.add_argument("--interval", type=int, default=0,
                   help="Secondes entre deux envois (0 = valeur donnée par le serveur)")
    p.add_argument("--wait", type=int, default=25, help="Attente de la 1re position navigateur (s)")
    p.add_argument("--once", action="store_true", help="Un seul envoi puis quitter (test)")
    p.add_argument("--port", type=int, default=DEFAULT_LOCAL_PORT,
                   help=f"Port de la page locale (0 = aléatoire à chaque lancement : le "
                        f"navigateur redemandera alors l'autorisation de position à chaque "
                        f"fois ; défaut {DEFAULT_LOCAL_PORT}, fixe, pour que l'autorisation "
                        f"reste mémorisée)")
    p.add_argument("--no-open", action="store_true", help="Ne pas ouvrir le navigateur")
    p.add_argument("--send-neighbors", action="store_true",
                   help="Envoyer aussi la liste des BSSID voisins (désactivé par défaut)")
    p.add_argument("--debug", action="store_true", help="Sortie brute des commandes")
    args = p.parse_args()
    DEBUG = args.debug
    args.server = args.server.rstrip("/")

    if not args.token:
        sys.exit("❌ Jeton requis : créez un client dans le tableau de bord, puis lancez la "
                 "commande affichée (--server ... --token ... --save).")
    host = urlparse(args.server).hostname or ""
    local = host in ("127.0.0.1", "localhost", "::1")
    if not local and not args.server.lower().startswith("https://"):
        print("⚠️  Serveur distant en HTTP : utilisez https:// pour protéger le jeton et les positions.")
    if args.save:
        save_config(args.server, args.token)
        print("💾 Configuration enregistrée : lancez désormais simplement « python3 client.py ».")

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
                sys.exit("❌ Jeton refusé (client supprimé ou jeton régénéré). Recréez la commande "
                         "depuis le tableau de bord.")
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
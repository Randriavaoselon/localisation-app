# Wifi Tracker (comptes + clients pilotés à distance)

Chaque utilisateur crée un **compte** sur le serveur, puis des **clients** (un par appareil à suivre).
Un client garde la **même identité** (son jeton) où qu'il aille : lieu A, lieu B, autre réseau Wi-Fi...
Le tableau de bord montre sa position actuelle et tout son **historique avec marqueurs**.
Un utilisateur ne voit jamais les clients ni les positions d'un autre utilisateur.

## Test en local

```bash
pip install -r requirements.txt
python server.py                    # http://127.0.0.1:5000
```

1. Ouvrez http://127.0.0.1:5000, onglet **Créer un compte**, puis connectez-vous.
2. Cliquez **➕ Nouveau client**, donnez un nom (ex. `PC-Majunga`) : le serveur affiche une commande avec un
   jeton unique, **visible une seule fois**.
3. Sur l'appareil à suivre, lancez cette commande une seule fois :
   ```bash
   python3 client.py --server http://127.0.0.1:5000 --token LE_JETON --save
   ```
   `--save` mémorise la configuration (`~/.config/wifi-tracker/config.json`, droits 600) : ensuite,
   `python3 client.py` suffit.
4. Le client se met en attente, puis obéit aux boutons **▶ Démarrer / ⏹ Arrêter** du tableau de bord.
   Vous pouvez vous **déconnecter** du compte : le client continue d'envoyer ses positions.

## Suivre un client d'un endroit à l'autre

Cliquez sur un client dans la liste : le panneau **Trajectoire** s'ouvre.

- 🟢 **D** = première connexion, 🔴 **F** = dernière connexion, 🔵 **2, 3…** = étapes intermédiaires
  (lieu A → lieu B → ...), reliées par une ligne avec flèches.
- Un clic sur un marqueur (ou sur une étape de la liste) affiche : lieu, Wi-Fi utilisés, heure d'arrivée,
  dernière mesure, durée sur place, altitude.
- **Afficher toutes les mesures** : ajoute un petit point pour chaque position reçue (info-bulle : heure + Wi-Fi).
- **Inclure les positions approximatives (IP)** : par défaut elles sont ignorées ; cochez pour les voir
  (en orange) si le client n'a pas de position navigateur à un endroit.
- Le sélecteur de période limite l'historique (1 h, 24 h, 7 jours, tout).
- Réglage : `STOP_RADIUS_M` (150 m par défaut) = distance en dessous de laquelle deux positions comptent
  comme le même lieu.

Pour une position précise, laissez ouverte la page locale que le client ouvre au démarrage (le navigateur
fournit la position). Sans elle, le client utilise les routeurs voisins, puis l'IP.

## Lancer le client automatiquement au démarrage (Linux)

Un client doit tourner sur l'appareil : le serveur ne peut pas lancer un programme qui n'y est pas installé.
Pour ne plus jamais le lancer à la main, après `--save`, créez `~/.config/systemd/user/wifi-tracker.service` :

```ini
[Unit]
Description=Wifi Tracker client
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /chemin/vers/client.py --no-open
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now wifi-tracker
```

Sans navigateur ouvert (`--no-open`), la position vient des routeurs voisins puis de l'IP (moins précise).
Pour une position navigateur, lancez le client normalement dans votre session graphique.

### Éviter que le navigateur redemande l'autorisation à chaque lancement

Depuis la v2, la page locale utilise toujours le même port (`8973` par défaut, modifiable avec
`--port`), donc toujours la même adresse `http://127.0.0.1:8973/`. Les navigateurs mémorisent
l'autorisation de géolocalisation **par adresse exacte** (y compris le port) : avec une adresse
stable, cliquez une fois sur **Autoriser**, et le choix reste valable pour les lancements suivants
(jusqu'à ce que vous l'effaciez dans les paramètres du site du navigateur, ou que vous videz les
cookies/données de site).

Si l'autorisation est quand même redemandée :
- Vérifiez que vous relancez bien sans changer `--port`.
- Si un autre programme occupe déjà le port 8973, le client bascule automatiquement sur un port
  aléatoire (message affiché dans la console) : dans ce cas, l'adresse change et l'autorisation
  sera redemandée. Changez `--port` pour un autre port fixe libre si ça arrive souvent.
- Le mode navigation privée/invité ne mémorise jamais les autorisations entre les fermetures du
  navigateur, par conception.

## Déploiement en ligne

1. **Variables d'environnement** sur le serveur :
   ```bash
   export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
   export COOKIE_SECURE=1          # cookie de session réservé à HTTPS (obligatoire en ligne)
   export TRUST_PROXY=1            # derrière Render / Caddy / nginx : IP réelle pour la limitation de tentatives
   export DB_PATH="/data/wifi_reports.db"   # dossier persistant
   # export ALLOW_SIGNUP=0         # pour fermer les inscriptions une fois vos comptes créés
   ```
2. **Lancer** : `gunicorn -w 1 -b 0.0.0.0:8000 server:app` (gardez `-w 1` : la limitation de tentatives est en mémoire).
3. **HTTPS obligatoire** (Render, Railway, Fly.io, VPS + Caddy...) : sans lui, mots de passe, jetons et
   positions circulent en clair.
4. **Disque persistant** : SQLite est un fichier. Sur une plateforme à disque éphémère (Render gratuit),
   montez un volume ou passez à PostgreSQL, sinon comptes et historique disparaissent.
5. **Client** : `python3 client.py --server https://votre-domaine.com --token LE_JETON --save`

## Migration depuis l'ancienne version

L'ancienne clé unique `WIFI_API_KEY` et le mot de passe `DASHBOARD_*` n'existent plus : chaque client a son
jeton. Les anciennes positions (sans compte) restent dans la base mais ne sont plus affichées.

## Sécurité et vie privée

- Mots de passe stockés hachés (scrypt/PBKDF2 de Werkzeug) ; jetons de clients stockés hachés (SHA-256).
- Sessions : cookie HttpOnly, SameSite=Lax, jeton CSRF sur toutes les écritures ; au-delà de 8 connexions
  ratées en 5 minutes (par utilisateur ou par IP), les tentatives sont bloquées temporairement.
- Les positions sont des données personnelles : ne suivez que vos propres appareils, ou avec l'accord de
  leur propriétaire. Le client s'exécute visiblement (console, page de localisation du navigateur).
- Un jeton fuité ? **🔑 Jeton** génère un nouveau jeton et invalide l'ancien. **🗑** supprime un client et
  son historique.
- La liste des BSSID voisins n'est envoyée que si vous passez `--send-neighbors`, et n'est jamais affichée.
- L'historique est purgé après `RETENTION_DAYS` jours (30 par défaut).

## Fond de carte (tuiles)

Par défaut : tuiles OpenStreetMap, usage léger uniquement ([politique](https://operations.osmfoundation.org/policies/tiles/)).
Si la carte affiche « Access blocked », videz le cache (Ctrl+Maj+R). Pour un site plus fréquenté :

```bash
export TILE_URL="https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key=VOTRE_CLE"
export TILE_ATTR="&copy; MapTiler &copy; contributeurs OpenStreetMap"
```

## À savoir

- **Altitude** : seulement si l'appareil a un GPS (téléphone). Sur un PC : « non disponible ».
- L'API de trajectoire : `GET /api/trajectory/<id_client>?hours=24&points=1&approx=1` (session requise).
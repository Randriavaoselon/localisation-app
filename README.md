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

## Lancer le client automatiquement au démarrage (Linux / Kali)

Un client doit tourner sur l'appareil : le serveur ne peut pas lancer un programme qui n'y est pas installé.
Pour ne plus jamais le lancer à la main, il y a deux options (**choisissez-en une seule**, sinon deux clients
enverraient les positions avec le même jeton).

**Préparation (une seule fois)**, dans le dossier qui contient `client.py` :

```bash
python3 -c "import requests" || pip install requests --break-system-packages
python3 client.py --server https://votre-domaine.com --token LE_JETON --save    # puis Ctrl+C
```

### Option A : au démarrage de l'ordinateur (sans navigateur)

Le client démarre dès le boot, même sans que personne ouvre de session. Pas de navigateur : la position vient
des routeurs voisins puis de l'IP (moins précise).

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/wifi-tracker.service << EOF
[Unit]
Description=Wifi Tracker client

[Service]
ExecStart=/usr/bin/python3 -u "$(readlink -f client.py)" --no-open
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now wifi-tracker     # active et démarre maintenant
sudo loginctl enable-linger "$USER"            # démarrage au boot, sans ouvrir de session
```

Les guillemets autour du chemin sont indispensables si un dossier contient un espace (ex. `Projet Wifi Tracker`) : sans eux, systemd coupe le chemin à l'espace et le service échoue.

Vérifier : `systemctl --user status wifi-tracker` et `journalctl --user -u wifi-tracker -f` (journal en direct).
Redémarrez l'ordinateur : le client doit apparaître « connecté » dans le tableau de bord.

### Option B : à l'ouverture de session graphique (avec navigateur, position précise)

Au démarrage, le client attend vos ordres ; quand vous cliquez **▶ Démarrer**, il ouvre la page de position dans
le navigateur. Le port de cette page est fixe (8973 par défaut) : le navigateur retient ainsi l'autorisation de localisation.

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/wifi-tracker.desktop << EOF
[Desktop Entry]
Type=Application
Name=Wifi Tracker
Exec=/usr/bin/python3 "$(readlink -f client.py)"
Terminal=false
EOF
```

Le client démarre à la prochaine ouverture de session. Pour l'arrêter : supprimez `~/.config/autostart/wifi-tracker.desktop`.

### Commandes utiles (option A)

| Action | Commande |
|---|---|
| Redémarrer (après un nouveau jeton + `--save`) | `systemctl --user restart wifi-tracker` |
| Arrêter | `systemctl --user stop wifi-tracker` |
| Ne plus démarrer au boot | `systemctl --user disable --now wifi-tracker` |
| Voir les messages du client | `journalctl --user -u wifi-tracker -f` |

Si le client affiche « Jeton refusé », le client a été supprimé ou son jeton régénéré : relancez la commande
`--save` avec le nouveau jeton, puis redémarrez le service.

## Installer le client sur Windows (assistant Setup.exe)

L'assistant d'installation (`WifiTracker-Setup.exe`) n'est **téléchargeable que par un utilisateur connecté** au
tableau de bord : le serveur refuse le téléchargement (et renvoie vers la page de connexion) à toute personne
qui n'a pas ouvert de session.

### Pour l'utilisateur

1. Connectez-vous au tableau de bord, créez un client (**➕ Nouveau client**) ou prenez-en un existant.
2. Cliquez **💻 Windows** sur ce client : une fenêtre propose **⬇ Télécharger WifiTracker-Setup.exe** et affiche un
   **code d'activation** (`XXXX-XXXX-XXXX`, valable 30 minutes, utilisable une seule fois).
3. Lancez le fichier téléchargé sur le PC à suivre et suivez l'assistant. Le fichier téléchargé porte le code dans son
   nom, l'assistant le **pré-remplit** (sinon, saisissez le code affiché dans le tableau de bord).
4. Terminé : le client démarre tout de suite, sans fenêtre, et redémarre à chaque ouverture de session Windows
   (case « Démarrer automatiquement avec Windows »). Il obéit aux boutons **▶ Démarrer / ⏹ Arrêter** du tableau de
   bord, même si vous vous déconnectez du compte.

L'installation se fait pour l'utilisateur courant (pas besoin d'être administrateur). Wifi Tracker apparaît dans
*Paramètres > Applications* (désinstallation propre : le client est fermé, le démarrage automatique et la
configuration sont supprimés) et dans le menu Démarrer (*Configurer*, *Journal*, *Diagnostic*).

Un code ne sert qu'une fois, et **l'enregistrement remplace le jeton précédent du client** : un client = un appareil.
Pour suivre un deuxième PC, créez un deuxième client.

**Avertissement SmartScreen** (« Windows a protégé votre PC ») : l'installateur n'est pas signé numériquement. Cliquez
sur **Informations complémentaires > Exécuter quand même**. Un antivirus peut aussi le signaler, comme d'autres
programmes créés avec PyInstaller : ajoutez une exception si besoin.

**Autorisation de localisation (Windows 11)** : pour lire le nom et l'identifiant du Wi-Fi, Windows exige que la
localisation soit active : *Paramètres > Confidentialité et sécurité > Localisation*, activez « Services de
localisation » et « Autoriser les applications de bureau à accéder à votre position ». Sans cela, le client le
signale dans le journal et se rabat sur l'adresse IP.

**Position précise** : à chaque démarrage du suivi, le client ouvre la page de position dans votre navigateur par défaut
(l'autorisation de localisation est retenue, le port étant fixe). Décochez « Ouvrir la page de position… » à
l'installation pour ne jamais ouvrir le navigateur ; la position vient alors des routeurs voisins, puis de l'IP.

| Action | Comment |
|---|---|
| Changer serveur, jeton ou code | menu Démarrer > *Configurer Wifi Tracker* (accepte un jeton **ou** un code d'activation) |
| Voir ce que fait le client | menu Démarrer > *Journal de Wifi Tracker* (`%APPDATA%\WifiTracker\client.log`) |
| Diagnostiquer en direct | menu Démarrer > *Diagnostic (console)* |
| Arrêter tout de suite | **⏹ Arrêter** dans le tableau de bord, ou Gestionnaire des tâches > `WifiTracker.exe` |
| Désinstaller | *Paramètres > Applications > Wifi Tracker*, puis **🗑** sur le client dans le tableau de bord |

### Pour vous : fabriquer et publier l'assistant

L'installateur est un programme Windows : il ne peut être fabriqué que sur Windows. Deux façons de procéder.

#### Option 1 (recommandée, sans PC Windows) : GitHub Actions

GitHub fabrique l'installateur pour vous, sur un Windows qu'il fournit, puis l'ajoute à votre dépôt.

1. Ajoutez à votre dépôt (en gardant ces chemins) :
   ```
   .github/workflows/build-windows-installer.yml
   assets/background.mp4
   build_windows.bat
   build_installer.bat
   installer.iss
   client.py
   ```
2. Sur GitHub : onglet **Actions** > **Fabriquer l'installateur Windows** > **Run workflow**, saisissez l'adresse de
   votre serveur (ex. `https://mon-serveur.onrender.com`), laissez « Ajouter l'installateur au dépôt » coché, puis
   **Run workflow**.
3. Attendez 3 à 6 minutes (pastille verte). L'installateur est ajouté au dépôt dans `installers/WifiTracker-Setup.exe`
   (Render redéploie alors le serveur si le déploiement automatique est activé) et reste téléchargeable depuis la page
   de l'exécution (section **Artifacts**).
4. À refaire chaque fois que `client.py`, `installer.iss` ou la vidéo changent.

Si le dépôt refuse l'ajout du fichier : *Settings > Actions > General > Workflow permissions* > « Read and write
permissions ». Chaque fabrication ajoute environ 15 Mo à l'historique du dépôt.

#### Option 2 : sur un PC Windows

1. Installez **Python 3.10+** ([python.org](https://www.python.org/downloads/), en gardant « tcl/tk and IDLE » coché
   **et en cochant « Add python.exe to PATH »** sur le premier écran) et **Inno Setup 6**
   ([jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php), gratuit).
2. Mettez dans un même dossier : `client.py`, `build_windows.bat`, `build_installer.bat`, `installer.iss` et le dossier `assets/`.
3. Ouvrez une invite de commandes dans ce dossier (Explorateur : barre d'adresse, tapez `cmd`, Entrée) et lancez :
   ```bat
   build_installer.bat https://mon-serveur.onrender.com
   ```
   Le script fabrique `dist\WifiTracker.exe` et `dist\WifiTracker-console.exe` (PyInstaller), puis
   **`installers\WifiTracker-Setup.exe`** (Inno Setup). L'adresse de votre serveur est pré-remplie dans l'assistant.
4. Publiez l'installateur **avec le serveur** : le dossier `installers/` doit se trouver à côté de `server.py` sur
   l'hébergeur (ajoutez-le au dépôt Git déployé, par exemple avec **Add file > Upload files** sur GitHub).

Tant que le fichier est absent, le bouton **💻 Windows** affiche seulement le code d'activation. Un autre emplacement
est possible avec la variable `INSTALLER_PATH`.

`build_windows.bat` seul fabrique uniquement les deux `.exe` (dossier `dist\`), sans assistant. Sans exécutable, vous
pouvez aussi, sous Windows : `pip install requests`, puis `python client.py --setup` (le démarrage automatique
utilisera alors `pythonw.exe`, sans fenêtre).

Un seul client tourne à la fois par session : le lancer une deuxième fois affiche « déjà en cours d'exécution ».

### La page du navigateur (position précise)

Quand le suivi démarre, le client ouvre une page locale dans le navigateur : c'est elle qui fournit la position.
Elle est volontairement **sans texte** : une vidéo de fond plein écran (`assets/background.mp4`, muette, en boucle).
Le navigateur demande l'autorisation de localisation la première fois ; gardez l'onglet ouvert. Un message n'apparaît
qu'en cas de problème (localisation refusée ou indisponible).

Pour changer la vidéo, remplacez `assets/background.mp4` (format MP4 / H.264, de préférence sans piste audio et de
quelques Mo, car elle est embarquée dans le programme), puis refabriquez l'installateur. Sans ce fichier, la page reste
simplement sur fond sombre. Pour la retirer ou modifier l'affichage : constante `LOCAL_PAGE` de `client.py`.

## Déploiement en ligne

> ⚠️ **Ne publiez jamais `.secret_key` (ni `wifi_reports.db`) sur GitHub** : ajoutez-les à `.gitignore`. Quiconque connaît la clé peut fabriquer un cookie de session valide. Sur un hébergeur, définissez `SECRET_KEY` en variable d'environnement.

1. **Variables d'environnement** sur le serveur :
   ```bash
   export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
   export COOKIE_SECURE=1          # cookie de session réservé à HTTPS (obligatoire en ligne)
   export TRUST_PROXY=1            # derrière Render / Caddy / nginx : IP réelle pour la limitation de tentatives
   export DB_PATH="/data/wifi_reports.db"   # dossier persistant
   # export ENROLL_MINUTES=30     # durée de validité d'un code d'activation Windows
   # export INSTALLER_PATH=...      # emplacement de WifiTracker-Setup.exe (défaut : installers/ à côté de server.py)
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
- L'installateur Windows n'est servi qu'aux utilisateurs connectés. Les codes d'activation sont à usage unique, valables 30 minutes, stockés hachés, et leur utilisation est limitée (10 essais / 10 min par IP).
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
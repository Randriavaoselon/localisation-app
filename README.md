# Suricate-tracker (comptes + clients pilotés à distance)

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
   `--save` mémorise la configuration (`~/.config/suricate-tracker/config.json`, droits 600) : ensuite,
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

## Position du serveur et trajectoire vers le client

À l'ouverture du tableau de bord, le navigateur demande l'autorisation de localisation (HTTPS requis, sauf en local).
La position du serveur (celle du navigateur qui affiche le tableau de bord) apparaît sur la carte (marqueur violet **Moi** + cercle de précision) et se met à jour si vous vous déplacez.
Cliquez sur un client : une trajectoire violette, fléchée dans le sens serveur → client, relie les deux positions, avec la **distance en ligne droite** et le **cap**
(boussole) affichés sur la ligne et dans le panneau de gauche. Le bouton **↔ Voir les deux** cadre la carte sur les deux points.

- Case **📍 Position du serveur (ce navigateur)** : décochez pour masquer et arrêter la localisation (choix mémorisé dans le navigateur).
- Bouton **🔄 Relocaliser** : relance la recherche de position. En cas de « délai dépassé », la page réessaie seule toutes les 8 s ; quand on décoche puis recoche la case, la dernière position connue est réaffichée tout de suite, le temps de l'actualiser.
- Cette position n'est **jamais envoyée au serveur** : elle reste dans la page.
- Sans autorisation ou sans GPS (PC), la précision peut être faible (position par Wi-Fi ou IP) : le panneau l'indique.
- La trajectoire est une ligne droite, pas un itinéraire routier (un calcul d'itinéraire enverrait les positions à un service externe).

## Affichage sur téléphone et tablette

Le tableau de bord s'adapte à la taille de l'écran. Sur téléphone (largeur ≤ 700 px), le panneau devient une **feuille en bas de l'écran** et la carte occupe le haut : la poignée grise en haut de la feuille alterne entre mi-hauteur, grand format (pour lire la liste des étapes) et réduit (carte presque plein écran) ; le choix est mémorisé. Tout le panneau défile d'un seul bloc, et un clic sur un client amène directement le panneau de trajectoire en haut de la feuille. En paysage (écran peu haut), le panneau reste à gauche et défile aussi d'un bloc. Les boutons d'action d'un client passent à la ligne au lieu de déborder, les zones tactiles sont agrandies, et les fenêtres (installation, jeton…) tiennent toujours dans l'écran. La page de connexion est adaptée de la même façon.

## Client Android (application)

Une vraie application Android (dossier `android/`, fabriquée par GitHub Actions comme l'installateur Windows ; **Android 7.0 ou plus récent**) : elle suit en arrière-plan, écran éteint, et **redémarre toute seule à chaque démarrage du téléphone**. Elle utilise le même protocole que les clients Linux et Windows (`/api/agent/poll` et `/api/agent/report`).

**Fabriquer l'APK (une fois, puis à chaque changement du code Android ou de l'adresse du serveur)**

1. Sur GitHub : onglet **Actions** > **Fabriquer l'application Android (APK)** > **Run workflow**, avec l'adresse de votre serveur (ex. `https://selonmoc.pythonanywhere.com`), « Ajouter l'APK au dépôt » coché. Compter 4 à 8 minutes.
2. La première fois, le workflow crée une clé de signature (`android/suricate.keystore`) et la range dans le dépôt : elle sert à pouvoir mettre l'application à jour sans la désinstaller. **Gardez le dépôt privé.**
3. Sur le serveur : `git pull` puis **Reload**. L'APK (`installers/SuricateTracker.apk`) est alors proposé par le tableau de bord.

**Installer sur le téléphone**

1. Sur le tableau de bord, créez un client dédié au téléphone (ex. `Téléphone-Alice`) puis cliquez sur **📱 Android** : un lien de téléchargement et un code d'activation (30 minutes, usage unique) s'affichent.
2. Sur le téléphone, ouvrez le lien dans Chrome, téléchargez l'APK et installez-le (Android demande d'autoriser « Installer des applications inconnues » pour Chrome).
3. Ouvrez **Suricate**, saisissez le code (l'adresse du serveur est déjà remplie) et touchez **Activer**.
4. L'application demande, dans l'ordre : la position précise, les notifications, **« Toujours autoriser »** la position, l'activation du **GPS**, puis l'exemption d'optimisation de batterie. Acceptez tout.
5. Le suivi se démarre et s'arrête avec **▶ Démarrer / ⏹ Arrêter** sur le tableau de bord.

**Ce que fait l'application**

- Android interdit à une application d'allumer le GPS en silence. Suricate affiche donc, à chaque ouverture tant que le GPS est éteint, une fenêtre non fermable qui mène directement aux réglages de localisation ; et la notification permanente indique « GPS désactivé : touchez pour l'activer ». Une fois le GPS allumé, il n'y a plus rien à refaire.
- Au démarrage du téléphone (et après une mise à jour de l'application), un service de premier plan démarre seul, sans ouvrir l'écran : une notification « Suivi actif » apparaît. Il faut que l'application ait été ouverte au moins une fois depuis l'installation, et que « Toujours autoriser » ait été accordé.
- Certains téléphones (Xiaomi, Tecno, Infinix, Huawei, Oppo, Samsung en mode économie…) coupent les applications en arrière-plan malgré tout : ouvrez les réglages de l'application et activez « Démarrage automatique » / « Aucune restriction » pour la batterie.
- Si Android affiche « L'application n'a pas été installée », vérifiez d'abord la version d'Android (7.0 minimum), puis désinstallez toute ancienne version de Suricate et retéléchargez l'APK depuis le lien du tableau de bord. Si Play Protect bloque l'installation, choisissez « Plus de détails » puis « Installer quand même ».
- Sur Android 7.0 et 7.1.0, la connexion au serveur échoue si le certificat HTTPS n'est pas reconnu par le téléphone : si la page du tableau de bord s'ouvre sans avertissement dans Chrome sur ce téléphone, l'application pourra aussi s'y connecter.
- Les positions contiennent coordonnées, précision, altitude et la méthode (GPS ou réseau). Le nom du Wi-Fi n'est pas transmis. L'adresse du serveur doit être en `https://`.
- Mise à jour : relancez le workflow, téléchargez de nouveau l'APK sur le téléphone et installez-le par-dessus (la clé de signature étant conservée, rien n'est perdu).

**Secours sans installation** : la page `/android/` (PWA) fonctionne dans Chrome, mais seulement écran allumé et page ouverte ; elle ne démarre pas seule et ne peut pas activer le GPS.

## Installer le client sur Linux (commande `suricate-tracker`, utilisable de partout)

Plus besoin d'être dans le dossier du projet : l'installateur copie le client dans `~/.local/share/suricate-tracker/`
(avec son propre environnement Python) et crée la commande `suricate-tracker`. Aucun droit root requis.

### Depuis le tableau de bord (serveur déjà déployé)

La commande est **générée toute prête**, avec l'adresse de votre serveur et le jeton/code déjà remplis :

- à la création d'un client (**➕ Nouveau client**), la fenêtre affiche la commande (avec le jeton) ;
- pour un client existant, bouton **🐧 Linux** : commande avec un **code d'activation** (30 min, usage unique,
  recommandé : le jeton n'apparaît pas dans l'historique du shell).

Collez-la dans un terminal sur l'appareil à suivre :

```bash
curl -fsSL https://votre-domaine.com/linux/install.sh | bash -s -- --server https://votre-domaine.com --code XXXX-XXXX-XXXX --autostart
```

Le script télécharge `client.py` depuis votre serveur : **`install-linux.sh`, `client.py` et `assets/` doivent donc être
déployés à côté de `server.py`** (le serveur les publie sans authentification sous `/linux/` ; ils ne contiennent aucun secret).

### Depuis un dossier local (sans passer par le serveur)

```bash
./install-linux.sh --server https://votre-domaine.com --token LE_JETON --autostart    # ou --code XXXX-XXXX-XXXX
```

### Ensuite, depuis n'importe quel dossier

`suricate-tracker` (lancer), `suricate-tracker --setup` (changer serveur/jeton), `suricate-tracker --once --debug` (test),
`suricate-tracker --remove-all` (tout désinstaller). `--autostart` démarre le client via un service systemd utilisateur
(sinon via l'autostart graphique) ; ajoutez `--no-open` pour ne jamais ouvrir le navigateur. Pour un démarrage dès le
boot : `sudo loginctl enable-linger "$USER"`. Mise à jour : relancez la même commande.

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
cat > ~/.config/systemd/user/suricate-tracker.service << EOF
[Unit]
Description=Suricate-tracker client

[Service]
ExecStart=/usr/bin/python3 -u "$(readlink -f client.py)" --no-open
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now suricate-tracker     # active et démarre maintenant
sudo loginctl enable-linger "$USER"            # démarrage au boot, sans ouvrir de session
```

Les guillemets autour du chemin sont indispensables si un dossier contient un espace (ex. `Projet Suricate-tracker`) : sans eux, systemd coupe le chemin à l'espace et le service échoue.

Vérifier : `systemctl --user status suricate-tracker` et `journalctl --user -u suricate-tracker -f` (journal en direct).
Redémarrez l'ordinateur : le client doit apparaître « connecté » dans le tableau de bord.

### Option B : à l'ouverture de session graphique (avec navigateur, position précise)

Au démarrage, le client attend vos ordres ; quand vous cliquez **▶ Démarrer**, il ouvre la page de position dans
le navigateur. Le port de cette page est fixe (8973 par défaut) : le navigateur retient ainsi l'autorisation de localisation.

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/suricate-tracker.desktop << EOF
[Desktop Entry]
Type=Application
Name=Suricate-tracker
Exec=/usr/bin/python3 "$(readlink -f client.py)"
Terminal=false
EOF
```

Le client démarre à la prochaine ouverture de session. Pour l'arrêter : supprimez `~/.config/autostart/suricate-tracker.desktop`.

### Commandes utiles (option A)

| Action | Commande |
|---|---|
| Redémarrer (après un nouveau jeton + `--save`) | `systemctl --user restart suricate-tracker` |
| Arrêter | `systemctl --user stop suricate-tracker` |
| Ne plus démarrer au boot | `systemctl --user disable --now suricate-tracker` |
| Voir les messages du client | `journalctl --user -u suricate-tracker -f` |

Si le client affiche « Jeton refusé », le client a été supprimé ou son jeton régénéré : relancez la commande
`--save` avec le nouveau jeton, puis redémarrez le service.

## Installer le client sur Windows (assistant Setup.exe)

L'assistant d'installation (`SuricateTracker-Setup.exe`) n'est **téléchargeable que par un utilisateur connecté** au
tableau de bord : le serveur refuse le téléchargement (et renvoie vers la page de connexion) à toute personne
qui n'a pas ouvert de session.

### Pour l'utilisateur

1. Connectez-vous au tableau de bord, créez un client (**➕ Nouveau client**) ou prenez-en un existant.
2. Cliquez **💻 Windows** sur ce client : une fenêtre propose **⬇ Télécharger SuricateTracker-Setup.exe** et affiche un
   **code d'activation** (`XXXX-XXXX-XXXX`, valable 30 minutes, utilisable une seule fois).
3. Lancez le fichier téléchargé sur le PC à suivre et suivez l'assistant. Le fichier téléchargé porte le code dans son
   nom, l'assistant le **pré-remplit** (sinon, saisissez le code affiché dans le tableau de bord).
4. Terminé : le client démarre tout de suite, sans fenêtre, et redémarre à chaque ouverture de session Windows
   (case « Démarrer automatiquement avec Windows »). Il obéit aux boutons **▶ Démarrer / ⏹ Arrêter** du tableau de
   bord, même si vous vous déconnectez du compte.

L'installation se fait pour l'utilisateur courant (pas besoin d'être administrateur). Suricate-tracker apparaît dans
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
| Changer serveur, jeton ou code | menu Démarrer > *Configurer Suricate-tracker* (accepte un jeton **ou** un code d'activation) |
| Voir ce que fait le client | menu Démarrer > *Journal de Suricate-tracker* (`%APPDATA%\SuricateTracker\client.log`) |
| Diagnostiquer en direct | menu Démarrer > *Diagnostic (console)* |
| Arrêter tout de suite | **⏹ Arrêter** dans le tableau de bord, ou Gestionnaire des tâches > `SuricateTracker.exe` |
| Désinstaller | *Paramètres > Applications > Suricate-tracker*, puis **🗑** sur le client dans le tableau de bord |

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
3. Attendez 3 à 6 minutes (pastille verte). L'installateur est ajouté au dépôt dans `installers/SuricateTracker-Setup.exe`
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
   Le script fabrique `dist\SuricateTracker.exe` et `dist\SuricateTracker-console.exe` (PyInstaller), puis
   **`installers\SuricateTracker-Setup.exe`** (Inno Setup). L'adresse de votre serveur est pré-remplie dans l'assistant.
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
   # export INSTALLER_PATH=...      # emplacement de SuricateTracker-Setup.exe (défaut : installers/ à côté de server.py)
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

# Fin
   systemctl --user restart suricate-tracker
   systemctl --user status suricate-tracker
   journalctl --user -u suricate-tracker -f

###############################

   systemctl --user stop suricate-tracker
   suricate-tracker --once --debug
   systemctl --user start suricate-tracker

# Configuration de demarage automatique avec navigateur

systemctl --user disable --now suricate-tracker
rm -f ~/.config/systemd/user/suricate-tracker.service
systemctl --user daemon-reload

mkdir -p ~/.config/autostart
cat > ~/.config/autostart/suricate-tracker.desktop << EOF
[Desktop Entry]
Type=Application
Name=Suricate-tracker
Exec=$HOME/.local/bin/suricate-tracker
Terminal=false
EOF

## Test Rapide:
pkill -f client.py
suricate-tracker &

# Commande de mise à jours apre modification
curl -fsSL http://127.0.0.1:5000/linux/install.sh | bash -s -- --server http://127.0.0.1:5000
pkill -f client.py; suricate-tracker &
# Worker de traitement d'image

Service séparé (cahier 9.2, 13 bis.3). **État : traitement écrit et testé en local (`traiter.py`, `main.py`) ; pas encore en service** : `PROCESSOR=mock` reste actif sur le site de test jusqu'à la recette sur le serveur.

## Déployer (rejouable, tout hébergeur : OVHcloud, Scaleway…)
`./deployer.sh utilisateur@serveur` copie le dossier, crée `.env` depuis `.env.example` s'il manque, puis `docker compose up -d --build`. Relancer = mettre à jour. Aucun secret ni identifiant de base dans ce dossier.

## Traitement (9.2) et tests
- `traiter.py` (pur, sans réseau) : décodage + HEIC, orientation, retrait des métadonnées, netteté, exposition, visages (cascade OpenCV locale, injectable), hors cadre (contours à basse résolution), alignement ORB/RANSAC limité à ± 3° / 5 % / 4 %, recadrage 4:3, variantes 640/1280/1600 (WebP + JPEG) et miniature 480 × 360. **Non fait : classifieur de contenu inapproprié** (aucun modèle local prévu au cahier : voir Q64).
- `main.py` : le worker **vient chercher** ses tâches (`worker-taches`, toutes les 10 s) : aucun port ouvert. Un processus par photo, 30 s maximum, une photo à la fois ; dépôt des variantes par URL signées ; rappel `photo-processed`. Il n'accepte que des adresses https de l'hôte Supabase.
- Tests : `python -m venv v && v/bin/pip install -r requirements.txt pytest && v/bin/pytest worker/tests` (images générées par programme). `opencv-python-headless` est épinglé : la version 5 n'a plus la détection de visages.

## Cloisonnement
- Conteneur sans privilège : utilisateur non root, système de fichiers en lecture seule, aucune capacité Linux, ressources limitées.
- **Sortie réseau coupée** : à poser sur le serveur (pare-feu hôte, hors de ce dépôt) — tout refuser en sortie sauf HTTPS vers l'hôte du projet Supabase (téléchargement de l'URL signée, rappel `photo-processed`) et DNS. Entrée : seulement depuis les fonctions Supabase, via un reverse proxy TLS devant `127.0.0.1:8080`.
- Le worker ne détient ni clé de service ni mot de passe de base : uniquement une URL signée (10 min) et un jeton de tâche (15 min) par photo.

## Sans SSH : mise à jour tirée et signe de vie (Q61)
- `cloud-config.yaml` (3 emplacements `__…__` à remplir avant de coller) crée le serveur : Docker, clone du **dépôt public du worker** (contenu de ce dossier `worker/` à la racine, sans secret), minuteur systemd `cairn-worker` toutes les 5 min.
- Le minuteur lance `mettre-a-jour.sh` : `git pull`, reconstruction si le code a changé, puis POST du signe de vie à la fonction Supabase `worker-vie` (jeton `WORKER_TOKEN`). Lecture : `GET` avec le même jeton → dernière heure et version.
- Le jeton est **inventé par le porteur** (32 caractères au moins), posé comme secret `WORKER_TOKEN` dans Supabase et collé dans le cloud-config ; jamais dans le dépôt.
- Le serveur actuel (squelette) ne reçoit pas cette nouveauté : il faut le recréer avec le nouveau fichier.

## À décider avec le porteur
Choix et achat du VPS (Q32) ; réglage du pare-feu et du reverse proxy une fois le serveur connu (SSH seulement sur demande, décision n° 54).

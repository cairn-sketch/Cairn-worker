#!/usr/bin/env bash
# Déploiement rejouable par SSH, indépendant de l'hébergeur : ./deployer.sh utilisateur@serveur
# Prérequis sur le serveur : docker et docker compose. Relancer le script met simplement à jour.
set -euo pipefail
cible="${1:?usage: ./deployer.sh utilisateur@serveur}"
ici="$(cd "$(dirname "$0")" && pwd)"
ssh "$cible" 'mkdir -p ~/cairn-worker'
rsync -az --delete --exclude '.env' "$ici"/ "$cible":~/cairn-worker/
ssh "$cible" 'cd ~/cairn-worker && { [ -f .env ] || cp .env.example .env; } && docker compose up -d --build && docker compose ps'

#!/usr/bin/env bash
# Exécuté sur le serveur toutes les 5 min par le minuteur systemd `cairn-worker` (Q61) : tire le dépôt public du worker,
# reconstruit seulement si le code a changé, puis envoie le signe de vie à Supabase. Lit /etc/cairn-worker.env (jamais dans le dépôt).
set -uo pipefail
. /etc/cairn-worker.env   # DEPOT_URL, SUPABASE_FUNCTIONS_URL, WORKER_TOKEN
cd /opt/cairn-worker
avant=$(git rev-parse HEAD)
git pull --ff-only -q && [ "$(git rev-parse HEAD)" != "$avant" ] && docker compose up -d --build >/dev/null 2>&1
version=$(git rev-parse --short HEAD)
curl -fsS -m 20 -X POST "$SUPABASE_FUNCTIONS_URL/worker-vie" -H "Authorization: Bearer $WORKER_TOKEN" -H 'Content-Type: application/json' -d "{\"version\":\"$version\"}" >/dev/null

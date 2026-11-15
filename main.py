"""Worker de traitement d'image (cahier 9.2, 13 bis.3).
Le worker VIENT CHERCHER ses tâches (fonction `worker-taches`, toutes les 10 s) : aucun port ouvert. Traitement dans un processus
séparé (30 s max), dépôt des variantes par URL signées, rappel de `photo-processed` avec le jeton de tâche. GET /sante en local.
Aucune clé de service ni identifiant de base. Adresses acceptées : https sur l'hôte Supabase seulement (pas de SSRF)."""
import json
import multiprocessing as mp
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

FONCTIONS = os.environ.get("SUPABASE_FUNCTIONS_URL", "")
HOTE = urlparse(FONCTIONS).hostname
SECRET = os.environ.get("WORKER_TOKEN", "")
TAILLE_MAX = 25 * 1024 * 1024
DELAI_TRAITEMENT = 30
UN_A_LA_FOIS = threading.Lock()


def url_permise(u) -> bool:
    p = urlparse(u) if isinstance(u, str) and len(u) < 2000 else None
    return bool(p and p.scheme == "https" and HOTE and p.hostname == HOTE and not p.username)


def telecharger(u: str) -> bytes:
    with urllib.request.urlopen(u, timeout=20) as r:
        d = r.read(TAILLE_MAX + 1)
    if len(d) > TAILLE_MAX:
        raise ValueError("trop gros")
    return d


def _enfant(conn, original, reference):
    import traiter
    conn.send(traiter.traiter(original, reference))


def traiter_isole(original: bytes, reference) -> dict:
    """Un processus par photo, limité à 30 s ; au-delà, il est tué et la photo part en revue manuelle."""
    pere, fils = mp.get_context("spawn").Pipe(False)
    pr = mp.get_context("spawn").Process(target=_enfant, args=(fils, original, reference))
    pr.start()
    if pere.poll(DELAI_TRAITEMENT):
        res = pere.recv()
        pr.join(5)
        return res
    pr.kill()
    return {"status": "pending", "reasons": ["delai"], "scores": {}, "variants": {}}


def requete(u: str, methode: str, corps: bytes, type_: str, jeton: str | None = None):
    h = {"Content-Type": type_}
    if jeton:
        h["Authorization"] = f"Bearer {jeton}"
    urllib.request.urlopen(urllib.request.Request(u, corps, h, method=methode), timeout=30).read()


def travailler(t: dict, jeton: str):
    with UN_A_LA_FOIS:
        try:
            original = telecharger(t["original_url"])
            reference = telecharger(t["reference_url"]) if t.get("reference_url") else None
            res = traiter_isole(original, reference)
            corps = {"photo_id": t["photo_id"], "status": res["status"], "reasons": res["reasons"], "scores": res["scores"]}
            if res["variants"]:
                for nom, octets in res["variants"].items():
                    requete(t["sortie"][nom]["upload_url"], "PUT", octets, "image/webp" if nom.endswith("webp") else "image/jpeg")
                corps["variants"] = {"normalized_path": t["sortie"]["1600.jpg"]["public_url"], "thumb_path": t["sortie"]["thumb.jpg"]["public_url"],
                                     "width": res["width"], "height": res["height"]}
        except Exception:  # aucune donnée de la photo dans les journaux
            print(json.dumps({"erreur": "traitement"}), flush=True)
            corps = {"photo_id": t["photo_id"], "status": "pending", "reasons": ["erreur_traitement"], "scores": {}}
        try:
            requete(f"{FONCTIONS}/photo-processed", "POST", json.dumps(corps).encode(), "application/json", jeton)
        except Exception:
            print(json.dumps({"erreur": "rappel"}), flush=True)


def tache_valide(t) -> bool:
    if not isinstance(t, dict) or not isinstance(t.get("photo_id"), str) or not url_permise(t.get("original_url")):
        return False
    if t.get("reference_url") is not None and not url_permise(t["reference_url"]):
        return False
    s = t.get("sortie")
    noms = [f"{w}.{e}" for w in (640, 1280, 1600) for e in ("webp", "jpg")] + ["thumb.jpg"]
    return isinstance(s, dict) and all(isinstance(s.get(n), dict) and url_permise(s[n].get("upload_url")) and url_permise(s[n].get("public_url")) for n in noms)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/sante" else 404); self.end_headers()
        if self.path == "/sante":
            self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def boucle():
    while True:
        try:
            req = urllib.request.Request(f"{FONCTIONS}/worker-taches", b"{}", {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    reponse = json.loads(r.read(100_000))
                    if tache_valide(reponse.get("tache")):
                        travailler(reponse["tache"], reponse["jeton"])
                        continue  # enchaîner tant qu'il y a des photos
        except Exception:
            print(json.dumps({"erreur": "interrogation"}), flush=True)
        time.sleep(10)


if __name__ == "__main__":
    if SECRET and HOTE:
        threading.Thread(target=boucle, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1" if os.environ.get("LOCAL") else "0.0.0.0", 8080), H).serve_forever()

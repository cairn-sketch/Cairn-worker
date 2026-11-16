"""Worker de traitement d'image (cahier 9.2, 13 bis.3) et d'assemblage des archives zip (9.8).
Images d'actualités (V1.2 S3) : ré-encodage JPEG sans métadonnées. Épreuve du panneau (V1.2 S4c) : PDF et aperçu PNG (panneau.py).
Le worker VIENT CHERCHER ses tâches (fonction `worker-taches`, toutes les 10 s) : aucun port ouvert. Traitement dans un processus
séparé (30 s max), dépôt des variantes par URL signées, rappel de `photo-processed` avec le jeton de tâche. GET /sante en local.
Aucune clé de service ni identifiant de base. Adresses acceptées : https sur l'hôte Supabase seulement (pas de SSRF)."""
import io
import json
import multiprocessing as mp
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # lancé avec -I : le dossier du script n'est pas dans le chemin (processus enfant compris)
FONCTIONS = os.environ.get("SUPABASE_FUNCTIONS_URL", "")
HOTE = urlparse(FONCTIONS).hostname
SECRET = os.environ.get("WORKER_TOKEN", "")
TAILLE_MAX = 25 * 1024 * 1024
DELAI_TRAITEMENT = 30
ZIP_MAX = 48 * 1024 * 1024  # dépôt limité à 50 Mo par fichier sur l'offre gratuite de Supabase
NOM_ZIP = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
UN_A_LA_FOIS = threading.Lock()


def url_permise(u) -> bool:
    p = urlparse(u) if isinstance(u, str) and len(u) < 2000 else None
    return bool(p and p.scheme == "https" and HOTE and p.hostname == HOTE and not p.username)


def telecharger(u: str, maxi: int = TAILLE_MAX, refuser: bool = True) -> bytes:
    with urllib.request.urlopen(u, timeout=20) as r:
        d = r.read(maxi + 1)
    if refuser and len(d) > maxi:
        raise ValueError("trop gros")
    return d


def _enfant(conn, original, reference):
    try:
        import traiter
        conn.send(traiter.traiter(original, reference))
    except Exception:  # une panne ne doit pas se déguiser en délai dépassé
        conn.send({"status": "pending", "reasons": ["erreur_traitement"], "scores": {}, "variants": {}})


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


def requete(u: str, methode: str, corps: bytes, type_: str, jeton: str | None = None, delai: int = 30):
    h = {"Content-Type": type_}
    if jeton:
        h["Authorization"] = f"Bearer {jeton}"
    urllib.request.urlopen(urllib.request.Request(u, corps, h, method=methode), timeout=delai).read()


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


def zip_valide(z) -> bool:
    if not isinstance(z, dict) or not isinstance(z.get("id"), str) or not url_permise(z.get("upload_url")) or not NOM_ZIP.match(str(z.get("dossier"))):
        return False
    f, t = z.get("fichiers"), z.get("textes")
    if not isinstance(f, list) or not isinstance(t, dict) or not 0 < len(f) <= 5000 or len(t) > 10:
        return False
    return (all(isinstance(x, dict) and NOM_ZIP.match(str(x.get("nom"))) and url_permise(x.get("url")) for x in f)
            and all(NOM_ZIP.match(str(k)) and isinstance(v, str) and len(v) < 2_000_000 for k, v in t.items()))


def assembler_zip(z: dict) -> bytes:
    """Archive en mémoire (images déjà compressées : stockées telles quelles) ; au-delà de ZIP_MAX, on renonce."""
    tampon, total = io.BytesIO(), 0
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED) as a:
        for x in z["fichiers"]:
            d = telecharger(x["url"])
            total += len(d)
            if total > ZIP_MAX:
                raise ValueError("archive trop grosse")
            a.writestr(f'{z["dossier"]}/{x["nom"]}', d)
        for nom, texte in z["textes"].items():
            a.writestr(zipfile.ZipInfo(f'{z["dossier"]}/{nom}', time.localtime()[:6]), texte, zipfile.ZIP_DEFLATED)
    return tampon.getvalue()


def archiver(z: dict):
    with UN_A_LA_FOIS:
        try:
            requete(z["upload_url"], "PUT", assembler_zip(z), "application/zip", None, 300)
            ok = True
        except Exception:
            print(json.dumps({"erreur": "archive"}), flush=True)
            ok = False
        try:
            requete(f"{FONCTIONS}/worker-taches", "POST", json.dumps({"zip_fini": z["id"], "ok": ok}).encode(), "application/json", SECRET)
        except Exception:
            print(json.dumps({"erreur": "rappel"}), flush=True)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/sante" else 404); self.end_headers()
        if self.path == "/sante":
            self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def media_valide(m) -> bool:
    return isinstance(m, dict) and isinstance(m.get("id"), str) and url_permise(m.get("original_url")) and url_permise(m.get("upload_url"))


def _enfant_media(conn, original):
    try:
        import traiter
        conn.send(traiter.reencoder(original))
    except Exception:
        conn.send(None)


def reencoder_media(m: dict):
    """Image d'une actualité : ré-encodée dans un processus isolé (30 s max), déposée par URL signée, compte rendu à `worker-taches`."""
    ok = False
    with UN_A_LA_FOIS:
        try:
            original = telecharger(m["original_url"])
            pere, fils = mp.get_context("spawn").Pipe(False)
            pr = mp.get_context("spawn").Process(target=_enfant_media, args=(fils, original))
            pr.start()
            sortie = pere.recv() if pere.poll(DELAI_TRAITEMENT) else None
            pr.kill() if sortie is None else pr.join(5)
            if sortie:
                requete(m["upload_url"], "PUT", sortie, "image/jpeg")
                ok = True
        except Exception:  # aucune donnée de l'image dans les journaux
            print(json.dumps({"erreur": "media"}), flush=True)
        try:
            requete(f"{FONCTIONS}/worker-taches", "POST", json.dumps({"media_fini": m["id"], "ok": ok}).encode(), "application/json", SECRET)
        except Exception:
            print(json.dumps({"erreur": "rappel_media"}), flush=True)


LOGO_MAX = 5 * 1024 * 1024
TYPES_LOGO = ("png", "jpg", "pdf")
DELAI_PANNEAU = 60


def panneau_valide(p) -> bool:
    """Forme seulement (adresses permises, types fermés) ; le contenu (code, adresses, QR, logos) est contrôlé dans panneau.py, dans un processus isolé."""
    if not isinstance(p, dict) or not isinstance(p.get("id"), str) or isinstance(p.get("gen"), bool) or not isinstance(p.get("gen"), int):
        return False
    if not url_permise(p.get("upload_pdf")) or not url_permise(p.get("upload_png")) or not isinstance(p.get("code"), str):
        return False
    logos = p.get("logos")
    return isinstance(logos, list) and len(logos) <= 3 and all(isinstance(l, dict) and l.get("kind") in TYPES_LOGO and url_permise(l.get("url")) for l in logos)


def _enfant_panneau(conn, entree):
    try:
        import panneau
        try:
            conn.send(panneau.produire(entree))
        except panneau.CanevasInvalide as e:
            conn.send({"erreur": "canevas_invalide", "detail": str(e)[:200]})
    except Exception:
        conn.send({"erreur": "erreur_traitement"})


def produire_panneau_isole(entree: dict) -> dict:
    """Un processus par épreuve, 60 s au plus ; le contenu des logos du client n'est décodé que là."""
    pere, fils = mp.get_context("spawn").Pipe(False)
    pr = mp.get_context("spawn").Process(target=_enfant_panneau, args=(fils, entree))
    pr.start()
    if pere.poll(DELAI_PANNEAU):
        res = pere.recv()
        pr.join(5)
        return res
    pr.kill()
    return {"erreur": "delai"}


def fabriquer_panneau(p: dict):
    """Épreuve : logos téléchargés (URL signées courtes), PDF et aperçu déposés par URL signées, compte rendu à `worker-taches` (avertissements en codes)."""
    ok, avert = False, []
    with UN_A_LA_FOIS:
        try:
            logos = [{"octets": telecharger(l["url"], LOGO_MAX, False), "kind": l["kind"]} for l in p["logos"]]
            res = produire_panneau_isole({"code": p["code"], "mail": p.get("mail"), "url": p.get("url"), "qr": p.get("qr"), "logos": logos})
            if "pdf" in res:
                requete(p["upload_pdf"], "PUT", res["pdf"], "application/pdf")
                requete(p["upload_png"], "PUT", res["png"], "image/png")
                ok, avert = True, res["avertissements"]
            else:
                avert = [{"code": res["erreur"]}] if res["erreur"] == "canevas_invalide" else []
                print(json.dumps({"erreur": "panneau", "cause": res["erreur"], "detail": res.get("detail")}), flush=True)
        except Exception:  # aucune donnée de logo dans les journaux
            print(json.dumps({"erreur": "panneau"}), flush=True)
        try:
            requete(f"{FONCTIONS}/worker-taches", "POST", json.dumps({"panneau_fini": p["id"], "gen": p["gen"], "ok": ok, "avertissements": avert}).encode(), "application/json", SECRET)
        except Exception:
            print(json.dumps({"erreur": "rappel_panneau"}), flush=True)


def boucle():
    while True:
        try:
            req = urllib.request.Request(f"{FONCTIONS}/worker-taches", b'{"zip": true, "media": true, "panneau": true}', {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    reponse = json.loads(r.read(100_000))
                    if tache_valide(reponse.get("tache")):
                        travailler(reponse["tache"], reponse["jeton"])
                        continue  # enchaîner tant qu'il y a des photos
                    if media_valide(reponse.get("media")):
                        reencoder_media(reponse["media"])
                        continue
                    if panneau_valide(reponse.get("panneau")):
                        fabriquer_panneau(reponse["panneau"])
                        continue
                    if zip_valide(reponse.get("zip")):
                        archiver(reponse["zip"])
                        continue
        except Exception:
            print(json.dumps({"erreur": "interrogation"}), flush=True)
        time.sleep(10)


if __name__ == "__main__":
    if SECRET and HOTE:
        threading.Thread(target=boucle, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1" if os.environ.get("LOCAL") else "0.0.0.0", 8080), H).serve_forever()

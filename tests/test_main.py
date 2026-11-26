"""Tests de l'interface /process : jeton, adresses permises, tâche valide (aucun réseau)."""
import importlib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def charger(monkeypatch):
    monkeypatch.setenv("SUPABASE_FUNCTIONS_URL", "https://exemple.supabase.co/functions/v1")
    monkeypatch.setenv("WORKER_TOKEN", "x" * 40)
    import main
    return importlib.reload(main)


def test_adresses_permises(monkeypatch):
    m = charger(monkeypatch)
    assert m.url_permise("https://exemple.supabase.co/storage/v1/x")
    for mauvaise in ("http://exemple.supabase.co/x", "https://autre.example/x", "https://exemple.supabase.co@evil.example/x",
                     "https://evil.example/https://exemple.supabase.co/", "file:///etc/passwd", None, 12):
        assert not m.url_permise(mauvaise), mauvaise


def test_tache(monkeypatch):
    m = charger(monkeypatch)
    u = "https://exemple.supabase.co/s/"
    noms = [f"{w}.{e}" for w in (640, 1280, 1600) for e in ("webp", "jpg")] + ["thumb.jpg"]
    t = {"photo_id": "p1", "original_url": u + "o", "sortie": {n: {"upload_url": u + n, "public_url": u + n} for n in noms}}
    assert m.tache_valide(t)
    assert not m.tache_valide({**t, "original_url": "https://evil.example/o"})
    assert not m.tache_valide({**t, "reference_url": "https://evil.example/r"})
    t["sortie"]["thumb.jpg"]["upload_url"] = "https://evil.example/u"
    assert not m.tache_valide(t)


def test_traitement_isole_de_bout_en_bout(monkeypatch):
    import os, cv2, pytest
    if not os.path.exists(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")):
        pytest.skip("modèle de détection des visages absent de cet environnement (présent dans l'image du worker)")
    m = charger(monkeypatch)
    from test_traiter import scene, octets
    r = m.traiter_isole(octets(scene()), None)
    # Doute → revue manuelle avec les variantes (décision du 24/11) ; seul un refus n'en a pas.
    assert r["status"] in ("accepted", "pending", "rejected") and (r["status"] == "rejected" or len(r["variants"]) == 7)


def test_delai_depasse_envoie_en_revue(monkeypatch):
    m = charger(monkeypatch)
    monkeypatch.setattr(m, "DELAI_TRAITEMENT", 0.01)
    r = m.traiter_isole(b"x", None)
    assert (r["status"], r["reasons"]) == ("pending", ["delai"])


def test_archive(monkeypatch):
    import io, zipfile
    m = charger(monkeypatch)
    u = "https://exemple.supabase.co/storage/v1/object/"
    z = {"id": "j1", "dossier": "4311", "upload_url": u + "upload/sign/archives/4311.zip?token=x",
         "fichiers": [{"nom": "4311-001_2026-11-01.jpg", "url": u + "public/media/a.jpg"}, {"nom": "4311-002_2026-11-02.jpg", "url": u + "public/media/b.jpg"}],
         "textes": {"manifest.csv": "cairn,pierre\n", "LISEZMOI.txt": "é"}}
    assert m.zip_valide(z)
    # noms piégés (sortie du dossier), adresses hors de l'hôte, dépôt ailleurs : refusés
    assert not m.zip_valide({**z, "fichiers": [{"nom": "../../etc/passwd", "url": u + "x"}]})
    assert not m.zip_valide({**z, "fichiers": [{"nom": "a.jpg", "url": "https://evil.example/a.jpg"}]})
    assert not m.zip_valide({**z, "upload_url": "https://evil.example/u"})
    assert not m.zip_valide({**z, "dossier": "../x"})
    assert not m.zip_valide({**z, "textes": {"../x": "a"}})
    assert not m.zip_valide({**z, "fichiers": []})
    monkeypatch.setattr(m, "telecharger", lambda url: b"image-" + url[-5:].encode())
    a = zipfile.ZipFile(io.BytesIO(m.assembler_zip(z)))
    assert sorted(a.namelist()) == ["4311/4311-001_2026-11-01.jpg", "4311/4311-002_2026-11-02.jpg", "4311/LISEZMOI.txt", "4311/manifest.csv"]
    assert a.read("4311/4311-001_2026-11-01.jpg") == b"image-a.jpg" and a.read("4311/LISEZMOI.txt").decode() == "é"
    # archive trop grosse : abandon, et le compte rendu dit « échec »
    monkeypatch.setattr(m, "ZIP_MAX", 5)
    envois = []
    monkeypatch.setattr(m, "requete", lambda url, methode, corps, type_, jeton=None, delai=30: envois.append((url, corps)))
    m.archiver(z)
    assert len(envois) == 1 and envois[0][0].endswith("/worker-taches") and b'"ok": false' in envois[0][1]


def test_tache_image_d_actualite(monkeypatch):
    m = charger(monkeypatch)
    u = "https://exemple.supabase.co/s/"
    assert m.media_valide({"id": "m1", "original_url": u + "o", "upload_url": u + "u"})
    assert not m.media_valide({"id": "m1", "original_url": "https://evil.example/o", "upload_url": u + "u"})
    assert not m.media_valide({"id": "m1", "original_url": u + "o", "upload_url": "http://exemple.supabase.co/u"})
    assert not m.media_valide({"original_url": u + "o", "upload_url": u + "u"})
    assert not m.media_valide(None)


def test_image_d_actualite_de_bout_en_bout(monkeypatch):
    """Téléchargement simulé → ré-encodage isolé → dépôt de la seule image JPEG → compte rendu « ok »."""
    m = charger(monkeypatch)
    from test_traiter import scene, octets
    from PIL import Image
    exif = Image.Exif(); exif[0x010F] = "Marque-secrete"
    envois = []
    monkeypatch.setattr(m, "telecharger", lambda u: octets(scene(), exif=exif))
    monkeypatch.setattr(m, "requete", lambda u, meth, corps, type_, jeton=None, delai=30: envois.append((u, meth, corps, type_)))
    u = "https://exemple.supabase.co/s/"
    m.reencoder_media({"id": "m1", "original_url": u + "o", "upload_url": u + "u"})
    depot, rappel = envois
    assert depot[0] == u + "u" and depot[1] == "PUT" and depot[3] == "image/jpeg" and depot[2][:3] == b"\xff\xd8\xff" and b"Marque-secrete" not in depot[2]
    assert json.loads(rappel[2]) == {"media_fini": "m1", "ok": True}


def test_image_d_actualite_illisible_signalee_en_erreur(monkeypatch):
    m = charger(monkeypatch)
    envois = []
    monkeypatch.setattr(m, "telecharger", lambda u: b"<svg onload=alert(1)/>")
    monkeypatch.setattr(m, "requete", lambda u, meth, corps, type_, jeton=None, delai=30: envois.append((u, corps)))
    u = "https://exemple.supabase.co/s/"
    m.reencoder_media({"id": "m2", "original_url": u + "o", "upload_url": u + "u"})
    assert len(envois) == 1 and json.loads(envois[0][1]) == {"media_fini": "m2", "ok": False}   # rien n'est déposé


def test_logo_iframe_forme_et_traitement_isole(monkeypatch):
    import io
    from PIL import Image
    m = charger(monkeypatch)
    u = "https://exemple.supabase.co/storage/v1/object/"
    ok = {"id": "o", "kind": "png", "chemin": "o/a.png", "url": u + "sign/org-logos/a.png?token=x", "upload_url": u + "upload/sign/logos-iframe/o/a.png?token=x"}
    assert m.logo_valide(ok)
    assert not m.logo_valide({**ok, "kind": "svg"}) and not m.logo_valide({**ok, "chemin": None}) and not m.logo_valide({**ok, "upload_url": "https://evil.example/u"}) and not m.logo_valide(None)
    im = Image.new("RGB", (300, 100), "white")
    im.paste((200, 30, 30), (40, 20, 260, 80))
    b = io.BytesIO(); im.save(b, "PNG")
    res = m.produire_panneau_isole({"octets": b.getvalue(), "kind": "png"})
    assert Image.open(io.BytesIO(res["png"])).height <= 112

"""Tests de l'interface /process : jeton, adresses permises, tâche valide (aucun réseau)."""
import importlib, sys
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
    m = charger(monkeypatch)
    from test_traiter import scene, octets
    r = m.traiter_isole(octets(scene()), None)
    assert r["status"] in ("accepted", "rejected") and (r["status"] != "accepted" or len(r["variants"]) == 7)


def test_delai_depasse_envoie_en_revue(monkeypatch):
    m = charger(monkeypatch)
    monkeypatch.setattr(m, "DELAI_TRAITEMENT", 0.01)
    r = m.traiter_isole(b"x", None)
    assert (r["status"], r["reasons"]) == ("pending", ["delai"])

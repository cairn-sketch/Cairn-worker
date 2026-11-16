"""Tests du traitement (images générées par programme : aucune photo de personne dans le dépôt)."""
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import traiter as t  # noqa: E402


def scene(decalage=(0, 0), w=1200, h=900, graine=1) -> Image.Image:
    """Paysage synthétique : bruit à plusieurs échelles + formes, assez de détails pour un recalage."""
    r = np.random.default_rng(graine)
    base = np.zeros((h + 200, w + 200), np.float32)
    for k in (8, 24, 80):
        base += np.kron(r.random((base.shape[0] // k + 1, base.shape[1] // k + 1)), np.ones((k, k)))[: base.shape[0], : base.shape[1]] * (k ** 0.5)
    base = (255 * (base - base.min()) / np.ptp(base)).astype(np.uint8)
    dx, dy = decalage
    rec = base[100 + dy:100 + dy + h, 100 + dx:100 + dx + w]
    return Image.fromarray(np.stack([rec, rec, rec], -1)).convert("RGB")


def octets(im, fmt="JPEG", **kw):
    b = io.BytesIO(); im.save(b, fmt, **kw); return b.getvalue()


SANS_VISAGE = lambda g: []  # noqa: E731


def test_photo_nette_acceptee_et_variantes():
    r = t.traiter(octets(scene()), visages=SANS_VISAGE)
    assert r["status"] == "accepted" and r["reasons"] == []
    assert set(r["variants"]) == {"640.webp", "640.jpg", "1280.webp", "1280.jpg", "1600.webp", "1600.jpg", "thumb.jpg"}
    assert Image.open(io.BytesIO(r["variants"]["1600.jpg"])).size == (1600, 1200)
    assert Image.open(io.BytesIO(r["variants"]["thumb.jpg"])).size == (480, 360)


def test_floue_rejetee():
    flou = scene().filter(ImageFilter.GaussianBlur(12))
    assert t.traiter(octets(flou), visages=SANS_VISAGE)["reasons"] == ["floue"]


def test_trop_sombre_rejetee():
    sombre = Image.fromarray((np.asarray(scene()) * 0.05).astype(np.uint8))
    assert t.traiter(octets(sombre), visages=SANS_VISAGE)["reasons"] == ["trop_sombre"]


def _rect(frac, x=0.4, y=0.3):
    return lambda g: [(int(g.shape[1] * x), int(g.shape[0] * y), int(g.shape[1] * frac), int(g.shape[1] * frac))]


def test_visage_assez_grand_rejete_si_le_lieu_le_demande_petit_ignore():
    img = octets(scene())
    r = t.traiter(img, visages=_rect(0.2), politique_visages="refuser")
    assert r["reasons"] == ["visage"] and r["status"] == "rejected" and r["scores"]["has_faces"] is True
    assert t.traiter(img, visages=_rect(0.02), politique_visages="refuser")["status"] == "accepted"


def test_visage_assez_grand_flouté_par_défaut_et_publié():
    im = scene()
    r = t.traiter(octets(im), visages=_rect(0.2))
    assert r["status"] == "accepted" and r["reasons"] == [] and r["scores"]["has_faces"] is True and r["variants"]
    # la zone du visage est lissée : bien moins de contraste local que l'image d'origine (même découpe 4:3)
    avant = np.asarray(t.recadrer(im.convert("RGB")).resize((1600, 1200)).convert("L"), dtype=float)
    apres = np.asarray(Image.open(io.BytesIO(r["variants"]["1600.jpg"])).convert("L"), dtype=float)
    zone = (slice(int(1200 * 0.3), int(1200 * 0.3) + 250), slice(int(1600 * 0.4), int(1600 * 0.4) + 250))
    assert apres[zone].std() < avant[zone].std() * 0.6 or apres[zone].std() < 8
    hors = (slice(900, 1100), slice(100, 300))  # loin du visage : inchangé
    assert abs(apres[hors].mean() - avant[hors].mean()) < 6


def test_petit_visage_non_flouté():
    r = t.traiter(octets(scene()), visages=_rect(0.02))
    assert r["scores"]["has_faces"] is False and r["status"] == "accepted"


def test_illisible_rejete_sans_exception():
    assert t.traiter(b"pas une image")["reasons"] == ["format"]


def test_gps_et_metadonnees_supprimes_et_orientation_appliquee():
    im = scene(w=1200, h=900)
    ex = Image.Exif(); ex[0x0112] = 6; ex[0x8825] = {1: "N", 2: (48.0, 51.0, 24.0)}; ex[0x010F] = "Marque"
    brut = octets(im, exif=ex)
    assert b"Marque" in brut
    r = t.traiter(brut, visages=SANS_VISAGE)
    for nom, v in r["variants"].items():
        out = Image.open(io.BytesIO(v))
        assert not out.getexif() and b"Marque" not in v and b"Exif" not in v, nom
    # orientation 6 : l'image portrait 900x1200 est recadrée en 4:3 (pas de 1600x1200 déformé)
    assert t.decoder(brut).size == (900, 1200)


def test_hors_cadre_en_revue_manuelle():
    ref = octets(scene(graine=1))
    autre = octets(scene(graine=7))
    r = t.traiter(autre, ref, visages=SANS_VISAGE)
    assert (r["status"], r["reasons"]) == ("pending", ["hors_cadre"])
    assert r["variants"]  # les variantes existent quand même pour la revue


def test_petit_decalage_aligne_grand_decalage_conserve():
    ref = octets(scene())
    petit = t.traiter(octets(scene(decalage=(20, 10))), ref, visages=SANS_VISAGE)
    assert petit["status"] == "accepted" and petit["scores"]["alignment_score"] is not None
    grand = t.traiter(octets(scene(decalage=(90, 60))), ref, visages=SANS_VISAGE)
    assert grand["status"] in ("accepted", "pending") and grand["scores"]["alignment_score"] is None


def test_bombe_de_decompression_refusee(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    assert t.traiter(octets(scene(w=200, h=200)))["reasons"] == ["format"]


def test_heic_decode():
    pytest.importorskip("pillow_heif")
    import pillow_heif
    b = io.BytesIO(); pillow_heif.from_pillow(scene()).save(b); 
    assert t.traiter(b.getvalue(), visages=SANS_VISAGE)["status"] == "accepted"


def test_reencoder_efface_exif_gps_et_applique_l_orientation():
    """Image d'actualité (V1.2 S3) : aucune métadonnée ne survit, orientation appliquée, taille limitée, sortie JPEG."""
    im = scene(w=2400, h=1200)
    exif = Image.Exif()
    exif[0x010F] = "Marque-secrete"          # fabricant
    exif[0x0112] = 6                          # orientation : 90° → l'image doit être tournée
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (43.0, 3.0, 0.0), "E", (0.0, 5.0, 0.0)   # position GPS
    source = octets(im, exif=exif, icc_profile=b"profil-bidon")
    assert b"Marque-secrete" in source
    sortie = t.reencoder(source)
    assert sortie[:3] == b"\xff\xd8\xff"
    for secret in (b"Marque-secrete", b"Exif", b"profil-bidon", b"ICC_PROFILE"):
        assert secret not in sortie
    res = Image.open(io.BytesIO(sortie))
    assert not res.getexif() and "icc_profile" not in res.info
    assert max(res.size) <= 1600
    assert res.size[1] > res.size[0]  # tournée par l'orientation EXIF (2400×1200 → portrait)


def test_reencoder_refuse_ce_qui_n_est_pas_une_image():
    for faux in (b"", b"<svg onload=alert(1)/>", b"%PDF-1.4", b"x" * 1000):
        with pytest.raises(t.Illisible):
            t.reencoder(faux)

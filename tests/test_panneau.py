"""Ébauche du panneau (V1.2 S4c) : canevas à marqueurs, PDF vectoriel aux polices incorporées, QR décodé sur l'ébauche, logos (mise à l'échelle,
avertissements, fichiers déguisés, énormes, malformés), exécution isolée et tâche du worker. Aucun réseau."""
import importlib
import io
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import panneau  # noqa: E402
from exemples_epreuves import logo_carre, logo_etroit, logo_large  # noqa: E402

HZ = float(re.search(r'data-hauteur="([\d.]+)"', panneau.CANEVAS.read_text(encoding="utf-8")).group(1))  # hauteur de la zone des logos (17,3 mm sur le panneau V2)
SZ = float(re.search(r'<g id="logos" transform="translate\([\d.]+ ([\d.]+)\)', panneau.CANEVAS.read_text(encoding="utf-8")).group(1))  # haut de la zone des logos
FIXTURES = json.loads((Path(__file__).parent / "qr_fixtures.json").read_text())
MAIL = FIXTURES["PYR001"]["lien"][7:].split("?")[0]


def tache(code="PYR001", logos=(), **kw):
    f = FIXTURES.get(code)
    base = {"code": code, "mail": MAIL, "url": "hote.exemple/c/" + code, "qr": f["qr"] if f else None, "logos": [{"octets": o, "kind": k} for o, k in logos]}
    base.update(kw)
    return base


def png(w, h, couleur="white", mode="RGB"):
    t = io.BytesIO()
    Image.new(mode, (w, h), couleur).save(t, "PNG")
    return t.getvalue()


def jpg(w, h):
    t = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(t, "JPEG")
    return t.getvalue()


def pdf_pages(n):
    t = io.BytesIO()
    pages = [Image.new("RGB", (900, 300), "white") for _ in range(n)]
    pages[0].save(t, "PDF", save_all=True, append_images=pages[1:], resolution=72)
    return t.getvalue()


def raster(pdf: bytes, dpi: int) -> Image.Image:
    d = pdfium.PdfDocument(pdf)
    try:
        return d[0].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        d.close()


def codes(r):
    return [a["code"] for a in r["avertissements"]]


# ------------------------------------------------------------------ rendu : PDF vectoriel à l'échelle, polices incorporées, textes
def test_pdf_a_l_echelle_polices_incorporees_et_textes():
    r = panneau.produire(tache())
    d = pdfium.PdfDocument(r["pdf"])
    assert len(d) == 1
    w, h = d[0].get_size()
    assert (round(w * 25.4 / 72, 1), round(h * 25.4 / 72, 1)) == (206.0, 346.0)  # 200 x 340 mm + fond perdu de 3 mm de chaque côté
    texte = d[0].get_textpage().get_text_range()  # texte réel (vectoriel), pas une image
    assert "PYR001" in texte and MAIL in texte and "Objet" in texte and "hote.exemple/c/PYR001" in texte and "Retrouve le timelapse" in texte
    assert texte.count("hote.exemple") == 1  # adresse web (le pied « cairn-photo.fr » du panneau V2 est vectorisé)
    assert "{{" not in texte and "chronolog" not in texte.lower()
    polices = set(re.findall(rb"/BaseFont /[A-Z]{6}\+([A-Za-z-]+)", r["pdf"]))
    for attendu in (b"DMMono-Medium", b"Figtree-ExtraBold", b"Figtree-SemiBold", b"Figtree-Medium"):
        assert attendu in polices, polices  # sous-ensembles incorporés (préfixe XXXXXX+)
    assert polices <= {b"DMMono-Medium", b"DMMono-Regular", b"Figtree-ExtraBold", b"Figtree-SemiBold", b"Figtree-Medium"}, polices  # aucune police de repli
    assert b"/FontFile2" in r["pdf"]
    assert len(r["pdf"]) < 2_000_000
    assert r["png"][:8] == b"\x89PNG\r\n\x1a\n" and Image.open(io.BytesIO(r["png"])).size[0] == 1000


# ------------------------------------------------------------------ QR : décodé sur l'ébauche elle-même
def decoder_tuile(pdf: bytes, dpi: int) -> str:
    im = raster(pdf, dpi)
    k = dpi / 25.4
    tuile = im.crop((int(18 * k), int(234.7 * k), int(65 * k), int(281.7 * k)))  # tuile claire 47 x 47 mm (+ 3 mm de fond perdu)
    return cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(np.asarray(tuile), cv2.COLOR_RGB2BGR))[0]


@pytest.mark.parametrize("code", list(FIXTURES))
def test_qr_de_l_epreuve_redonne_exactement_le_lien_attendu(code):
    r = panneau.produire(tache(code))
    assert decoder_tuile(r["pdf"], 300) == f"mailto:{MAIL}?subject={code}"
    assert FIXTURES[code]["lien"] == f"mailto:{MAIL}?subject={code}"


@pytest.mark.parametrize("dpi", [150, 200])
def test_qr_lisible_a_la_taille_imprimee_35_mm(dpi):
    """38 mm de côté, modules d'environ 1 mm : lisible même sur une rastérisation modeste."""
    r = panneau.produire(tache("MASS007"))
    assert decoder_tuile(r["pdf"], dpi) == f"mailto:{MAIL}?subject=MASS007"
    n = len(FIXTURES["MASS007"]["qr"])
    assert 38 / n >= panneau.MODULE_MIN_MM


def test_qr_35_mm_marge_claire_et_contraste():
    im = np.asarray(raster(panneau.produire(tache())["pdf"], 300).convert("L"), dtype=float)
    k = 300 / 25.4
    qr = im[int(240 * k):int(276 * k), int(23.5 * k):int(59.5 * k)]
    marge = im[int(235.6 * k):int(238.6 * k), int(25 * k):int(58 * k)]  # bande de la tuile claire au-dessus du QR (4,5 mm de marge)
    assert marge.min() > 230 and qr.min() < 80  # marge blanche, modules ardoise
    assert (qr.max() - qr.min()) > 150  # contraste fort


def test_sans_adresse_pas_de_qr_et_a_completer_visible():
    r = panneau.produire(tache(mail=None, url=None, qr=None))
    t = pdfium.PdfDocument(r["pdf"])[0].get_textpage().get_text_range()
    assert t.count("[À COMPLÉTER]") == 3  # adresse de réception, adresse web, emplacement du QR
    assert {"adresse_mail_absente", "adresse_web_absente"} <= set(codes(r))
    assert decoder_tuile(r["pdf"], 300) == ""


def test_matrice_de_qr_malformee_refusee_sans_planter():
    for mauvaise in (["1" * 21] * 20, ["2" * 21] * 21, "texte", [["1"] * 21] * 21, ["1" * 300] * 300):
        r = panneau.produire(tache(qr=mauvaise))
        assert "[À COMPLÉTER]" in pdfium.PdfDocument(r["pdf"])[0].get_textpage().get_text_range()


def test_texte_injecte_ne_passe_pas():
    with pytest.raises(ValueError):
        panneau.produire(tache(code='PYR001"><script>alert(1)</script>'))
    r = panneau.produire(tache(mail="<script>@x.fr", url='x"><script>'))
    assert {"adresse_mail_absente", "adresse_web_absente"} <= set(codes(r))
    svg, _ = panneau.composer(tache(mail="a@b.fr", url="x.fr/c/PYR001"))
    assert "<script" not in svg and "{{" not in svg


# ------------------------------------------------------------------ canevas remplaçable : aucun changement de code
def test_canevas_du_depot_a_tous_ses_marqueurs():
    svg = panneau.lire_canevas()
    for m in panneau.MARQUEURS:
        assert "{{%s}}" % m in svg


def test_marqueur_manquant_erreur_claire(tmp_path):
    base = panneau.CANEVAS.read_text(encoding="utf-8")
    for m in panneau.MARQUEURS:
        f = tmp_path / f"sans-{m}.svg"
        f.write_text(base.replace("{{%s}}" % m, ""), encoding="utf-8")
        with pytest.raises(panneau.CanevasInvalide, match=re.escape("{{%s}}" % m)):
            panneau.produire(tache(), f)


def test_marqueur_hors_groupe_erreur_claire(tmp_path):
    f = tmp_path / "x.svg"
    f.write_text(re.sub(r'<g id="qr"[^>]*>\{\{QR\}\}</g>', "{{QR}}", panneau.CANEVAS.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(panneau.CanevasInvalide, match=r"\{\{QR\}\}.*groupe"):
        panneau.lire_canevas(f)


def test_autre_canevas_meme_code(tmp_path):
    """Un nouveau canevas (autre taille, autres positions, marqueurs déplacés) se dépose sans toucher au code."""
    f = tmp_path / "autre.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100" width="100mm" height="100mm">'
                 '<rect width="100" height="100" fill="#FFFCF4"/><text x="5" y="10" font-family="Figtree" font-size="5">{{CODE}} {{MAIL}} {{URL}} {{CODE}}</text>'
                 '<g transform="translate(5 20)" fill="#000" data-cote="30">{{QR}}</g>'
                 '<g transform="translate(40 20)" stroke="#000" data-largeur="50" data-hauteur="20" data-marge="2">{{LOGOS}}</g></svg>', encoding="utf-8")
    r = panneau.produire(tache(logos=[(logo_carre(), "png")]), f)
    d = pdfium.PdfDocument(r["pdf"])
    assert round(d[0].get_size()[0] * 25.4 / 72) == 100
    assert d[0].get_textpage().get_text_range().count("PYR001") == 2
    assert r["avertissements"] == []


# ------------------------------------------------------------------ logos : trois formes, mise à l'échelle, jamais étirés ni recolorés
def images(svg):
    return [tuple(float(v) for v in m) for m in re.findall(r'<image x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)]


def contenu(o: bytes):
    """Taille du contenu visible (hors fond blanc ou transparent) d'un logo, sans marge."""
    a = np.asarray(Image.open(io.BytesIO(o)).convert("RGBA")).astype(int)
    vis = (a[..., 3] > 16) & ~(a[..., :3] >= 231).all(axis=2)
    ys, xs = np.where(vis.any(axis=1))[0], np.where(vis.any(axis=0))[0]
    return xs[-1] - xs[0] + 1, ys[-1] - ys[0] + 1


@pytest.mark.parametrize("nom,fabrique", [("large", logo_large), ("carre", logo_carre), ("etroit", logo_etroit)])
def test_trois_formes_de_logo_dans_l_emplacement(nom, fabrique):
    original = fabrique()
    ow, oh = contenu(original)  # les marges blanches sont rognées : les proportions sont celles du contenu
    p = panneau.preparer_logo(original, "png", 168, HZ - 3)
    assert p["w"] / p["h"] == pytest.approx(ow / oh, rel=0.15)  # proportions du contenu conservées (+ petite marge)
    svg, _ = panneau.composer(tache(logos=[(original, "png")]))
    (x, y, w, h), = images(svg)
    assert min(x, 176 - (x + w)) >= 4 - 1e-3 and min(y, HZ - (y + h)) >= 1.5 - 1e-3  # marges : 4 mm sur les côtés, 1,5 mm en hauteur
    assert abs(x - (176 - (x + w))) < 0.01 and abs(y - (HZ - (y + h))) < 0.01  # centré dans la zone de 176 mm x HZ
    assert w / h == pytest.approx(ow / oh, rel=0.15)  # jamais étiré
    assert (abs(w - 168) < 0.01 or abs(h - (HZ - 3)) < 0.01)  # au plus grand dans la zone utile
    im = raster(panneau.produire(tache(logos=[(original, "png")]))["pdf"], 150)  # et il est bien dessiné dans l'ébauche
    k = 150 / 25.4
    zone = np.asarray(im.crop((int((3 + 12 + x) * k), int((3 + SZ + y) * k), int((3 + 12 + x + w) * k), int((3 + SZ + y + h) * k))).convert("L"))
    assert (zone < 120).mean() > 0.02


def test_jamais_recolore_ni_etire():
    """Petit logo (aucun rééchantillonnage) : les couleurs de l'original se retrouvent à l'identique, mêmes dimensions."""
    im = Image.new("RGB", (300, 100), "white")
    im.paste((200, 30, 30), (20, 20, 120, 80))
    im.paste((10, 120, 60), (160, 20, 280, 80))
    t = io.BytesIO(); im.save(t, "PNG")
    p = panneau.preparer_logo(t.getvalue(), "png", 168, 16)
    sortie = np.asarray(Image.open(io.BytesIO(p["png"])).convert("RGBA"))
    assert sortie.shape[1] - 2 * 2 >= 260 and sortie.shape[0] - 2 * 2 >= 60  # rogné au contenu (260 x 60) plus 2 px de marge, aucun rééchantillonnage
    opaques = {tuple(c[:3]) for c in sortie.reshape(-1, 4) if c[3] == 255}
    assert opaques == {(200, 30, 30), (10, 120, 60)}  # couleurs inchangées ; le fond blanc est devenu transparent


def centres(n, logo=logo_carre):
    svg, _ = panneau.composer(tache(logos=[(logo(), "png")] * n))
    return [x + w / 2 for x, y, w, h in images(svg)], [(y, h) for x, y, w, h in images(svg)]


def test_disposition_un_deux_trois_logos():
    c1, _ = centres(1)
    assert c1 == [pytest.approx(88, abs=0.01)]  # un logo : au milieu de la zone (ligne médiane x = 88 mm)
    c2, v2 = centres(2)
    assert c2[0] + c2[1] == pytest.approx(176, abs=0.01) and c2[0] < 88 < c2[1]  # symétriques de part et d'autre de la ligne médiane
    c3, v3 = centres(3)
    assert c3[1] == pytest.approx(88, abs=0.01) and c3[0] + c3[2] == pytest.approx(176, abs=0.01)  # le central sur la ligne médiane
    assert c3[1] - c3[0] == pytest.approx(c3[2] - c3[1], abs=0.01)  # répartis régulièrement
    for v in (v2, v3):
        assert len(set(v)) == 1  # même hauteur utile et même position verticale (centrés verticalement)
    assert v3[0][0] + v3[0][1] / 2 == pytest.approx(HZ / 2, abs=0.01)


def test_logos_de_formes_differentes_centres_chacun_dans_sa_cellule():
    svg, _ = panneau.composer(tache(logos=[(logo_large(), "png"), (logo_carre(), "png"), (logo_etroit(), "png")]))
    im = images(svg)
    assert [round(x + w / 2, 2) for x, y, w, h in im] == pytest.approx([32, 88, 144], abs=0.01)  # centres des 3 cellules (168 / 3 = 56 mm)
    for (x, y, w, h), (bx, bw) in zip(im, [(4, 52), (4 + 56 + 2, 52 - 0), (4 + 112 + 2, 52)]):
        assert y >= 1.5 - 1e-3 and y + h <= HZ - 1.5 + 1e-3 and w <= 52 + 1e-3  # 4 mm au moins entre deux logos
    assert "stroke-dasharray" not in svg.split('id="logos"')[1]  # plus de pointillé séparateur
    assert len(pdfium.PdfDocument(panneau.produire(tache(logos=[(logo_large(), "png")] * 3))["pdf"])) == 1
    assert panneau.composer(tache(logos=[(logo_carre(), "png")] * 4))[0].count("<image") == 3  # trois au plus


def test_sans_logo_emplacement_vide_et_avertissement():
    r = panneau.produire(tache())
    assert codes(r) == ["aucun_logo"]


# ------------------------------------------------------------------ contrôles automatiques et avertissements
def test_resolution_trop_faible():
    r = panneau.produire(tache(logos=[(png(120, 40), "png")]))
    a = next(x for x in r["avertissements"] if x["code"] == "resolution_faible")
    assert a["logo"] == 0 and a["dpi"] < panneau.DPI_MIN
    assert "resolution_faible" not in codes(panneau.produire(tache(logos=[(logo_large(), "png")])))


def test_fond_non_blanc_et_fond_transparent():
    sombre = Image.new("RGB", (1200, 400), (40, 54, 74)); t = io.BytesIO(); sombre.save(t, "PNG")
    assert "fond_non_blanc" in codes(panneau.produire(tache(logos=[(t.getvalue(), "png")])))
    transparent = Image.new("RGBA", (1200, 400), (0, 0, 0, 0)); transparent.paste((40, 54, 74, 255), (300, 100, 900, 300)); t = io.BytesIO(); transparent.save(t, "PNG")
    assert "fond_non_blanc" not in codes(panneau.produire(tache(logos=[(t.getvalue(), "png")])))
    assert "fond_non_blanc" not in codes(panneau.produire(tache(logos=[(logo_large(), "png")])))


def test_rapport_trop_etroit():
    for o in (png(3000, 100), png(100, 1500)):
        assert "rapport_extreme" in codes(panneau.produire(tache(logos=[(o, "png")])))
    assert "rapport_extreme" not in codes(panneau.produire(tache(logos=[(logo_large(), "png")])))


def test_pdf_multipage_premiere_page_et_avertissement():
    assert "pdf_multipage" not in codes(panneau.produire(tache(logos=[(pdf_pages(1), "pdf")])))
    r = panneau.produire(tache(logos=[(pdf_pages(3), "pdf")]))
    assert {"code": "pdf_multipage", "pages": 3, "logo": 0} in r["avertissements"]
    assert "<image" in panneau.composer(tache(logos=[(pdf_pages(3), "pdf")]))[0]


def test_jpeg_accepte():
    assert "logo_refuse" not in codes(panneau.produire(tache(logos=[(jpg(1200, 400), "jpg")])))


# ------------------------------------------------------------------ sécurité : logo déguisé, énorme, malformé, SVG
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><image href="https://evil.example/x"/></svg>'


@pytest.mark.parametrize("octets,kind", [
    (SVG, "png"), (SVG, "jpg"), (SVG, "pdf"), (SVG, "svg"),
    (b"<html><script>alert(1)</script></html>", "png"),
    (png(50, 50), "pdf"), (png(50, 50), "jpg"), (jpg(50, 50), "png"), (pdf_pages(1), "png"),
    (b"MZ\x90\x00programme", "png"), (b"GIF89a" + b"\0" * 40, "png"), (b"", "png"), (b"\x89PNG", "png"),
    (b"%PDF-1.4 " + SVG, "png"),
])
def test_logo_deguise_refuse(octets, kind):
    with pytest.raises(panneau.LogoRefuse) as e:
        panneau.preparer_logo(octets, kind, 76, 20)
    assert e.value.code == "logo_refuse"
    r = panneau.produire(tache(logos=[(octets, kind)]))
    assert {"code": "logo_refuse", "logo": 0} in r["avertissements"] and panneau.BLOQUANTS & set(codes(r))
    assert b"evil.example" not in r["pdf"] and b"alert" not in r["pdf"]  # rien du fichier client dans l'ébauche


def test_svg_du_client_jamais_integre():
    r = panneau.composer(tache(logos=[(SVG, "png")]))[0]
    assert "evil.example" not in r and "<script" not in r and "alert" not in r


def test_logo_trop_gros_en_octets_refuse():
    gros = png(50, 50) + b"\0" * (panneau.LOGO_OCTETS_MAX + 1)
    assert "logo_refuse" in codes(panneau.produire(tache(logos=[(gros, "png")])))


def test_logo_enorme_en_pixels_refuse_avant_decodage():
    """PNG de 20 000 x 20 000 (400 millions de pixels) : quelques dizaines de Ko compressés, des Go une fois décodé."""
    im = Image.new("1", (20000, 20000), 1); t = io.BytesIO(); im.save(t, "PNG", optimize=False, compress_level=9)
    assert len(t.getvalue()) < panneau.LOGO_OCTETS_MAX
    with pytest.raises(panneau.LogoRefuse) as e:
        panneau.preparer_logo(t.getvalue(), "png", 76, 20)
    assert e.value.code == "logo_enorme"
    assert "logo_enorme" in codes(panneau.produire(tache(logos=[(t.getvalue(), "png")])))


def test_pdf_page_immense_refuse():
    contenu = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200000 200000]>>endobj\ntrailer<</Root 1 0 R/Size 4>>\n%%EOF"
    with pytest.raises(panneau.LogoRefuse) as e:
        panneau.preparer_logo(contenu, "pdf", 76, 20)
    assert e.value.code in ("logo_enorme", "logo_illisible")


@pytest.mark.parametrize("octets,kind", [
    (png(300, 100)[:120], "png"), (b"\x89PNG\r\n\x1a\n" + b"garbage" * 30, "png"),
    (jpg(300, 100)[:200], "jpg"), (b"\xff\xd8\xff\xe0" + b"garbage" * 30, "jpg"),
    (b"%PDF-1.7\n" + b"garbage" * 30, "pdf"), (pdf_pages(1)[:300], "pdf"),
])
def test_logo_malforme_illisible_sans_planter(octets, kind):
    with pytest.raises(panneau.LogoRefuse) as e:
        panneau.preparer_logo(octets, kind, 76, 20)
    assert e.value.code in ("logo_illisible", "logo_refuse")
    r = panneau.produire(tache(logos=[(octets, kind)]))
    assert panneau.BLOQUANTS & set(codes(r)) and len(r["pdf"]) > 1000  # l'ébauche sort quand même, avec l'avertissement bloquant


def test_un_mauvais_logo_n_empeche_pas_les_autres():
    r = panneau.produire(tache(logos=[(SVG, "png"), (logo_carre(), "png")]))
    assert {"code": "logo_refuse", "logo": 0} in r["avertissements"]
    assert "<image" in panneau.composer(tache(logos=[(SVG, "png"), (logo_carre(), "png")]))[0]


# ------------------------------------------------------------------ exécution par le worker : tâche valide, isolation, rappel
def charger(monkeypatch):
    monkeypatch.setenv("SUPABASE_FUNCTIONS_URL", "https://exemple.supabase.co/functions/v1")
    monkeypatch.setenv("WORKER_TOKEN", "x" * 40)
    import main
    return importlib.reload(main)


U = "https://exemple.supabase.co/storage/v1/"


def tache_worker(**kw):
    t = {"id": "a1a1a1a1-0000-4000-8000-000000000001", "gen": 2, "code": "PYR001", "mail": MAIL, "url": "hote.exemple/c/PYR001", "qr": FIXTURES["PYR001"]["qr"],
         "logos": [{"url": U + "l1", "kind": "png"}], "upload_pdf": U + "p.pdf", "upload_png": U + "p.png"}
    t.update(kw)
    return t


def test_tache_panneau_valide(monkeypatch):
    m = charger(monkeypatch)
    assert m.panneau_valide(tache_worker())
    assert m.panneau_valide(tache_worker(logos=[]))
    mauvaises = [
        tache_worker(upload_pdf="https://evil.example/p"), tache_worker(upload_png="http://exemple.supabase.co/p"),
        tache_worker(logos=[{"url": "https://evil.example/l", "kind": "png"}]), tache_worker(logos=[{"url": U + "l", "kind": "svg"}]),
        tache_worker(logos=[{"url": U + "l", "kind": "png"}] * 4), tache_worker(gen=True), tache_worker(gen="2"), tache_worker(id=5), tache_worker(code=7), None, [], "x",
    ]
    for t in mauvaises:
        assert not m.panneau_valide(t), t


def test_epreuve_isolee_de_bout_en_bout(monkeypatch):
    m = charger(monkeypatch)
    r = m.produire_panneau_isole({**tache(), "logos": [{"octets": logo_carre(), "kind": "png"}]})
    assert r["pdf"][:5] == b"%PDF-" and r["png"][:4] == b"\x89PNG" and r["avertissements"] == []


def test_epreuve_isolee_canevas_invalide(monkeypatch, tmp_path):
    m = charger(monkeypatch)
    f = tmp_path / "vide.svg"
    f.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    monkeypatch.setattr(panneau, "CANEVAS", f)  # processus enfant : repli par `spawn`, on teste donc la fonction directement
    with pytest.raises(panneau.CanevasInvalide, match="marqueur manquant"):
        panneau.produire(tache())


def test_delai_depasse(monkeypatch):
    m = charger(monkeypatch)
    monkeypatch.setattr(m, "DELAI_PANNEAU", 0.01)
    assert m.produire_panneau_isole(tache()) == {"erreur": "delai"}


def test_fabriquer_panneau_telecharge_depose_et_rend_compte(monkeypatch, capsys):
    m = charger(monkeypatch)
    envoyes = []
    monkeypatch.setattr(m, "telecharger", lambda u, maxi=m.TAILLE_MAX, refuser=True: logo_carre())
    monkeypatch.setattr(m, "requete", lambda u, methode, corps, type_, jeton=None, delai=30: envoyes.append((u, methode, type_, corps, jeton)))
    m.fabriquer_panneau(tache_worker())
    pdf, png_, rappel = envoyes
    assert pdf[0] == U + "p.pdf" and pdf[1] == "PUT" and pdf[2] == "application/pdf" and pdf[3][:5] == b"%PDF-"
    assert png_[0] == U + "p.png" and png_[2] == "image/png" and png_[3][:4] == b"\x89PNG"
    corps = json.loads(rappel[3])
    assert rappel[0].endswith("/worker-taches") and rappel[4] == "x" * 40
    assert corps == {"panneau_fini": "a1a1a1a1-0000-4000-8000-000000000001", "gen": 2, "ok": True, "avertissements": []}
    assert capsys.readouterr().out == ""  # rien dans les journaux


def test_fabriquer_panneau_echec_aucune_donnee_dans_les_journaux(monkeypatch, capsys):
    m = charger(monkeypatch)
    envoyes = []

    def echec(u, maxi=m.TAILLE_MAX, refuser=True):
        raise OSError("réseau coupé : https://exemple.supabase.co/storage/v1/l1?token=SECRET")

    monkeypatch.setattr(m, "telecharger", echec)
    monkeypatch.setattr(m, "requete", lambda u, methode, corps, type_, jeton=None, delai=30: envoyes.append((u, corps)))
    m.fabriquer_panneau(tache_worker())
    assert json.loads(envoyes[-1][1]) == {"panneau_fini": "a1a1a1a1-0000-4000-8000-000000000001", "gen": 2, "ok": False, "avertissements": []}
    sortie = capsys.readouterr().out
    assert "SECRET" not in sortie and "token" not in sortie


def test_logo_telecharge_borne_a_5_mo(monkeypatch):
    m = charger(monkeypatch)

    class Flux:
        def __init__(self): self.lu = None
        def read(self, n): self.lu = n; return b"x" * n
        def __enter__(self): return self
        def __exit__(self, *a): return False

    flux = Flux()
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda u, timeout=20: flux)
    assert len(m.telecharger(U + "l", m.LOGO_MAX, False)) == m.LOGO_MAX + 1  # lu jusqu'à la limite + 1 : panneau.py le refuse
    assert flux.lu == m.LOGO_MAX + 1
    with pytest.raises(ValueError):
        m.telecharger(U + "l", m.LOGO_MAX)


def test_adresse_trop_longue_pour_l_encadre():
    r = panneau.produire(tache(mail="a" * 30 + "@exemple.org"))
    assert "texte_trop_long" in codes(r) and "texte_trop_long" in panneau.BLOQUANTS
    assert "texte_trop_long" not in codes(panneau.produire(tache()))


def test_hote_du_pied_pris_dans_l_adresse_web(tmp_path):
    """Panneau V2 : pied « cairn-photo.fr » vectorisé ; un canevas qui garde {{HOTE}} reçoit l'hôte de l'adresse web."""
    assert 'id="pied-adresse"' in panneau.composer(tache())[0]
    f = tmp_path / "hote.svg"
    f.write_text(panneau.CANEVAS.read_text(encoding="utf-8").replace("</svg>", '<text font-family="Figtree">{{HOTE}}</text></svg>'), encoding="utf-8")
    svg, _ = panneau.composer(tache(url="cairn-photo.fr/c/PYR001"), f)
    assert ">cairn-photo.fr</text>" in svg and "{{HOTE}}" not in svg


def test_gabarit_et_calque_de_decoupe_non_imprimes():
    svg = panneau.composer(tache())[0]
    assert "CutContour" not in svg and "LOGOS DU PARTENAIRE" not in svg and "EC008C" not in svg


# ------------------------------------------------------------------ prétraitement : fond blanc détouré, marges rognées, logo qui remplit la zone
def logo_marges():
    """Écusson 400 x 400 au centre d'un JPG/PNG carré de 2000 x 2000, grandes marges blanches ; un blanc intérieur (un disque) à ne pas détourer."""
    im = Image.new("RGB", (2000, 2000), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((800, 800, 1199, 1199), fill=(40, 54, 74))
    d.ellipse((900, 900, 1099, 1099), fill="white")
    return im


def octets(im, fmt="PNG"):
    t = io.BytesIO(); im.save(t, fmt); return t.getvalue()


@pytest.mark.parametrize("fmt,kind", [("PNG", "png"), ("JPEG", "jpg")])
def test_logo_a_grandes_marges_blanches_remplit_la_zone(fmt, kind):
    o = octets(logo_marges(), fmt)
    svg, avert = panneau.composer(tache(logos=[(o, kind)]))
    (x, y, w, h), = images(svg)
    assert h == pytest.approx(HZ - 3, abs=0.01) and w == pytest.approx(h, rel=0.02)  # hauteur utile maximale, proportions du contenu (carré)
    assert "fond_non_blanc" not in [a["code"] for a in avert]
    p = panneau.preparer_logo(o, kind, 168, HZ - 3)
    sortie = np.asarray(Image.open(io.BytesIO(p["png"])).convert("RGBA"))
    assert sortie.shape[0] < 460 and sortie.shape[1] < 460  # 2000 px d'origine, contenu de 400 px + petite marge
    assert sortie[0, 0, 3] == 0 and sortie[-1, -1, 3] == 0  # fond transparent
    c = sortie.shape[0] // 2
    assert sortie[c, sortie.shape[1] // 2, 3] == 255 and tuple(sortie[c, sortie.shape[1] // 2, :3]) == (255, 255, 255)  # blanc intérieur conservé, opaque


def test_logo_deja_transparent_rogne_sans_alteration():
    im = Image.new("RGBA", (1500, 900), (0, 0, 0, 0))
    im.paste((200, 30, 30, 255), (500, 300, 1100, 500))
    p = panneau.preparer_logo(octets(im), "png", 168, HZ - 3)
    sortie = np.asarray(Image.open(io.BytesIO(p["png"])).convert("RGBA"))
    assert sortie.shape[:2] == (200 + 2 * 12, 600 + 2 * 12)  # contenu 600 x 200 + marge (2 % de 600)
    assert {tuple(c) for c in sortie.reshape(-1, 4) if c[3]} == {(200, 30, 30, 255)}
    assert "fond_non_blanc" not in codes(panneau.produire(tache(logos=[(octets(im), "png")])))


def test_fond_opaque_non_blanc_avertit_sans_bloquer():
    r = panneau.produire(tache(logos=[(octets(Image.new("RGB", (1200, 400), (40, 54, 74))), "png")]))
    assert "fond_non_blanc" in codes(r) and not panneau.BLOQUANTS & set(codes(r)) and "<image" in panneau.composer(tache(logos=[(octets(Image.new("RGB", (1200, 400), (40, 54, 74))), "png")]))[0]


def test_pdf_de_logo_rogne_ses_marges():
    im = Image.new("RGB", (2000, 1000), "white")
    ImageDraw.Draw(im).rectangle((900, 450, 1099, 549), fill=(200, 30, 30))  # 200 x 100 px au centre d'une page à grandes marges
    t = io.BytesIO(); im.save(t, "PDF", resolution=72)
    p = panneau.preparer_logo(t.getvalue(), "pdf", 168, HZ - 3)
    assert p["w"] / p["h"] == pytest.approx(2.0, rel=0.1) and p["w"] > 200
    assert Image.open(io.BytesIO(p["png"])).convert("RGBA").getpixel((0, 0))[3] == 0

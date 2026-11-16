"""Épreuve du panneau (V1.2 S4c) : le canevas (worker/panneau/canevas.svg, modèle à marqueurs) reçoit le code, l'adresse de réception, l'adresse web,
le QR (matrice envoyée par la fonction Edge) et les logos du client, puis sort en PDF vectoriel (polices incorporées) et en aperçu PNG.
Changer de canevas = déposer un autre fichier modèle ; aucun changement de code. Marqueurs : {{CODE}} {{MAIL}} {{URL}} {{QR}} {{LOGOS}} (+ {{HOTE}} facultatif : hôte du site, pied du panneau).
Sécurité : jamais de SVG venant du client ; un logo est un PNG, un JPEG ou un PDF reconnu par ses octets, décodé puis ré-encodé en PNG propre
(Pillow ; pypdfium2 pour la page 1 d'un PDF) ; seul le SVG du dépôt est donné à rsvg-convert, qui ne lit aucune adresse. Appelé dans un processus isolé (main.py)."""
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image, ImageOps

DOSSIER = Path(__file__).resolve().parent / "panneau"
CANEVAS = DOSSIER / "canevas.svg"
POLICES = DOSSIER / "polices"
MARQUEURS = ("CODE", "MAIL", "URL", "QR", "LOGOS")
A_COMPLETER = "[À COMPLÉTER]"
RE_CODE = re.compile(r"^[A-Z]{3,4}[0-9]{3}$")
RE_MAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,120}\.[A-Za-z]{2,}$")
RE_URL = re.compile(r"^[A-Za-z0-9._~:/-]{3,100}$")  # adresse imprimée, sans schéma
LOGO_OCTETS_MAX = 5 * 1024 * 1024
PIXELS_MAX = 40_000_000
PAGE_PDF_MAX = 5000  # points (1,76 m) : au-delà, ce n'est pas un logo
DPI_MIN = 200  # sous ce seuil, avertissement « résolution faible » (impression à l'échelle du panneau)
DPI_RENDU = 600  # rastérisation d'un PDF et plafond de ré-échantillonnage
PX_MAX = 3600
MODULE_MIN_MM = 0.6  # module de QR plus petit : illisible une fois imprimé
LOGOS_MAX = 3
MAX_TEXTE = 34  # caractères de l'adresse mail et de l'adresse web imprimées (DM Mono 5,6 et 4,4 dans l'encadré du canevas v2)
DELAI_RSVG = 30
BLOQUANTS = {"adresse_mail_absente", "adresse_web_absente", "logo_refuse", "logo_enorme", "logo_illisible", "qr_trop_dense", "canevas_invalide", "texte_trop_long"}
CODES_AVERTISSEMENT = BLOQUANTS | {"resolution_faible", "fond_non_blanc", "rapport_extreme", "pdf_multipage", "aucun_logo"}


class CanevasInvalide(ValueError):
    pass


class LogoRefuse(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ------------------------------------------------------------------ canevas
def _groupe(svg: str, marqueur: str):
    r = re.search(r"<g\b([^>]*)>\s*\{\{%s\}\}\s*</g>" % marqueur, svg)
    if not r:
        raise CanevasInvalide('le marqueur {{%s}} doit être seul dans un groupe <g transform="translate(x y)">…</g>' % marqueur)
    return r


def _nombre(attrs: str, nom: str, defaut: float) -> float:
    r = re.search(r'\b%s="([0-9.]+)"' % nom, attrs)
    return float(r.group(1)) if r else defaut


def lire_canevas(chemin=None) -> str:
    """Lit le modèle et vérifie ses marqueurs : un marqueur manquant est une erreur claire (jamais un panneau incomplet)."""
    svg = Path(chemin or CANEVAS).read_text(encoding="utf-8")
    manquants = [m for m in MARQUEURS if "{{%s}}" % m not in svg]
    if manquants:
        raise CanevasInvalide("marqueur manquant dans le canevas : " + ", ".join("{{%s}}" % m for m in manquants))
    for m in ("QR", "LOGOS"):
        _groupe(svg, m)
    if not re.search(r"<svg\b", svg):
        raise CanevasInvalide("le canevas n'est pas un fichier SVG")
    return svg


# ------------------------------------------------------------------ QR : matrice envoyée par la fonction Edge (même encodeur que le site, niveau Q)
def qr_valide(rows) -> bool:
    return (isinstance(rows, list) and 21 <= len(rows) <= 177 and all(isinstance(r, str) and len(r) == len(rows) and set(r) <= {"0", "1"} for r in rows))


def svg_qr(rows, cote: float) -> str:
    c = cote / len(rows)
    d = ""
    for y, ligne in enumerate(rows):
        for m in re.finditer(r"1+", ligne):  # un rectangle par suite de modules noirs
            d += "M%.4f %.4fh%.4fv%.4fh-%.4fz" % (m.start() * c, y * c, (m.end() - m.start()) * c, c, (m.end() - m.start()) * c)
    return '<path d="%s"/>' % d


# ------------------------------------------------------------------ logos
def signature(o: bytes):
    if o[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if o[:3] == b"\xff\xd8\xff":
        return "jpg"
    if o[:5] == b"%PDF-":
        return "pdf"
    return None


def ajuster(w: float, h: float, bw: float, bh: float):
    s = min(bw / w, bh / h)
    return w * s, h * s


def _raster(o: bytes, kind: str) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = PIXELS_MAX
    try:
        im = Image.open(io.BytesIO(o))
        if im.format != {"png": "PNG", "jpg": "JPEG"}[kind]:
            raise LogoRefuse("logo_refuse")
        if im.width * im.height > PIXELS_MAX or not im.width or not im.height:
            raise LogoRefuse("logo_enorme")
        im.load()
    except LogoRefuse:
        raise
    except Image.DecompressionBombError:
        raise LogoRefuse("logo_enorme")
    except Exception:
        raise LogoRefuse("logo_illisible")
    if kind == "jpg":
        im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if ("A" in im.mode or "transparency" in im.info) else "RGB")
    return im


def _page_pdf(o: bytes, bw: float, bh: float):
    """Page 1 d'un PDF, rastérisée à 600 ppp à la taille d'emplacement (aucun script : pypdfium2 sans formulaires). Renvoie (image, nombre de pages)."""
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(o)
        try:
            pages = len(pdf)
            if pages < 1:
                raise LogoRefuse("logo_illisible")
            page = pdf[0]
            w, h = page.get_size()
            if not (0 < w <= PAGE_PDF_MAX and 0 < h <= PAGE_PDF_MAX):
                raise LogoRefuse("logo_enorme")
            wmm, _ = ajuster(w, h, bw, bh)
            px = min(PX_MAX, wmm / 25.4 * DPI_RENDU)
            im = page.render(scale=px / w).to_pil().convert("RGB")  # fond blanc : un PDF de logo est un logo sur papier
        finally:
            pdf.close()
    except LogoRefuse:
        raise
    except Exception:
        raise LogoRefuse("logo_illisible")
    return im, pages


def _fond_non_blanc(im: Image.Image) -> bool:
    a = np.asarray(im)
    k = max(1, min(a.shape[0], a.shape[1]) // 50)
    anneau = np.ones(a.shape[:2], bool)
    anneau[k:-k, k:-k] = False
    px = a[anneau]
    if px.shape[1] == 4:
        px = px[px[:, 3] > 128]
    return bool(len(px)) and float(px[:, :3].mean(axis=0).min()) < 235


def preparer_logo(o: bytes, kind: str, bw: float, bh: float) -> dict:
    """PNG propre prêt à placer, mêmes proportions que l'original, jamais recoloré ni étiré ; `avert` : avertissements non bloquants."""
    if not isinstance(o, bytes) or len(o) > LOGO_OCTETS_MAX or signature(o) != kind:
        raise LogoRefuse("logo_refuse")  # fichier déguisé, trop gros, ou type annoncé différent des octets
    avert = []
    if kind == "pdf":
        im, pages = _page_pdf(o, bw, bh)
        if pages > 1:
            avert.append({"code": "pdf_multipage", "pages": pages})
    else:
        im = _raster(o, kind)
    wmm, hmm = ajuster(im.width, im.height, bw, bh)
    if kind != "pdf":
        dpi = int(im.width / (wmm / 25.4))
        if dpi < DPI_MIN:
            avert.append({"code": "resolution_faible", "dpi": dpi})
        cible = int(wmm / 25.4 * DPI_RENDU)
        if im.width > max(cible, 1) and im.width > 200:  # inutile au-delà de 600 ppp : fichier plus léger
            im = im.resize((cible, max(1, round(im.height * cible / im.width))), Image.LANCZOS)
    if _fond_non_blanc(im):
        avert.append({"code": "fond_non_blanc"})
    if hmm < 6 or wmm < 10:
        avert.append({"code": "rapport_extreme"})
    tampon = io.BytesIO()
    im.save(tampon, "PNG")
    return {"png": tampon.getvalue(), "w": im.width, "h": im.height, "avert": avert}


def boites(n: int, W: float, H: float, mx: float, my: float, ecart: float = 4.0):
    """Zone utile (marge latérale mx, verticale my) partagée en n cellules égales : 1 logo au milieu, 2 de part et d'autre de la ligne médiane,
    3 répartis régulièrement (le central sur la ligne médiane). Même hauteur utile pour tous ; `ecart` mm au moins entre deux logos."""
    cell = (W - 2 * mx) / n
    e = ecart if n > 1 else 0
    return [(mx + i * cell + e / 2, my, cell - e, H - 2 * my) for i in range(n)]


def svg_logos(logos: list, attrs: str):
    """Logos dans l'emplacement lu sur le groupe du canevas (data-largeur/hauteur/marge/marge-y) : centrés dans leur cellule, sans étirement."""
    W, H, m = _nombre(attrs, "data-largeur", 176), _nombre(attrs, "data-hauteur", 19), _nombre(attrs, "data-marge", 4)
    my = _nombre(attrs, "data-marge-y", m)
    sortie, avert = "", []
    cases = boites(len(logos), W, H, m, my) if logos else []
    for (idx, o, kind), (bx, by, bw, bh) in zip(logos, cases):
        try:
            p = preparer_logo(o, kind, bw, bh)
        except LogoRefuse as e:
            avert.append({"code": e.code, "logo": idx})
            continue
        avert += [{**a, "logo": idx} for a in p["avert"]]
        wmm, hmm = ajuster(p["w"], p["h"], bw, bh)
        x, y = bx + (bw - wmm) / 2, by + (bh - hmm) / 2
        sortie += '<image x="%.3f" y="%.3f" width="%.3f" height="%.3f" preserveAspectRatio="none" xlink:href="data:image/png;base64,%s"/>' % (x, y, wmm, hmm, base64.b64encode(p["png"]).decode())
    return sortie, avert


# ------------------------------------------------------------------ rendu
def _rsvg(svg: str, fmt: str, *extra: str) -> bytes:
    exe = shutil.which("rsvg-convert")
    if not exe:
        raise RuntimeError("rsvg-convert absent")
    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / "fonts.conf"  # seules les polices du dépôt sont connues : rendu identique partout, police manquante = repli visible
        conf.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>%s</dir><cachedir>%s/cache</cachedir></fontconfig>' % (POLICES, tmp))
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "FONTCONFIG_FILE": str(conf), "HOME": tmp, "XDG_CACHE_HOME": tmp, "LANG": "C.UTF-8"}
        r = subprocess.run([exe, "-f", fmt, "--dpi-x", "72", "--dpi-y", "72", *extra], input=svg.encode("utf-8"), capture_output=True, timeout=DELAI_RSVG, env=env, check=True)
    return r.stdout


def composer(tache: dict, canevas=None):
    """SVG final et avertissements. `tache` : code, mail, url (sans schéma), qr (lignes '0'/'1') et logos [{octets, kind}] ; mail, url, qr absents = « [À COMPLÉTER] »."""
    svg = lire_canevas(canevas)
    avert = []
    code = tache.get("code")
    if not isinstance(code, str) or not RE_CODE.match(code):
        raise ValueError("code")
    mail, url, qr = tache.get("mail"), tache.get("url"), tache.get("qr")
    if not (isinstance(mail, str) and RE_MAIL.match(mail)):
        mail = None
        avert.append({"code": "adresse_mail_absente"})
    if not (isinstance(url, str) and RE_URL.match(url)):
        url = None
        avert.append({"code": "adresse_web_absente"})
    for v, code_av in ((mail, "texte_trop_long"), (url, "texte_trop_long")):
        if v and len(v) > MAX_TEXTE:  # ne tiendrait pas dans l'encadré du canevas
            avert.append({"code": code_av})
            break
    g = _groupe(svg, "QR")
    cote = _nombre(g.group(1), "data-cote", 35)
    if mail and qr_valide(qr) and cote / len(qr) >= MODULE_MIN_MM:
        dessin = svg_qr(qr, cote)
    else:
        if mail and qr_valide(qr):
            avert.append({"code": "qr_trop_dense"})
        dessin = '<text x="%s" y="%s" font-family="DM Mono, monospace" font-size="3.4" text-anchor="middle">%s</text>' % (cote / 2, cote / 2, escape(A_COMPLETER))
    svg = svg[:g.start()] + g.group(0).replace("{{QR}}", dessin) + svg[g.end():]
    g = _groupe(svg, "LOGOS")
    logos = [(i, x["octets"], x["kind"]) for i, x in enumerate(tache.get("logos") or [])][:LOGOS_MAX]
    dessin, a = svg_logos(logos, g.group(1))
    avert += a + ([{"code": "aucun_logo"}] if not logos else [])
    svg = svg[:g.start()] + g.group(0).replace("{{LOGOS}}", dessin) + svg[g.end():]
    hote = url.split("/")[0] if url else A_COMPLETER  # pied du panneau (marqueur facultatif {{HOTE}})
    for m, v in (("CODE", code), ("MAIL", mail or A_COMPLETER), ("URL", url or A_COMPLETER), ("HOTE", hote)):
        svg = svg.replace("{{%s}}" % m, escape(v))
    return svg, avert


def produire(tache: dict, canevas=None) -> dict:
    """{'pdf': octets, 'png': octets, 'avertissements': [...]}. Erreur de canevas : exception CanevasInvalide (message en français)."""
    svg, avert = composer(tache, canevas)
    return {"pdf": _rsvg(svg, "pdf"), "png": _rsvg(svg, "png", "-w", "1000"), "avertissements": avert}


if __name__ == "__main__":
    # Essai local : python worker/panneau.py [canevas.svg] [dossier de sortie] — exemple de code et d'adresses, sans logo ; erreurs en clair.
    ch = sys.argv[1] if len(sys.argv) > 1 else None
    sortie = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
    try:
        r = produire({"code": "PYR001", "mail": "exemple@exemple.org", "url": "exemple.org/c/PYR001", "qr": None, "logos": []}, ch)
    except CanevasInvalide as e:
        sys.exit("Canevas refusé : %s" % e)
    (sortie / "essai-panneau.pdf").write_bytes(r["pdf"])
    (sortie / "essai-panneau.png").write_bytes(r["png"])
    print("écrit : essai-panneau.pdf et essai-panneau.png ; avertissements :", [a["code"] for a in r["avertissements"]] or "aucun")

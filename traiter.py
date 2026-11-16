"""Traitement d'une photo (cahier 9.2, étapes 1 à 5). Pur : octets en entrée, octets en sortie, aucun accès réseau ni fichier.
Seuils par défaut ; la table `settings` pourra les surcharger (paramètre `seuils`)."""
import io
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # HEIC indisponible : l'image sera refusée comme illisible
    pass

Image.MAX_IMAGE_PIXELS = 100_000_000  # parade aux bombes de décompression (13 bis.2 point 5)

SEUILS = {
    "flou": 60.0,           # variance du laplacien (image ramenée à 800 px de large) en dessous de laquelle la photo est floue
    "sombre": 28.0,         # luminance moyenne (0-255) en dessous de laquelle la photo est trop sombre
    "visage": 0.07,         # largeur d'un visage (part de la largeur de l'image) à partir de laquelle il est reconnaissable
    "hors_cadre": 0.25,     # similarité de contours minimale avec la référence
    "rotation": 3.0, "echelle": 0.05, "decalage": 0.04,  # limites de l'alignement (9.2 étape 4)
}
TAILLES = (640, 1280, 1600)


class Illisible(Exception):
    pass


def decoder(octets: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(octets))
        im = ImageOps.exif_transpose(im)  # orientation appliquée
        return im.convert("RGB")          # re-création de l'image : plus aucune métadonnée (GPS compris)
    except Exception as e:
        raise Illisible() from e


def _gris(im: Image.Image, largeur: int) -> np.ndarray:
    h = max(1, round(im.height * largeur / im.width))
    return np.asarray(im.resize((largeur, h)).convert("L"))


def visages_opencv(gris: np.ndarray):
    """Détecteur local (cascade de Haar fournie avec OpenCV, aucun service tiers) : liste de largeurs en pixels."""
    c = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return [int(w) for (_x, _y, w, _h) in c.detectMultiScale(gris, 1.1, 6, minSize=(24, 24))]


def _contours(im: Image.Image) -> np.ndarray:
    g = cv2.GaussianBlur(_gris(im.resize((64, 48)), 64), (5, 5), 0).astype(np.float32)
    m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return (m - m.mean()).ravel()


def similarite(a: Image.Image, b: Image.Image) -> float:
    x, y = _contours(a), _contours(b)
    d = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(x @ y / d) if d else 0.0


def aligner(im: Image.Image, ref: Image.Image, s: dict):
    """Recalage ORB + RANSAC, limité aux petites différences. Renvoie (image, score) ; en cas d'échec (image, None)."""
    try:
        t = 640
        a, b = _gris(im, t), _gris(ref, t)
        orb = cv2.ORB_create(1500)
        ka, da = orb.detectAndCompute(a, None)
        kb, db = orb.detectAndCompute(b, None)
        if da is None or db is None or len(ka) < 20 or len(kb) < 20:
            return im, None
        m = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db), key=lambda x: x.distance)[:300]
        if len(m) < 15:
            return im, None
        src = np.float32([ka[x.queryIdx].pt for x in m])
        dst = np.float32([kb[x.trainIdx].pt for x in m])
        mat, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if mat is None or inl is None:
            return im, None
        score = float(inl.sum() / len(m))
        echelle = math.hypot(mat[0, 0], mat[1, 0])
        angle = math.degrees(math.atan2(mat[1, 0], mat[0, 0]))
        dec = math.hypot(mat[0, 2], mat[1, 2]) / t
        if score < 0.3 or abs(echelle - 1) > s["echelle"] or abs(angle) > s["rotation"] or dec > s["decalage"]:
            return im, None
        k = im.width / t
        mat = mat.copy(); mat[:, 2] *= k
        out = cv2.warpAffine(np.asarray(im), mat, (im.width, im.height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return Image.fromarray(out), score
    except cv2.error:
        return im, None


def recadrer(im: Image.Image) -> Image.Image:
    """Recadrage centré 4:3."""
    w, h = im.size
    if w * 3 > h * 4:
        nw = h * 4 // 3; x = (w - nw) // 2
        return im.crop((x, 0, x + nw, h))
    nh = w * 3 // 4; y = (h - nh) // 2
    return im.crop((0, y, w, y + nh))


def variantes(im: Image.Image) -> dict:
    sortie = {}
    for w in TAILLES:
        v = im.resize((w, w * 3 // 4), Image.LANCZOS)
        for ext, fmt, opt in (("webp", "WEBP", {"quality": 82}), ("jpg", "JPEG", {"quality": 85, "optimize": True})):
            b = io.BytesIO(); v.save(b, fmt, **opt); sortie[f"{w}.{ext}"] = b.getvalue()
    b = io.BytesIO(); im.resize((480, 360), Image.LANCZOS).save(b, "JPEG", quality=80, optimize=True)
    sortie["thumb.jpg"] = b.getvalue()
    return sortie


def traiter(original: bytes, reference: bytes | None = None, seuils: dict | None = None, visages=visages_opencv) -> dict:
    """Renvoie { status, reasons, scores, variants (nom → octets), width, height }. `visages(gris)` est injectable (tests)."""
    s = {**SEUILS, **(seuils or {})}
    try:
        im = decoder(original)
    except Illisible:
        return {"status": "rejected", "reasons": ["format"], "scores": {}, "variants": {}}
    g = _gris(im, 800)
    lap = float(cv2.Laplacian(g, cv2.CV_64F).var())
    lum = float(g.mean())
    scores = {"quality_score": round(min(1.0, lap / (2 * s["flou"])), 3), "alignment_score": None, "has_faces": False}
    if lum < s["sombre"]:
        return {"status": "rejected", "reasons": ["trop_sombre"], "scores": scores, "variants": {}}
    if lap < s["flou"]:
        return {"status": "rejected", "reasons": ["floue"], "scores": scores, "variants": {}}
    gv = _gris(im, 1000)
    gros = [w for w in visages(gv) if w / gv.shape[1] >= s["visage"]]
    if gros:
        scores["has_faces"] = True
        return {"status": "rejected", "reasons": ["visage"], "scores": scores, "variants": {}}
    statut, motifs = "accepted", []
    if reference:
        try:
            ref = decoder(reference)
        except Illisible:
            ref = None
        if ref:
            if similarite(im, ref) < s["hors_cadre"]:
                statut, motifs = "pending", ["hors_cadre"]
            else:
                im, scores["alignment_score"] = aligner(im, ref, s)
    im = recadrer(im)
    return {"status": statut, "reasons": motifs, "scores": scores, "variants": variantes(im), "width": 1600, "height": 1200}


def reencoder(octets: bytes, cote_max: int = 1600) -> bytes:
    """Image d'actualité (V1.2 S3) : décodée puis recréée en JPEG (orientation appliquée, EXIF, GPS et profils effacés), côté long limité."""
    im = decoder(octets)
    im.thumbnail((cote_max, cote_max), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82, optimize=True, progressive=True)  # aucun paramètre exif= ni icc_profile= : rien n'est recopié
    return b.getvalue()

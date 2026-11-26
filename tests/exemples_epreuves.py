"""Logos synthétiques (large, carré, étroit) et ébauches d'exemple du panneau : sert aux tests et aux captures de la recette (V1.2 S4c).
Usage : python worker/tests/exemples_epreuves.py <dossier de sortie>   (écrit les 3 logos et les 3 ébauches en PNG et PDF)."""
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
POLICE = RACINE / "panneau" / "polices" / "figtree-latin-800-normal.ttf"
ARDOISE, LICHEN, ABRICOT, AZUR = (40, 54, 74), (196, 221, 184), (247, 217, 168), (186, 220, 240)


def _png(im: Image.Image) -> bytes:
    t = io.BytesIO()
    im.save(t, "PNG")
    return t.getvalue()


def logo_large() -> bytes:
    """1600 x 400 : bandeau de parc (rapport 4:1), fond blanc."""
    im = Image.new("RGB", (1600, 400), "white")
    d = ImageDraw.Draw(im)
    d.ellipse((20, 40, 340, 360), fill=LICHEN, outline=ARDOISE, width=10)
    d.polygon([(60, 300), (180, 120), (240, 210), (290, 150), (320, 300)], fill=ARDOISE)
    d.text((400, 70), "Parc naturel", font=ImageFont.truetype(str(POLICE), 150), fill=ARDOISE)
    d.text((400, 240), "des Hautes-Pyrénées", font=ImageFont.truetype(str(POLICE), 100), fill=ARDOISE)
    return _png(im)


def logo_carre() -> bytes:
    """900 x 900 : écusson carré, fond blanc."""
    im = Image.new("RGB", (900, 900), "white")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((40, 40, 860, 860), radius=120, fill=ABRICOT, outline=ARDOISE, width=16)
    d.ellipse((250, 120, 650, 520), fill=AZUR, outline=ARDOISE, width=14)
    d.text((450, 700), "RÉSERVE", font=ImageFont.truetype(str(POLICE), 140), fill=ARDOISE, anchor="mm")
    return _png(im)


def logo_etroit() -> bytes:
    """200 x 900 : oriflamme vertical (rapport 2:9), fond blanc."""
    im = Image.new("RGB", (200, 900), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 190, 890), fill=LICHEN, outline=ARDOISE, width=8)
    for i in range(6):
        d.ellipse((40, 60 + i * 130, 160, 160 + i * 130), fill=ARDOISE if i % 2 else ABRICOT, outline=ARDOISE, width=5)
    return _png(im)


if __name__ == "__main__":
    import panneau
    sortie = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    sortie.mkdir(parents=True, exist_ok=True)
    fixtures = json.loads((Path(__file__).parent / "qr_fixtures.json").read_text())["4219"]
    trois = [logo_large(), logo_carre(), logo_etroit()]
    for nom, f in (("large", [trois[0]]), ("carre", [trois[1]]), ("etroit", [trois[2]]), ("duo", trois[:2]), ("trio", trois)):
        if len(f) == 1:
            (sortie / f"logo-{nom}.png").write_bytes(f[0])
        r = panneau.produire({"code": "4219", "mail": fixtures["lien"][7:].split("?")[0], "url": "cairn-photo.fr/c/4219", "qr": fixtures["qr"], "logos": [{"octets": b, "kind": "png"} for b in f]})
        (sortie / f"epreuve-{nom}.pdf").write_bytes(r["pdf"])
        (sortie / f"epreuve-{nom}.png").write_bytes(r["png"])
        print(nom, [a["code"] for a in r["avertissements"]] or "aucun avertissement")

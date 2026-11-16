# Worker de traitement d'image (9.2, 13 bis.3) et d'ébauche de panneau (V1.2 S4c) — conteneur sans privilège, sans identifiant de base.
FROM python:3.12-slim
# rsvg-convert (librsvg) : SVG → PDF vectoriel et aperçu PNG du panneau, sur le seul modèle du dépôt (aucune adresse lue).
RUN apt-get update && apt-get install -y --no-install-recommends librsvg2-bin && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --no-create-home cairn
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py traiter.py panneau.py ./
COPY panneau ./panneau
USER 10001
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-I", "main.py"]

# Worker de traitement d'image (9.2, 13 bis.3) — conteneur sans privilège, sans identifiant de base.
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home cairn
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py traiter.py ./
USER 10001
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-I", "main.py"]

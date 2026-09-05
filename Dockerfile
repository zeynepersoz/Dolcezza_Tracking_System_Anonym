FROM python:3.12-slim

WORKDIR /app

RUN useradd --create-home --home-dir /home/appuser appuser
ENV HOME=/home/appuser

# fonts-dejavu-core: POD belgesindeki Turkce karakterler icin Unicode TTF
#   gerekiyor; python:slim imajinda hic font yoktur (bkz. FONT_CANDIDATES).
# tzdata: `timez.ZoneInfo("Europe/Istanbul")` saat dilimi veritabani ister.
#   Bugun /usr/share/zoneinfo bir bagimliligin yan urunu olarak var; acikca
#   istenmezse o bagimlilik degistigi gun panel saati sessizce patlar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DB'ler (~/.gls_pod) ve POD/etiket ciktilari icin kalici hacim noktalari
RUN mkdir -p /home/appuser/.gls_pod /data/output \
    && chown -R appuser:appuser /home/appuser /data /app

USER appuser

ENV OUTPUT_DIR=/data/output

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]

#!/usr/bin/env bash
# Sunucuya (192.0.2.50:8765) aktarim. Elle rsync YAZMAYIN.
#
# NEDEN BU DOSYA VAR: elle yazilan bir `rsync -a --delete` sunucudaki `data/`
# klasorunu (uc SQLite veritabani) ve `.env` dosyasini (gercek GLS kimlikleri)
# sildi/uzerine yazdi. Uygulama acilmadi, kimlikler mock'a dondu. Asagidaki uc
# haric tutma PAZARLIGA ACIK DEGILDIR:
#
#   data/    -> shipments.db, addressbook.db, auth.db  (yalnizca sunucuda yasar)
#   output/  -> POD ve etiket arsivi                   (yalnizca sunucuda yasar)
#   .env     -> gercek GLS kimlikleri; yereldeki kopya GLS_MODE=mock'tur
#
# Kullanim:  ./deploy.sh            aktar + imaji kur + yeniden baslat
#            ./deploy.sh --dry-run  yalnizca ne gonderilecegini goster
set -euo pipefail

HOST=glsvm
# Tirnak SART: tirnaksiz `~` YEREL ev dizinine acilir ve rsync olmayan bir yola yazmaya calisir.
REMOTE='~/gls-pod'

RSYNC_OPTS=(-az --delete --stats)
[[ "${1:-}" == "--dry-run" ]] && RSYNC_OPTS+=(--dry-run --itemize-changes)

rsync "${RSYNC_OPTS[@]}" \
  --exclude 'data/' --exclude 'output/' --exclude '.env' \
  --exclude '.git/' --exclude 'venv/' --exclude 'dotnet/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/' \
  --exclude '.gitignore' --exclude '.dockerignore' --exclude '.env.example' \
  --exclude '.claude/' \
  ./ "$HOST:$REMOTE/"

[[ "${1:-}" == "--dry-run" ]] && exit 0

ssh "$HOST" "cd $REMOTE && docker compose build web && docker compose up -d web"
sleep 8
ssh "$HOST" "curl -sf -o /dev/null -w 'health: %{http_code}\n' http://127.0.0.1:8765/health"

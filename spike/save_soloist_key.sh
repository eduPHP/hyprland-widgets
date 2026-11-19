#!/usr/bin/env bash
# Pede a chave do Soloist sem ecoar e grava em ~/.config/hyprspot/soloist.env (0600).
set -euo pipefail
read -rsp 'Cole a chave do Soloist e tecle Enter: ' K
echo
if [ -z "$K" ]; then echo "Chave vazia, nada gravado."; exit 1; fi
mkdir -p ~/.config/hyprspot
umask 077
printf 'SOLOIST_API_KEY=%s\n' "$K" > ~/.config/hyprspot/soloist.env
chmod 600 ~/.config/hyprspot/soloist.env
unset K
echo "OK: chave gravada em ~/.config/hyprspot/soloist.env"

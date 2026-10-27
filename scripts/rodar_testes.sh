#!/bin/bash
# Roda a suíte de testes (cada test_*.py num banco SQLite novo, com o seed) em paralelo.
# Uso: scripts/rodar_testes.sh [python]   (padrão: python)
# Sai com erro se algum teste falhar e mostra o final do log de cada falha.
PY="${1:-python}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
cd "$RAIZ"

rodar_um() {
  f="$1"; n="${f%.py}"
  export DATABASE_URL="sqlite:///$TMP/$n.db"
  "$PY" seed.py > "$TMP/$n.seed.log" 2>&1 || { echo "FALHOU(seed) $f"; return 1; }
  timeout 600 "$PY" -W ignore "$f" > "$TMP/$n.log" 2>&1 && echo "OK $f" || echo "FALHOU $f"
}
export -f rodar_um
export PY TMP

ls test_*.py | xargs -P "${PARALELO:-4}" -I{} bash -c 'rodar_um {}' | tee "$TMP/resultado.txt"

total=$(grep -c . "$TMP/resultado.txt")
ok=$(grep -c '^OK' "$TMP/resultado.txt")
echo "==== $ok de $total testes passaram"
if [ "$ok" -ne "$total" ]; then
  for f in $(grep '^FALHOU' "$TMP/resultado.txt" | awk '{print $2}'); do
    n="${f%.py}"
    echo "---- $f (final do log)"; tail -25 "$TMP/$n.log" 2>/dev/null || tail -25 "$TMP/$n.seed.log"
  done
  exit 1
fi

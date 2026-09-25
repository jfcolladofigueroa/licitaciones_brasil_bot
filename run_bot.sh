#!/bin/bash
# ============================================================
#  Lanzador del bot para launchd (ejecución automática diaria)
#  Resuelve el directorio de trabajo, el python3 y deja log.
# ============================================================

cd "$(dirname "$0")" || exit 1

LOG="logs/monitor_$(date +%Y-%m-%d).log"
mkdir -p logs

# Resolver python3 (launchd arranca con un PATH mínimo)
# El venv del bot es el que tiene las dependencias (requests, etc.). Usarlo
# SIEMPRE que exista: el python del sistema falla con ModuleNotFoundError.
PY=""
for c in ./licitaciones_env/bin/python3 ./licitaciones_env/bin/python \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -x "$c" ] && PY="$c" && break
done
[ -z "$PY" ] && PY="$(command -v python3)"
[ -z "$PY" ] && { echo "$(date '+%F %T') ❌ python3 no encontrado" >> "$LOG"; exit 1; }

if ! "$PY" -c "import requests" 2>/dev/null; then
    echo "$(date '+%F %T') ❌ $PY no tiene 'requests' — instala con: ./licitaciones_env/bin/pip install requests" >> "$LOG"
    exit 1
fi

{
    echo ""
    echo "════════════════════════════════════════════════════════"
    echo "  INICIO  $(date '+%F %T')   ($PY)"
    echo "════════════════════════════════════════════════════════"
} >> "$LOG"

# Integridad BD ↔ disco: si una carpeta de descargas se borró, la BD no puede
# seguir afirmando que hay documentos. Ver verificar_integridad.py (caso Poá).
"$PY" verificar_integridad.py >> "$LOG" 2>&1

"$PY" monitor.py >> "$LOG" 2>&1
CODIGO=$?

echo "$(date '+%F %T')  FIN — código de salida: $CODIGO" >> "$LOG"

# Limpieza de disco: borra lo cerrado y lo que está fuera de perfil
if [ -x "./limpiar_disco.sh" ]; then
    ./limpiar_disco.sh --no-software --borrar >> "$LOG" 2>&1
fi

# Conservar solo los últimos 30 logs
ls -1t logs/monitor_*.log 2>/dev/null | tail -n +31 | xargs -I{} rm -f {} 2>/dev/null

exit $CODIGO

#!/bin/bash
# ============================================================
#  Descarga + extrae texto de un lote de licitaciones concretas
#  Uso:  ./bajar_lote.sh              (usa la lista de abajo)
#        ./bajar_lote.sh PNCP-xxx ... (ids sueltos)
# ============================================================

cd "$(dirname "$0")" || exit 1

# El venv del bot es el que tiene requests instalado. Usarlo siempre que exista;
# el python del sistema no trae las dependencias y falla con ModuleNotFoundError.
PY=""
for c in ./licitaciones_env/bin/python3 ./licitaciones_env/bin/python \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -x "$c" ] && PY="$c" && break
done
[ -z "$PY" ] && PY="$(command -v python3)"
[ -z "$PY" ] && { echo "❌ python3 no encontrado"; exit 1; }

if ! "$PY" -c "import requests" 2>/dev/null; then
    echo "❌ $PY no tiene 'requests'."
    echo "   Instálalo con:  ./licitaciones_env/bin/pip install requests"
    exit 1
fi
echo "🐍 $PY"

# Lista por defecto — pendientes de analizar (07/09)
# Estas 5 no traen archivos por la API del PNCP: si vuelven a fallar,
# hay que abrirlas a mano en el navegador desde la URL del edital.
DEFAULT_IDS=(
  "PNCP-08434600000170-2026-41"    # Barueri/SP     · chatbot IPRESB        · 14/09 · R$95.947
  "PNCP-24472003000196-2026-18"    # Rio Largo/AL   · GED legislativo + IA  · 15/09 · R$119.242
  "PNCP-75968412000119-2026-78"    # C. Mairinck/PR · cesta de preços       · 16/09 · R$8.520
  "PNCP-77926509000194-2026-38"    # Maringá/PR     · clipping de medios    · 21/09 · R$65.199
  "PNCP-76898196000145-2026-9"     # Pato Branco/PR · colaboración + correo · 25/09 · R$38.537
)

if [ $# -gt 0 ]; then IDS=("$@"); else IDS=("${DEFAULT_IDS[@]}"); fi

for id in "${IDS[@]}"; do
    echo ""
    echo "════════════════════════════════════════════════════════"
    echo "  $id"
    echo "════════════════════════════════════════════════════════"
    "$PY" descargador_documentos.py "$id" || { echo "⚠️  descarga falló"; continue; }
    "$PY" extractor_texto.py "descargas/$id" >/dev/null 2>&1 \
        && echo "✅ texto extraído" \
        || echo "⚠️  extracción de texto falló"
done

echo ""
echo "Listo. Carpetas en: $(pwd)/descargas/"

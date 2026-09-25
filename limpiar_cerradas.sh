#!/bin/bash
# ============================================================
#  Limpieza de carpetas de licitaciones YA CERRADAS
#  Generado: 05/08/2026
#
#  Borra las carpetas de descargas/ cuyo prazo de propostas
#  ya venció (data_encerramento < hoy), según licitacoes.db.
#  Las licitaciones VIGENTES se conservan intactas.
#
#  Uso:
#     cd ~/Documents/Programacion/Personal/tools_licitaciones/bot_licitaciones
#     chmod +x limpiar_cerradas.sh
#     ./limpiar_cerradas.sh            # simulación (no borra nada)
#     ./limpiar_cerradas.sh --borrar   # borra de verdad
# ============================================================

cd "$(dirname "$0")" || exit 1

MODO="${1:-simular}"

python3 - "$MODO" <<'PY'
import sqlite3, os, sys, datetime, shutil

modo = sys.argv[1] if len(sys.argv) > 1 else "simular"
borrar_de_verdad = (modo == "--borrar")

DB = "licitacoes.db"
BASE = "descargas"
hoy = datetime.date.today()

if not os.path.exists(DB):
    print("❌ No encuentro licitacoes.db"); sys.exit(1)

con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("SELECT id, data_encerramento FROM licitacoes")
enc = {i: (e or "")[:10] for i, e in cur.fetchall()}
con.close()

carpetas = [d for d in os.listdir(BASE)
            if d.startswith("PNCP-") and os.path.isdir(os.path.join(BASE, d))]

cerradas, vigentes, sin_fecha = [], [], []
for d in carpetas:
    e = enc.get(d, "")
    if not e:
        sin_fecha.append(d); continue
    try:
        (cerradas if datetime.date.fromisoformat(e) < hoy else vigentes).append(d)
    except ValueError:
        sin_fecha.append(d)

def tamano_mb(lista):
    total = 0
    for d in lista:
        for raiz, _, ficheros in os.walk(os.path.join(BASE, d)):
            for f in ficheros:
                try: total += os.path.getsize(os.path.join(raiz, f))
                except OSError: pass
    return total / 1024 / 1024

mb = tamano_mb(cerradas)

print("=" * 58)
print(f"  Carpetas totales      : {len(carpetas)}")
print(f"  CERRADAS (a borrar)   : {len(cerradas)}  →  {mb:,.0f} MB")
print(f"  VIGENTES (se guardan) : {len(vigentes)}")
print(f"  Sin fecha (se guardan): {len(sin_fecha)}")
print("=" * 58)

if not borrar_de_verdad:
    print("\n🔍 MODO SIMULACIÓN — no se ha borrado nada.")
    print("   Para borrar de verdad ejecuta:  ./limpiar_cerradas.sh --borrar\n")
    for d in cerradas[:15]:
        print(f"   - {d}  (encerrou {enc.get(d)})")
    if len(cerradas) > 15:
        print(f"   ... y {len(cerradas) - 15} más")
    sys.exit(0)

print(f"\n🗑️  Borrando {len(cerradas)} carpetas...")
ok = fallos = 0
for i, d in enumerate(cerradas, 1):
    try:
        shutil.rmtree(os.path.join(BASE, d)); ok += 1
    except Exception as e:
        fallos += 1
        if fallos <= 5: print(f"   ⚠️  {d}: {e}")
    if i % 100 == 0:
        print(f"   ... {i}/{len(cerradas)}")

print(f"\n✅ Borradas: {ok}   ⚠️ Fallos: {fallos}")
print(f"💾 Espacio liberado: ~{mb:,.0f} MB")
PY

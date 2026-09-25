#!/bin/bash
# ============================================================
#  LIMPIEZA DE DISCO — descargas/
#  Sustituye a limpiar_cerradas.sh (más completo).
#
#  Borra:
#   1. Carpetas de licitaciones CERRADAS (data_encerramento < hoy)
#   2. Carpetas SIN FECHA en la BD descargadas hace más de N días
#      (por defecto 25 — el bot busca con horizonte de 20 días,
#       así que algo descargado hace 25+ días ya venció)
#   3. Carpetas huérfanas (no están en la BD)
#
#  NUNCA borra:
#   - Licitaciones vigentes
#   - Los IDs de la LISTA BLANCA (abajo)
#
#   4. (opcional, --no-software) Todo lo que NO sea del perfil
#      de software/TI, esté vigente o no. Es donde está el grueso.
#
#  Uso:
#     ./limpiar_disco.sh                        # simulación
#     ./limpiar_disco.sh --borrar               # borra de verdad
#     ./limpiar_disco.sh --dias 15              # cambia el umbral
#     ./limpiar_disco.sh --no-software          # simula limpieza profunda
#     ./limpiar_disco.sh --no-software --borrar # limpieza profunda real
# ============================================================

cd "$(dirname "$0")" || exit 1

python3 - "$@" <<'PY'
import os, sys, time, shutil, sqlite3, datetime, re, unicodedata

args = sys.argv[1:]
BORRAR = "--borrar" in args
NOSW = "--no-software" in args
DIAS = 25
if "--dias" in args:
    try: DIAS = int(args[args.index("--dias") + 1])
    except (IndexError, ValueError): pass

DB, BASE = "licitacoes.db", "descargas"


def norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


RX_SOFTWARE = re.compile(
    r"desenvolvimento de (software|sistema|aplica)|fabrica de software|ponto de funcao"
    r"|licenciamento de (software|sistema)|sistema (informatizado|integrado|de gestao|web)"
    r"|software de gestao|cesta[s]? de preco|pesquisa de preco|formacao de preco"
    r"|chatbot|canal de denuncia|ouvidoria|portal da transparencia|aplicativo"
    r"|plataforma digital|locacao de (software|sistema)|cessao de uso de (software|sistema)"
    r"|solucao tecnologica|saas|software as a service|atendimento (virtual|automatizado)"
    r"|gestao eletronica de documento|whatsapp|software|licenca de uso|erp ",
    re.I,
)

# --- IDs que NUNCA se borran (licitaciones en curso o de interés) ---
LISTA_BLANCA = {
    "PNCP-01073089000189-2026-51",   # Caldas Novas/GO — presentada
    "PNCP-03043283000147-2026-23",   # Bela Vista de Goiás/GO — presentada
    "PNCP-04768671000158-2026-19",   # Maceió/AL — chatbot omnicanal
    "PNCP-11762128000109-2026-8",    # Macapá/AP — chatbot
}

if not os.path.exists(DB):
    sys.exit("❌ licitacoes.db não encontrado. Corre a partir de bot_licitaciones/")

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("SELECT id, data_encerramento, objeto FROM licitacoes")
filas = cur.fetchall()
con.close()
enc = {i: (e or "")[:10] for i, e, _ in filas}
obj = {i: (o or "") for i, _, o in filas}

hoy = datetime.date.today()
ahora = time.time()

cerradas, vigentes, viejas_sinfecha, recientes_sinfecha = [], [], [], []
huerfanas, protegidas, fuera_perfil = [], [], []

for d in sorted(os.listdir(BASE)):
    p = os.path.join(BASE, d)
    if not d.startswith("PNCP-") or not os.path.isdir(p):
        continue
    if d in LISTA_BLANCA:
        protegidas.append(d); continue
    if d not in enc:
        huerfanas.append(d); continue
    if NOSW and not RX_SOFTWARE.search(norm(obj.get(d, ""))):
        fuera_perfil.append(d); continue
    e = enc[d]
    if e:
        try:
            if datetime.date.fromisoformat(e) < hoy:
                cerradas.append(d)
            else:
                vigentes.append(d)
            continue
        except ValueError:
            pass
    # sin fecha válida -> decide por antigüedad da pasta
    try:
        edad = (ahora - os.path.getmtime(p)) / 86400
    except OSError:
        edad = 0
    (viejas_sinfecha if edad > DIAS else recientes_sinfecha).append(d)


def tamano_mb(lista):
    total = 0
    for d in lista:
        for raiz, _, ficheros in os.walk(os.path.join(BASE, d)):
            for f in ficheros:
                try: total += os.path.getsize(os.path.join(raiz, f))
                except OSError: pass
    return total / 1024 / 1024


a_borrar = cerradas + viejas_sinfecha + huerfanas + fuera_perfil
mb_borrar = tamano_mb(a_borrar)

print("=" * 62)
modo = "PROFUNDA (inclui fora de perfil)" if NOSW else "normal"
print(f"  LIMPIEZA DE DISCO — {modo} · umbral sin fecha: {DIAS}d")
print("=" * 62)
print(f"  🗑️  Cerradas (venceram)        : {len(cerradas):>5}")
print(f"  🗑️  Sin fecha e antigas        : {len(viejas_sinfecha):>5}")
print(f"  🗑️  Órfãs (não estão na BD)    : {len(huerfanas):>5}")
if NOSW:
    print(f"  🗑️  Fora do perfil (não é TI)  : {len(fuera_perfil):>5}")
print(f"      {'─' * 40}")
print(f"  🗑️  TOTAL A BORRAR             : {len(a_borrar):>5}  →  {mb_borrar:,.0f} MB")
print()
print(f"  ✅ Vigentes (guardadas)        : {len(vigentes):>5}")
print(f"  ✅ Sin fecha recentes (<{DIAS}d)  : {len(recientes_sinfecha):>5}")
print(f"  🔒 Lista branca                : {len(protegidas):>5}")
print("=" * 62)

if not BORRAR:
    print("\n🔍 SIMULACIÓN — no se ha borrado nada.")
    extra = " --no-software" if NOSW else ""
    print(f"   Para borrar:  ./limpiar_disco.sh --dias {DIAS}{extra} --borrar\n")
    for d in a_borrar[:12]:
        motivo = ("cerrada" if d in cerradas else
                  "sin fecha, antigua" if d in viejas_sinfecha else
                  "fora de perfil" if d in fuera_perfil else "órfã")
        print(f"   - {d}  ({motivo})")
    if len(a_borrar) > 12:
        print(f"   ... y {len(a_borrar) - 12} más")
    sys.exit(0)

print(f"\n🗑️  Borrando {len(a_borrar)} carpetas...")
ok = fallos = 0
for i, d in enumerate(a_borrar, 1):
    try:
        shutil.rmtree(os.path.join(BASE, d)); ok += 1
    except Exception as e:
        fallos += 1
        if fallos <= 5: print(f"   ⚠️  {d}: {e}")
    if i % 200 == 0:
        print(f"   ... {i}/{len(a_borrar)}")

print(f"\n✅ Borradas: {ok}   ⚠️ Fallos: {fallos}")
print(f"💾 Espacio liberado: ~{mb_borrar:,.0f} MB")

# Marcar en la BD lo que se acaba de borrar. Sin esto se entra en un bucle:
# limpiar_disco borra la carpeta → verificar_integridad ve que falta y la pone
# de nuevo en cola (docs_baixados=0) → el monitor la vuelve a descargar → y
# limpiar_disco la borra otra vez. Estado 2 = "descargada y purgada a propósito".
try:
    cur.executemany(
        "UPDATE licitacoes SET docs_baixados = 2 WHERE id = ?",
        [(d,) for d in a_borrar],
    )
    con.commit()
    print(f"🗂️  {len(a_borrar)} marcadas como purgadas en la BD (no se redescargan)")
except Exception as e:
    print(f"   ⚠️  no se pudo marcar en la BD: {e}")
PY

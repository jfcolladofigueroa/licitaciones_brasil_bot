"""
RELLENO DE data_encerramento
=============================
Problema que resuelve: 2.414 de 12.063 filas de `licitacoes` no tienen fecha de
cierre de propuestas. Sin ella el bot está ciego: no sabe qué priorizar, qué
purgar ni qué sigue abierto, y los scripts de análisis las descartaban en
silencio (viables_ahora.py, limpiar_cerradas.sh, el orden de la cola...).

De dónde salió el agujero: en agosto de 2026 el índice de /contratacoes/proposta
del PNCP se cayó y el bot tiró del fallback /contratacoes/publicacao. Ese camino
conserva a propósito los registros cuyo `dataEncerramentoProposta` viene vacío
(mejor tenerlos sin fecha que perderlos), y `salvar_licitacao()` es INSERT-only:
nunca vuelve a tocar una fila existente. Resultado: el NULL se fosilizó.
2.242 de las 2.414 filas ciegas son de ese único mes.

Estrategia, de lo barato a lo caro:
    1. Barrido de /contratacoes/proposta: ~320 páginas cubren TODAS las que
       siguen abiertas. Una petición por página en vez de una por licitación.
    2. Detalle por compra (/orgaos/{cnpj}/compras/{ano}/{seq}) para el resto.
       Es la única vía que responde para licitaciones ya cerradas, que son la
       mayoría de las huérfanas.

Idempotente: solo lee filas con data_encerramento IS NULL y solo escribe cuando
el PNCP devuelve una fecha, así que se puede relanzar las veces que haga falta.
Las que el PNCP tampoco sabe (contrataciones directas sin plazo publicado) se
quedarán NULL para siempre y se reportan aparte: son ruido esperado, no un fallo.

Uso:
    python rellenar_fechas.py --dry-run          # enseña qué haría, no toca nada
    python rellenar_fechas.py                    # rellena de verdad
    python rellenar_fechas.py --limite 200       # tanteo corto
    python rellenar_fechas.py --sin-barrido      # salta el paso 1 (solo detalle)
    python rellenar_fechas.py --db /tmp/prueba.db  # contra una copia
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from apis_licitacoes import PNCP_API

BASE = Path(__file__).resolve().parent
DB = BASE / "licitacoes.db"

# Las mismas que barre el bot en su día a día (ver BuscadorLicitacoes.buscar).
MODALIDADES = [6, 8]  # 6=Pregão Eletrônico, 8=Dispensa
# Horizonte del barrido. Generoso a propósito: hay dispensas con cierre a más de
# un año vista (el ejemplo de Ladário/MS cerraba 12 meses después de publicarse).
DIAS_ADELANTE = 400


def _pendientes(con, limite=None):
    """Filas ciegas, las más recientes primero: son las que aún pueden servir."""
    sql = ("SELECT id FROM licitacoes WHERE data_encerramento IS NULL "
           "ORDER BY data_encontrada DESC")
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [f[0] for f in con.execute(sql).fetchall()]


def _grabar(con, id_lic, fecha, aplicar):
    """
    COALESCE defensivo: si otra ejecución en paralelo ya puso fecha, no la
    pisamos. Sin él, dos pasadas simultáneas podrían escribir valores distintos.
    """
    if not aplicar:
        return
    con.execute(
        "UPDATE licitacoes SET data_encerramento = COALESCE(data_encerramento, ?) "
        "WHERE id = ?",
        (fecha, id_lic),
    )
    con.commit()


def rellenar(db=DB, aplicar=True, limite=None, con_barrido=True) -> dict:
    con = sqlite3.connect(db, timeout=30)
    pendientes = _pendientes(con, limite)
    total = len(pendientes)
    print(f"🕳️  Filas sin data_encerramento: {total}", flush=True)
    if not total:
        con.close()
        return {"pendientes": 0, "por_barrido": 0, "por_detalle": 0, "sin_fecha": 0}

    api = PNCP_API()
    por_barrido = por_detalle = 0
    faltan = set(pendientes)

    # ---- Paso 1: barrido de las que siguen abiertas -----------------------
    if con_barrido:
        data_final = (datetime.now() + timedelta(days=DIAS_ADELANTE)).strftime("%Y%m%d")
        for mod in MODALIDADES:
            print(f"🔍 Barrido /proposta modalidade {mod} (hasta {data_final})...",
                  flush=True)
            abiertas = api.buscar_propostas_abertas(
                data_final=data_final, codigo_modalidade=mod
            )
            print(f"   {len(abiertas)} contrataciones abiertas leídas", flush=True)
            for lic in abiertas:
                if lic.id in faltan and lic.data_encerramento:
                    _grabar(con, lic.id, lic.data_encerramento, aplicar)
                    faltan.discard(lic.id)
                    por_barrido += 1
        print(f"✅ Rellenadas por barrido: {por_barrido}", flush=True)

    # ---- Paso 2: detalle compra a compra ----------------------------------
    restantes = [i for i in pendientes if i in faltan]
    sin_fecha = 0
    for n, id_lic in enumerate(restantes, 1):
        try:
            _, cnpj, ano, seq = id_lic.split("-")
        except ValueError:
            sin_fecha += 1  # id fuera del formato PNCP-<cnpj>-<ano>-<seq>
            continue

        dados = api.consultar_compra(cnpj, ano, seq)
        fecha = ((dados or {}).get("dataEncerramentoProposta") or "")[:10] or None
        if fecha:
            _grabar(con, id_lic, fecha, aplicar)
            por_detalle += 1
        else:
            sin_fecha += 1

        if n % 100 == 0:
            print(f"   [{n}/{len(restantes)}] detalle: {por_detalle} ok, "
                  f"{sin_fecha} sin fecha", flush=True)
        time.sleep(0.3)  # pausa cortés: el PNCP devuelve 429 con poco más ritmo

    quedan = con.execute(
        "SELECT COUNT(*) FROM licitacoes WHERE data_encerramento IS NULL"
    ).fetchone()[0]
    con.close()

    return {
        "pendientes": total,
        "por_barrido": por_barrido,
        "por_detalle": por_detalle,
        "sin_fecha": sin_fecha,
        "siguen_null": quedan,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Rellena data_encerramento en licitacoes.db")
    p.add_argument("--dry-run", action="store_true", help="no escribe nada")
    p.add_argument("--limite", type=int, help="procesar solo N filas")
    p.add_argument("--sin-barrido", action="store_true",
                   help="saltar el barrido /proposta, ir directo al detalle")
    p.add_argument("--db", default=str(DB), help="ruta de la BD (para pruebas)")
    args = p.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"❌ No existe la BD: {args.db}")

    r = rellenar(db=args.db, aplicar=not args.dry_run, limite=args.limite,
                 con_barrido=not args.sin_barrido)

    print("\n📅 Relleno de data_encerramento" + (" (simulación)" if args.dry_run else ""))
    print(f"   filas ciegas al empezar : {r['pendientes']}")
    print(f"   rellenadas por barrido  : {r['por_barrido']}")
    print(f"   rellenadas por detalle  : {r['por_detalle']}")
    print(f"   el PNCP tampoco la tiene: {r.get('sin_fecha', 0)}")
    print(f"   siguen NULL en la BD    : {r.get('siguen_null', '?')}")
    if args.dry_run:
        print("\n   (simulación: no se ha escrito nada; 'siguen NULL' es el estado actual)")

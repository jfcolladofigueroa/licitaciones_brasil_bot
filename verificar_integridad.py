"""
VERIFICADOR DE INTEGRIDAD BD ↔ DISCO
=====================================
Problema que resuelve: la columna `docs_baixados` se marca a 1 cuando el bot
descarga los documentos, pero NUNCA vuelve atrás si la carpeta se borra —ni por
`limpiar_disco.sh`, ni por un borrado manual de `descargas/`—. Resultado: la BD
afirma que hay documentos que no existen, y el análisis se hace de memoria.

Coste real de ese desajuste: Poá/SP (PE 015/2026, R$77.268) estuvo semanas
marcada como "sin muro económico" sin que nadie pudiera comprobarlo, porque los
documentos se habían borrado. El edital exigía ILG ≥ 1,00, ILC ≥ 1,00 y
GE ≤ 0,80 —los tres imposibles— y el pregoeiro negó por escrito la sustitución
por patrimônio líquido o capital social.

Estados de `docs_baixados`:
    0 = sin documentos, hay que descargarlos (el monitor los reintenta)
    1 = documentos presentes en disco
    2 = documentos descargados y purgados; licitación cerrada, no reintentar
"""

import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "licitacoes.db"
DESCARGAS = BASE / "descargas"


def _tiene_documentos(carpeta: Path) -> bool:
    """Una carpeta cuenta como válida si existe y tiene algún archivo real."""
    if not carpeta.is_dir():
        return False
    return any(f.is_file() and not f.name.startswith("_") for f in carpeta.iterdir())


def verificar(aplicar: bool = True, hoy: str | None = None) -> dict:
    hoy = hoy or date.today().isoformat()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    filas = con.execute(
        "SELECT id, docs_baixados, data_encerramento FROM licitacoes"
    ).fetchall()

    a_cero, a_dos, a_uno = [], [], []
    for f in filas:
        presente = _tiene_documentos(DESCARGAS / f["id"])
        estado = f["docs_baixados"]
        cerrada = bool(f["data_encerramento"]) and f["data_encerramento"] < hoy

        if presente and estado != 1:
            a_uno.append(f["id"])            # están en disco: marcarlas bien
        elif not presente and estado == 1:
            # la BD miente: decidir si merece la pena volver a bajarlas
            (a_dos if cerrada else a_cero).append(f["id"])

    if aplicar:
        for ids, valor in ((a_uno, 1), (a_cero, 0), (a_dos, 2)):
            con.executemany(
                "UPDATE licitacoes SET docs_baixados = ? WHERE id = ?",
                [(valor, i) for i in ids],
            )
        con.commit()

    pendientes = con.execute(
        "SELECT COUNT(*) FROM licitacoes "
        "WHERE docs_baixados = 0 AND (data_encerramento IS NULL OR data_encerramento >= ?)",
        (hoy,),
    ).fetchone()[0]
    con.close()

    return {
        "corregidas_a_1": len(a_uno),
        "corregidas_a_0": len(a_cero),
        "marcadas_purgadas": len(a_dos),
        "pendientes_de_descarga": pendientes,
    }


if __name__ == "__main__":
    solo_ver = "--dry-run" in sys.argv
    r = verificar(aplicar=not solo_ver)
    print("🔍 Integridad BD ↔ disco" + (" (simulación)" if solo_ver else ""))
    print(f"   presentes marcadas mal → 1 : {r['corregidas_a_1']}")
    print(f"   ausentes y aún abiertas → 0 : {r['corregidas_a_0']}  (se redescargan)")
    print(f"   ausentes y ya cerradas → 2 : {r['marcadas_purgadas']}")
    print(f"   pendientes de descarga     : {r['pendientes_de_descarga']}")
    if r["corregidas_a_0"]:
        print("\n⚠️  Había licitaciones ABIERTAS marcadas como descargadas sin")
        print("   documentos en disco. No analizar ninguna de ellas hasta bajarlas.")

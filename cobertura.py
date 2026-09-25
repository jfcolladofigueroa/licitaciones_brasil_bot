#!/usr/bin/env python3
"""
cobertura.py — ¿qué licitaciones hay que NO hemos visto?

Escrito el 19/09/2026, y la razón está en una frase de Jose:

    "lo importante es NO PERDER licitaciones. Luego podemos hacer 'malha fina'
     pero claro, si perdemos es que estamos mal"

Tenía razón, y los datos de ese día se la dieron: los cinco fallos que
arreglamos —texto cortado por el principio, el veto que mataba antes de mirar,
la primera coincidencia negada, los patrones sin acentos, las siete familias
que faltaban— eran TODOS falsos negativos. El sistema estaba construido para
descartar, y descartaba de más en silencio.

`viables_ahora.py` responde a "¿cuáles puedo hacer?". Este programa responde a
la otra pregunta, que es la que duele: "¿cuáles se me están escapando?".

Cuatro bloques, de más urgente a menos:

  1. CIEGAS Y CERRANDO  — de nuestras familias, cierran pronto y no tenemos el
                          texto. Es la lista de las que se pierden.
  2. SALUD DEL BARRIDO  — ¿corrió el bot? ¿cuántas páginas falló? Una página
                          fallida son hasta 50 contrataciones no vistas.
  3. HUECOS EN DISCO    — sin carpeta, carpeta vacía, texto vacío, texto
                          recortado.
  4. YA CERRADAS SIN MIRAR — el cementerio. Lo que se fue sin que lo viéramos.
                          Duele, pero es el único número que mide de verdad.

Uso:
    python3 cobertura.py                 informe completo
    python3 cobertura.py --dias 10       horizonte de alarma (por defecto 7)
    python3 cobertura.py --breve         solo el bloque 1, para el día a día
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from medir_plataformas import DESCARGAS, familia, detectar_plataforma

BASE = Path(__file__).resolve().parent
DB = BASE / "licitacoes.db"
LOGS = BASE / "logs"

# Un expediente cuyo texto extraído es menor que esto es sospechoso: un edital
# de verdad no baja de unos pocos miles de caracteres.
MIN_CHARS_CREIBLE = 3000


# ─────────────────────────────────────────────────────────────────────────────
def _texto_de(lic_id: str) -> str | None:
    f = DESCARGAS / lic_id / "_texto_extraido.txt"
    if not f.is_file():
        return None
    try:
        t = f.read_text(errors="ignore")
    except OSError:
        return None
    return t if t.strip() else None


def _estado_disco(lic_id: str) -> str:
    """Clasifica qué tenemos en disco de un expediente."""
    carpeta = DESCARGAS / lic_id
    if not carpeta.is_dir():
        return "sin carpeta"
    docs = [f for f in carpeta.iterdir() if not f.name.startswith("_")]
    if not docs:
        alt = carpeta / "_FUENTES_ALTERNATIVAS.txt"
        return "sin documentos (hay enlaces alternativos)" if alt.is_file() \
            else "carpeta vacía"
    t = _texto_de(lic_id)
    if t is None:
        return "documentos sin texto extraíble"
    if "[... RECORTE:" in t:
        return "texto recortado"
    if len(t) < MIN_CHARS_CREIBLE:
        return f"texto sospechosamente corto ({len(t)} chars)"
    return "ok"


# ─────────────────────────────────────────────────────────────────────────────
def bloque_ciegas(cur, dias: int) -> list:
    """Lo único que de verdad importa: de mi familia, cierra pronto, no la veo."""
    hoy = date.today().isoformat()
    limite = (date.today() + timedelta(days=dias)).isoformat()
    cur.execute(
        "select * from licitacoes where data_encerramento >= ? "
        "and data_encerramento <= ? order by data_encerramento", (hoy, limite))
    fuera = []
    for f in cur.fetchall():
        fam = familia(f["objeto"] or "")
        if not fam:
            continue
        estado = _estado_disco(f["id"])
        if estado == "ok":
            continue
        fuera.append((f, fam, estado))
    return fuera


def bloque_barrido() -> dict:
    """¿Corrió el bot y qué se dejó por el camino?"""
    hoy = date.today().isoformat()
    log = LOGS / f"monitor_{hoy}.log"
    if not log.is_file():
        # ¿cuál fue la última vez?
        anteriores = sorted(LOGS.glob("monitor_*.log"))
        ultimo = anteriores[-1].name.replace("monitor_", "").replace(".log", "") \
            if anteriores else None
        return {"corrio": False, "ultimo_dia": ultimo}

    txt = log.read_text(errors="replace")
    paginas_ok = len(re.findall(r"Página \d+(?:/\d+)?\.\.\..*registros", txt))
    paginas_mal = len(re.findall(r"omitida|⏭️", txt))
    return {
        "corrio": True,
        "termino": "FIN —" in txt,
        "paginas_ok": paginas_ok,
        "paginas_mal": paginas_mal,
        "registros_perdidos_max": paginas_mal * 50,
        "api_cero": "RETORNOU ZERO RESULTADOS" in txt,
        "timeouts": txt.count("timeout, reintentando"),
        "dns": txt.count("Failed to resolve"),
    }


def bloque_huecos(cur) -> dict:
    """Estado de disco de todo lo abierto de nuestras familias."""
    hoy = date.today().isoformat()
    cur.execute("select * from licitacoes where data_encerramento >= ?", (hoy,))
    conteo: dict[str, int] = {}
    for f in cur.fetchall():
        if not familia(f["objeto"] or ""):
            continue
        estado = _estado_disco(f["id"])
        conteo[estado] = conteo.get(estado, 0) + 1
    return conteo


def bloque_cementerio(cur, dias_atras: int = 30) -> list:
    """Las que cerraron sin que tuviéramos el texto. El coste real."""
    hoy = date.today().isoformat()
    desde = (date.today() - timedelta(days=dias_atras)).isoformat()
    cur.execute(
        "select * from licitacoes where data_encerramento < ? "
        "and data_encerramento >= ? order by data_encerramento desc", (hoy, desde))
    fuera = []
    for f in cur.fetchall():
        fam = familia(f["objeto"] or "")
        if not fam:
            continue
        if _texto_de(f["id"]) is None:
            fuera.append((f, fam))
    return fuera


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dias", type=int, default=7,
                   help="horizonte de la alarma por fecha (por defecto 7)")
    p.add_argument("--breve", action="store_true",
                   help="solo el bloque de ciegas y cerrando")
    p.add_argument("--db", default=str(DB))
    args = p.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print()
    print("🔦  COBERTURA · " + date.today().isoformat())
    print("    La pregunta no es qué puedo hacer, sino qué no estoy viendo.")
    print()

    # ── 1. CIEGAS Y CERRANDO ─────────────────────────────────────────────
    ciegas = bloque_ciegas(cur, args.dias)
    if not ciegas:
        print(f"✅ Nada ciego cerrando en {args.dias} días. "
              f"Todo lo de nuestras familias tiene texto.")
    else:
        print(f"🚨 {len(ciegas)} licitaciones de NUESTRAS FAMILIAS cierran en "
              f"{args.dias} días y NO PODEMOS JUZGARLAS")
        print()
        for f, fam, estado in ciegas:
            plat = detectar_plataforma(f["objeto"] or "", f["url"] or "") \
                or "compras.gov.br"
            dias = (datetime.fromisoformat(f["data_encerramento"]).date()
                    - date.today()).days
            urg = "🔴" if dias <= 2 else "🟠" if dias <= 4 else "🟡"
            print(f"  {urg} cierra {f['data_encerramento']} ({dias}d) · "
                  f"R$ {f['valor_estimado'] or 0:>11,.0f} · {f['uf']} "
                  f"{(f['municipio'] or '')[:20]}")
            print(f"      {fam} · {plat} · {estado}")
            print(f"      {' '.join((f['objeto'] or '').split())[:95]}")
            print(f"      {f['id']}")
        print()
        ids = " ".join(f["id"] for f, _, _ in ciegas[:12])
        print(f"  ./bajar_lote.sh {ids}")
    print()

    if args.breve:
        return

    # ── 2. SALUD DEL BARRIDO ─────────────────────────────────────────────
    b = bloque_barrido()
    print("─" * 72)
    print("BARRIDO DE HOY")
    if not b["corrio"]:
        print(f"  ❌ El bot NO ha corrido hoy. Último día con log: "
              f"{b['ultimo_dia'] or 'ninguno'}")
        print(f"     ./licitaciones_env/bin/python run_bot.py")
    else:
        print(f"  páginas leídas    : {b['paginas_ok']}")
        if b["paginas_mal"]:
            print(f"  ⚠️  páginas falladas: {b['paginas_mal']}  "
                  f"→ hasta {b['registros_perdidos_max']} contrataciones no vistas")
        else:
            print("  páginas falladas  : 0")
        if b["api_cero"]:
            print("  🚨 la API devolvió CERO resultados — no es que no haya, "
                  "es que no respondió")
        if not b["termino"]:
            print("  ⚠️  la ejecución no llegó al final")
        if b["timeouts"] or b["dns"]:
            print(f"  red: {b['timeouts']} timeouts, {b['dns']} fallos de DNS")
    print()

    # ── 3. HUECOS EN DISCO ───────────────────────────────────────────────
    print("─" * 72)
    print("ABIERTAS DE NUESTRAS FAMILIAS, POR ESTADO EN DISCO")
    huecos = bloque_huecos(cur)
    total = sum(huecos.values())
    for estado, n in sorted(huecos.items(), key=lambda x: -x[1]):
        marca = "  " if estado == "ok" else "⚠️"
        pct = 100 * n // total if total else 0
        print(f"  {marca} {n:4}  ({pct:>3}%)  {estado}")
    print(f"       {total:4}         total")
    print()

    # ── 4. CEMENTERIO ────────────────────────────────────────────────────
    print("─" * 72)
    muertas = bloque_cementerio(cur)
    if not muertas:
        print("CERRADAS EN 30 DÍAS SIN HABERLAS VISTO: ninguna. ")
    else:
        print(f"CERRADAS EN LOS ÚLTIMOS 30 DÍAS SIN TEXTO: {len(muertas)}")
        print("  No se pudieron juzgar y ya no se puede hacer nada. "
              "Este número es el que mide si el sistema funciona.")
        print()
        for f, fam in muertas[:12]:
            print(f"    {f['data_encerramento']} · R$ "
                  f"{f['valor_estimado'] or 0:>10,.0f} · {f['uf']} "
                  f"{(f['municipio'] or '')[:18]:18} · {fam}")
        if len(muertas) > 12:
            print(f"    … y {len(muertas) - 12} más")
    print()


if __name__ == "__main__":
    main()

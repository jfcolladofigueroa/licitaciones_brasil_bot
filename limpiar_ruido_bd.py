#!/usr/bin/env python3
"""
Elimina de licitacoes.db las licitações gravadas SEM o filtro de termos.

Contexto: em 05/08/2026 a gravação incremental gravou os registos em bruto
da API (11.679) antes de aplicar o filtro de termos/exclusões do config.json.
Este script apaga o ruído e deixa só o que passa o filtro.

Uso:
    cd ~/Documents/Programacion/Personal/tools_licitaciones/bot_licitaciones
    python3 limpiar_ruido_bd.py            # simulação
    python3 limpiar_ruido_bd.py --borrar   # apaga a sério
"""
import sqlite3, json, unicodedata, sys, shutil, os
from datetime import datetime

BORRAR = "--borrar" in sys.argv
DB = "licitacoes.db"
DIA = "2026-08-05"          # dia gravado sem filtro


def norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


cfg = json.load(open("config.json", encoding="utf-8"))
termos = [norm(x) for x in cfg["busca"]["termos"]]
exclus = [norm(x) for x in cfg["busca"].get("exclusoes", [])]

con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM licitacoes")
total_antes = cur.fetchone()[0]

cur.execute(
    "SELECT id, titulo, objeto FROM licitacoes WHERE substr(data_encontrada,1,10)=?",
    (DIA,),
)
rows = cur.fetchall()

manter, fora = [], []
for lid, tit, obj in rows:
    t = norm((obj or "") + " " + (tit or ""))
    if any(x in t for x in termos) and not any(x in t for x in exclus):
        manter.append(lid)
    else:
        fora.append(lid)

print("=" * 58)
print(f"  Total na base            : {total_antes}")
print(f"  Gravadas em {DIA}  : {len(rows)}")
print(f"  ✅ Passam o filtro        : {len(manter)}")
print(f"  🗑️  Ruído (a apagar)      : {len(fora)}")
print("=" * 58)

if not BORRAR:
    print("\n🔍 SIMULAÇÃO — nada foi apagado.")
    print("   Para apagar:  python3 limpiar_ruido_bd.py --borrar\n")
    for lid, tit, obj in rows[:8]:
        t = norm((obj or "") + " " + (tit or ""))
        ok = any(x in t for x in termos) and not any(x in t for x in exclus)
        print(f"   [{'MANTÉM' if ok else 'APAGA '}] {(obj or tit or '')[:66]}")
    con.close()
    sys.exit(0)

# backup antes de mexer
bak = f"{DB}.bak_{datetime.now():%Y%m%d_%H%M}"
con.close()
shutil.copy2(DB, bak)
print(f"\n💾 Backup criado: {bak}")

con = sqlite3.connect(DB)
cur = con.cursor()
apagadas = 0
for i in range(0, len(fora), 500):
    lote = fora[i : i + 500]
    q = ",".join("?" * len(lote))
    cur.execute(f"DELETE FROM licitacoes WHERE id IN ({q})", lote)
    apagadas += cur.rowcount
    for tabela, coluna in (("triaje", "licitacao_id"), ("notificacoes", "licitacao_id")):
        try:
            cur.execute(f"DELETE FROM {tabela} WHERE {coluna} IN ({q})", lote)
        except sqlite3.Error:
            pass
con.commit()

cur.execute("SELECT COUNT(*) FROM licitacoes")
total_depois = cur.fetchone()[0]
print(f"\n✅ Apagadas: {apagadas}")
print(f"   {total_antes} → {total_depois} registos")

cur.execute(
    "SELECT substr(data_encontrada,1,10) d, COUNT(*) FROM licitacoes "
    "GROUP BY d ORDER BY d DESC LIMIT 5"
)
print("\n=== Por dia ===")
for d, n in cur.fetchall():
    print(f"   {d}: {n}")

con.execute("VACUUM")
con.close()
print("\n🧹 VACUUM executado (ficheiro compactado).")

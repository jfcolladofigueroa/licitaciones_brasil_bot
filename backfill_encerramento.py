"""
BACKFILL DE data_encerramento, UF Y MUNICIPIO
==============================================
Rellena la fecha de cierre de propuestas, la UF y el municipio de las
licitaciones antiguas (guardadas antes de las correcciones) consultando el
detalle de cada compra en el PNCP:
    GET https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}

Se puede relanzar sin problema: solo procesa las filas con campos vacíos.

Desde el 24/09/2026 las fechas pasan por DatabaseLicitacoes.salvar_licitacao()
(el UPSERT con histórico). Antes hacía COALESCE(?, data_encerramento), que
sobrescribía la fecha sin dejar rastro.

Uso: python backfill_encerramento.py
"""

import sqlite3
import time

import requests

from apis_licitacoes import PNCP_API
from database import DatabaseLicitacoes

DB = "licitacoes.db"
URL = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"


def main():
    base = DatabaseLicitacoes(DB)
    api = PNCP_API()
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """SELECT id FROM licitacoes
           WHERE data_encerramento IS NULL
              OR uf IS NULL OR uf = ''"""
    ).fetchall()
    print(f"Pendientes de backfill: {len(rows)}", flush=True)

    session = requests.Session()
    session.headers.update({
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (compatible; MonitorLicitacoes/2.0)",
    })

    ok = err = 0
    for i, row in enumerate(rows, 1):
        try:
            _, cnpj, ano, seq = row["id"].split("-")
        except ValueError:
            err += 1
            continue

        try:
            resp = session.get(URL.format(cnpj=cnpj, ano=ano, seq=seq), timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(15)
                resp = session.get(URL.format(cnpj=cnpj, ano=ano, seq=seq), timeout=30)
            if resp.status_code == 200:
                dados = resp.json()
                # Fechas, situación y valor: por el UPSERT (con histórico)
                base.salvar_licitacao(api.parse_item(dados))
                unidade = dados.get("unidadeOrgao") or {}
                uf = unidade.get("ufSigla")
                municipio = unidade.get("municipioNome")
                cur.execute(
                    """UPDATE licitacoes
                       SET uf = COALESCE(NULLIF(uf, ''), ?),
                           municipio = COALESCE(municipio, ?)
                       WHERE id = ?""",
                    (uf, municipio, row["id"]),
                )
                conn.commit()
                ok += 1
            else:
                err += 1
        except requests.exceptions.RequestException:
            err += 1
            time.sleep(5)

        if i % 100 == 0:
            print(f"[{i}/{len(rows)}] ok={ok} err={err}", flush=True)
        time.sleep(0.3)

    print(f"FIN ok={ok} err={err}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()

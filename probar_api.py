#!/usr/bin/env python3
"""
Prueba rápida de la API del PNCP y del fallback /publicacao.

Uso:
    cd ~/Documents/Programacion/Personal/tools_licitaciones/bot_licitaciones
    python3 probar_api.py
"""
from datetime import datetime, timedelta
import requests

BASE_PROPOSTA   = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
BASE_PUBLICACAO = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"

ses = requests.Session()
ses.headers.update({"Accept": "*/*", "User-Agent": "Mozilla/5.0 (MonitorLicitacoes)"})

hoje = datetime.now()
data_final = (hoje + timedelta(days=30)).strftime("%Y%m%d")
d_ini = (hoje - timedelta(days=30)).strftime("%Y%m%d")
d_fim = hoje.strftime("%Y%m%d")

print("=" * 62)
print(f"  Teste da API do PNCP — {hoje:%d/%m/%Y %H:%M}")
print("=" * 62)

for mod, nome in [(8, "Dispensa"), (6, "Pregão Eletrônico")]:
    print(f"\n### {nome} (modalidade {mod})")

    # --- 1) endpoint original ---
    url = (f"{BASE_PROPOSTA}?dataFinal={data_final}"
           f"&codigoModalidadeContratacao={mod}&pagina=1&tamanhoPagina=50")
    try:
        r = ses.get(url, timeout=60)
        if r.status_code == 200:
            d = r.json()
            n = len(d.get("data", []))
            tot = d.get("totalRegistros")
            print(f"  /proposta   → HTTP 200 | registos: {n} | total: {tot}")
            if n:
                print("     ✅ O endpoint original VOLTOU A FUNCIONAR.")
        else:
            print(f"  /proposta   → HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"  /proposta   → ERRO: {e}")

    # --- 2) fallback ---
    url = (f"{BASE_PUBLICACAO}?dataInicial={d_ini}&dataFinal={d_fim}"
           f"&codigoModalidadeContratacao={mod}&pagina=1&tamanhoPagina=50")
    try:
        r = ses.get(url, timeout=60)
        if r.status_code == 200:
            d = r.json()
            dados = d.get("data", [])
            hoje_str = hoje.strftime("%Y-%m-%d")
            vig = [x for x in dados
                   if not (x.get("dataEncerramentoProposta") or "")[:10]
                   or (x.get("dataEncerramentoProposta") or "")[:10] >= hoje_str]
            print(f"  /publicacao → HTTP 200 | registos: {len(dados)} "
                  f"| vigentes: {len(vig)} | total: {d.get('totalRegistros')} "
                  f"| páginas: {d.get('totalPaginas')}")
            for it in vig[:3]:
                enc = (it.get("dataEncerramentoProposta") or "")[:10]
                print(f"     • [{enc}] {(it.get('objetoCompra') or '')[:62]}")
        else:
            print(f"  /publicacao → HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"  /publicacao → ERRO: {e}")

print("\n" + "=" * 62)
print("Se /publicacao devolve registos e /proposta não, o fallback do bot")
print("já está a funcionar — basta correr:  python3 monitor.py")
print("=" * 62)

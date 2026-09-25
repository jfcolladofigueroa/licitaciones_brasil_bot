#!/usr/bin/env python3
"""
Clasificador automático del MURO ECONÓMICO.

Lee los PDFs ya descargados en descargas/PNCP-*/ y decide, para cada
licitación, si COLLADO FIGUEIRA LTDA puede superar la qualificação
econômico-financeira.

Perfil de la empresa (contrato social + análise de 12/2025):
    Capital Social ......  R$ 5.000,00   (subscrito e integralizado)
    Patrimônio Líquido ... R$ -12.325,68 (NEGATIVO)
    LG / LC / SG ......... 0,00          (todos abaixo de 1)

Lógica:
    SIN_INDICES  -> ✅ VIABLE      (só certidão de falência)
    PL_OU_CAPITAL-> ✅ VIABLE se 10% do valor estimado <= 5.000  (=> valor <= 50.000)
                    ❌ DESCARTE se acima
    SOLO_PL      -> ❌ DESCARTE    (PL negativo, sem alternativa)
    BALANCO_SEM_INDICES -> 🟡 VIABLE (basta apresentar o balanço)

Uso:
    cd ~/Documents/Programacion/Personal/tools_licitaciones/bot_licitaciones
    python3 muro_economico.py                 # todas as vigentes
    python3 muro_economico.py --dias 15       # só as que encerram em <=15 dias
    python3 muro_economico.py --csv saida.csv # exporta
"""
import os
import re
import csv
import sys
import json
import sqlite3
import argparse
import subprocess
import unicodedata
from datetime import date

CAPITAL_SOCIAL = 5000.00
PATRIMONIO_LIQUIDO = -12325.68
TECHO = CAPITAL_SOCIAL * 10          # R$ 50.000

DB = "licitacoes.db"
BASE = "descargas"


# ----------------------------------------------------------------------
def norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def texto_da_pasta(pasta: str, limite_pdfs: int = 6) -> str:
    """Extrai texto dos PDFs da pasta (usa cache _texto_extraido.txt se existir)."""
    cache = os.path.join(pasta, "_texto_extraido.txt")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            pass

    partes = []
    pdfs = sorted(f for f in os.listdir(pasta) if f.lower().endswith(".pdf"))
    for nome in pdfs[:limite_pdfs]:
        caminho = os.path.join(pasta, nome)
        try:
            r = subprocess.run(
                ["pdftotext", "-layout", caminho, "-"],
                capture_output=True, timeout=90,
            )
            partes.append(r.stdout.decode("utf-8", errors="ignore"))
        except (OSError, subprocess.TimeoutExpired):
            continue
    return "\n".join(partes)


# ----------------------------------------------------------------------
# Padrões
RX_INDICES = re.compile(
    r"(liquidez\s+(geral|corrente)|solvencia\s+geral|indices?\s+(de\s+)?liquidez|\bLG\b.{0,40}\bLC\b)",
    re.I,
)
# "patrimônio líquido OU capital social" (nas duas ordens)
RX_PL_OU_CAPITAL = re.compile(
    r"(patrimonio\s+liquido[^.;]{0,80}\bou\b[^.;]{0,40}capital\s+social"
    r"|capital\s+social[^.;]{0,80}\bou\b[^.;]{0,40}patrimonio\s+liquido"
    r"|capital\s+ou\s+patrimonio\s+liquido"
    r"|patrimonio\s+liquido\s+ou\s+capital)",
    re.I,
)
RX_SOLO_PL = re.compile(
    r"(patrimonio\s+liquido\s+(minimo|nao\s+inferior|equivalente|de\s+no\s+minimo)"
    r"|comprovar?\s+patrimonio\s+liquido)",
    re.I,
)
RX_PERCENT = re.compile(r"(\d{1,2})\s*%|\b(dez)\s+por\s+cento", re.I)
RX_BALANCO = re.compile(r"balanco\s+patrimonial", re.I)
RX_FALENCIA = re.compile(r"(certidao|certidoes)\s+negativa[^.;]{0,60}falencia", re.I)

# --- filtro de objeto: software / TI ---
RX_SOFTWARE = re.compile(
    r"desenvolvimento de (software|sistema|aplica)|fabrica de software|ponto de funcao"
    r"|licenciamento de (software|sistema)|sistema (informatizado|integrado|de gestao|web)"
    r"|software de gestao|cesta[s]? de preco|pesquisa de preco|formacao de preco"
    r"|chatbot|canal de denuncia|ouvidoria|portal da transparencia|aplicativo"
    r"|plataforma digital|locacao de (software|sistema)|cessao de uso de (software|sistema)"
    r"|solucao tecnologica|saas|software as a service|atendimento (virtual|automatizado)"
    r"|gestao eletronica de documento|whatsapp",
    re.I,
)
RX_NAO_SOFTWARE = re.compile(
    r"cozinha|alimenticio|merenda|combustivel|medicamento|obra |pavimenta|reforma"
    r"|veiculo|movel |limpeza|uniforme|impressora|toner|cartucho|generos alimen"
    r"|hospitalar|odontolog|passagens aereas|agenciamento de viagens|seguro|pulseira"
    r"|ilumina[cç][aã]o|livros impressos|mao de obra",
    re.I,
)


def percentual_exigido(trecho: str, default: float = 10.0) -> float:
    m = RX_PERCENT.search(trecho)
    if not m:
        return default
    if m.group(2):
        return 10.0
    try:
        v = float(m.group(1))
        return v if 0 < v <= 30 else default
    except ValueError:
        return default


def classificar(texto: str, valor_estimado: float):
    """Devolve (veredicto, categoria, motivo, exigencia_reais)."""
    t = norm(texto)

    tem_indices = bool(RX_INDICES.search(t))
    tem_balanco = bool(RX_BALANCO.search(t))
    tem_falencia = bool(RX_FALENCIA.search(t))

    # janela de contexto à volta da menção a PL / capital social
    janela = ""
    for m in re.finditer(r"(patrimonio\s+liquido|capital\s+social)", t):
        janela += " " + t[max(0, m.start() - 300): m.start() + 400]

    pl_ou_capital = bool(RX_PL_OU_CAPITAL.search(janela))
    solo_pl = bool(RX_SOLO_PL.search(janela)) and not pl_ou_capital

    if not tem_indices and not solo_pl and not pl_ou_capital:
        if tem_balanco:
            return ("VIABLE", "BALANCO_SEM_INDICES",
                    "Pede balanço mas não exige índices nem PL mínimo", 0.0)
        if tem_falencia:
            return ("VIABLE", "SIN_INDICES",
                    "Só certidão negativa de falência", 0.0)
        return ("REVISAR", "INDETERMINADO",
                "Não foi possível localizar a exigência econômica", 0.0)

    pct = percentual_exigido(janela)
    exig = valor_estimado * pct / 100 if valor_estimado else 0.0

    if pl_ou_capital:
        if not valor_estimado:
            return ("REVISAR", "PL_OU_CAPITAL",
                    f"Aceita capital social ({pct:.0f}%) mas valor estimado desconhecido", 0.0)
        if exig <= CAPITAL_SOCIAL:
            return ("VIABLE", "PL_OU_CAPITAL",
                    f"Aceita capital social: exige R${exig:,.2f} <= R${CAPITAL_SOCIAL:,.2f}", exig)
        return ("DESCARTE", "PL_OU_CAPITAL",
                f"Exige R${exig:,.2f} de capital, disponível R${CAPITAL_SOCIAL:,.2f}", exig)

    if solo_pl:
        return ("DESCARTE", "SOLO_PL",
                f"Exige patrimônio líquido (R${exig:,.2f}) e o PL é negativo", exig)

    # índices sem alternativa explícita encontrada
    return ("DESCARTE", "INDICES_SEM_ALTERNATIVA",
            "Exige índices >1 e não se encontrou alternativa por capital social", 0.0)


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=30,
                    help="só licitações que encerram nos próximos N dias")
    ap.add_argument("--csv", help="ficheiro CSV de saída")
    ap.add_argument("--todas", action="store_true",
                    help="inclui também as já encerradas")
    ap.add_argument("--software", action="store_true",
                    help="só objetos de software/TI (recomendado)")
    ap.add_argument("--max-valor", type=float, default=0,
                    help="ignora licitações acima deste valor estimado")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("❌ licitacoes.db não encontrado. Corre a partir de bot_licitaciones/")

    hoje = date.today()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute(
        "SELECT id, objeto, uf, municipio, valor_estimado, data_encerramento "
        "FROM licitacoes"
    )
    linhas = cur.fetchall()
    con.close()

    alvo = []
    for lid, obj, uf, mun, valor, enc in linhas:
        pasta = os.path.join(BASE, lid)
        if not os.path.isdir(pasta):
            continue
        if not any(f.lower().endswith(".pdf") for f in os.listdir(pasta)):
            continue
        e = (enc or "")[:10]
        if not args.todas:
            if not e:
                continue
            try:
                d = (date.fromisoformat(e) - hoje).days
            except ValueError:
                continue
            if d < 0 or d > args.dias:
                continue
        if args.software:
            o = norm(obj)
            if not RX_SOFTWARE.search(o) or RX_NAO_SOFTWARE.search(o):
                continue
        if args.max_valor and (valor or 0) > args.max_valor:
            continue
        alvo.append((lid, obj, uf, mun, valor or 0.0, e, pasta))

    print("=" * 74)
    print(f"  MURO ECONÓMICO — Capital Social R$ {CAPITAL_SOCIAL:,.2f} | "
          f"PL R$ {PATRIMONIO_LIQUIDO:,.2f}")
    print(f"  Techo em editais 'PL ou capital social': R$ {TECHO:,.2f}")
    print(f"  A analisar: {len(alvo)} licitações com PDFs")
    print("=" * 74)

    resultados = []
    for i, (lid, obj, uf, mun, valor, enc, pasta) in enumerate(alvo, 1):
        texto = texto_da_pasta(pasta)
        if len(texto) < 500:
            ver, cat, motivo, exig = ("REVISAR", "SEM_TEXTO",
                                      "PDF sem texto (provavelmente digitalizado)", 0.0)
        else:
            ver, cat, motivo, exig = classificar(texto, valor)
        resultados.append(dict(
            id=lid, uf=uf, municipio=mun, valor=valor, encerramento=enc,
            veredicto=ver, categoria=cat, motivo=motivo, exigencia=exig,
            objeto=(obj or "")[:110],
        ))
        if i % 25 == 0:
            print(f"   ... {i}/{len(alvo)}")

    ordem = {"VIABLE": 0, "REVISAR": 1, "DESCARTE": 2}
    resultados.sort(key=lambda r: (ordem.get(r["veredicto"], 3), r["encerramento"]))

    icone = {"VIABLE": "✅", "REVISAR": "🟡", "DESCARTE": "❌"}
    for grupo in ("VIABLE", "REVISAR", "DESCARTE"):
        sel = [r for r in resultados if r["veredicto"] == grupo]
        print(f"\n{'=' * 74}\n{icone[grupo]}  {grupo} — {len(sel)}\n{'=' * 74}")
        for r in sel if grupo != "DESCARTE" else sel[:20]:
            print(f"\n[{r['encerramento']}] {r['uf']}/{r['municipio']} — "
                  f"R${r['valor']:,.0f}   ({r['categoria']})")
            print(f"   {r['objeto']}")
            print(f"   → {r['motivo']}")
            print(f"   {r['id']}")
        if grupo == "DESCARTE" and len(sel) > 20:
            print(f"\n   ... e mais {len(sel) - 20}")

    n = {g: sum(1 for r in resultados if r["veredicto"] == g)
         for g in ("VIABLE", "REVISAR", "DESCARTE")}
    print(f"\n{'=' * 74}")
    print(f"  RESUMO   ✅ {n['VIABLE']}   🟡 {n['REVISAR']}   ❌ {n['DESCARTE']}")
    print("=" * 74)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(resultados[0].keys()))
            w.writeheader()
            w.writerows(resultados)
        print(f"\n💾 CSV: {args.csv}")


if __name__ == "__main__":
    main()

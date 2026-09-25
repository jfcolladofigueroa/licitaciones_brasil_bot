"""
MEDIDOR DE PLATAFORMAS DE PAGO
===============================
Responde con datos a la única pregunta que importa sobre las plataformas:
¿cuánto valor de editais estamos dejando pasar por no estar registrados, y
compensa pagar la suscripción?

Cómo funciona: recorre las licitaciones del período —sin depender de la tabla triaje, que
puede estar incompleta— se queda con las que caen en una familia de producto
propia, detecta la plataforma a partir del prefijo "[NOMBRE]" con que el PNCP
etiqueta la descripción, y acumula el techo de los editais por plataforma.

Importante al leer el resultado: el techo del edital NO es ingreso. El histórico
real de la casa es que Sulina cerró al 23% del estimado y Chavantes se ganó al
50%. Por eso se muestra también una expectativa realista, con una tasa de
victoria configurable.

Uso:
    python medir_plataformas.py                # mes en curso
    python medir_plataformas.py --desde 2026-09-01 --hasta 2026-09-30
    python medir_plataformas.py --victoria 0.25
"""

import argparse
import json
import re
import sqlite3
import unicodedata
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "licitacoes.db"
DESCARGAS = BASE / "descargas"

# Dominios que delatan la plataforma dentro del texto del edital. Hace falta
# porque el PNCP solo prefija la descripción con [NOMBRE] en algunas: BLL y BNC
# no aparecen ahí, y son justo las que más nos han bloqueado.
DOMINIOS = {
    "bll.org.br": "BLL", "bllcompras": "BLL",
    "bnccompras": "BNC", "bnc.org.br": "BNC",
    "portaldecompraspublicas": "Portal de Compras Públicas",
    "licitanet": "LICITANET",
    "bbmnet": "BBMNET", "novobbmnet": "BBMNET",

    "comprasbr": "ComprasBR",
    "ammlicita": "AMM Licita (sobre Licitar Digital)",
    "licitardigital": "Licitar Digital",
    "publicenter": "Publicenter",
    "smarapd": "SmarAPD",
    "portaldecomprasnilopolis": "Portal próprio (Nilópolis)",
    "comprasnet.ba.gov.br": "Comprasnet.BA",
}

# Coste anual conocido. None = pendiente de confirmar con el proveedor.
COSTE_ANUAL = {
    "BLL": 2520.00,          # R$630/trimestre desde 13/07/2026
    "AMM Licita": None,
    "Publicenter": None,
    "SmarAPD": None,
    "BNC": None,
    "Portal de Compras Públicas": None,
    "LICITANET": None,
    "BBMNET": None,
    "Licitar Digital": None,
    "ComprasBR": None,
}

# Cierre medio sobre el estimado, según el histórico propio.
DESCUENTO_MEDIO = 0.40

# Techo de valor: por encima del muro económico no se puede competir.
TECHO_VALOR = 250_000

# Familias de producto de la casa. Solo cuenta lo que realmente podríamos hacer.
#
# Ampliado el 11/09/2026, y por un motivo concreto: la lista corta dejó pasar
# "MANUTENÇÃO, HOSPEDAGEM E DESENVOLVIMENTO DOS SÍTIOS ELETRÔNICOS DA
# PREFEITURA" —un portal de libro— porque solo buscaba "site institucional" y
# "portal institucional". El órgano escribe lo que quiere: sítio eletrônico,
# página na internet, portal web, sítio oficial. Un sinónimo que falta aquí es
# una licitación que nunca llega a leerse.
#
# El coste de ampliar es ruido, y el ruido se filtra después leyendo el edital.
# El coste de no ampliar es invisible, que es mucho peor.
FAMILIAS = {
    # OJO con "pesquisa de precos" a secas: es la coletilla administrativa que
    # aparece en CUALQUIER compra ("conforme valores obtidos com esta pesquisa
    # de preços"). Colaba capacetes, herbicida y aparatos de café de Tubarão.
    # Hay que exigir que se esté contratando la HERRAMIENTA, no citándola.
    "cesta de preços": ["plataforma de pesquisa de preco", "sistema de pesquisa de preco",
                        "ferramenta de pesquisa de preco", "software de pesquisa de preco",
                        "portal de pesquisa de preco", "assinatura de pesquisa de preco",
                        "comparacao de preco", "banco de preco", "cesta de preco",
                        "painel de preco", "pesquisa mercadologica",
                        "plataforma de cotacao", "tabela de preco referencial"],
    "chatbot": ["chatbot", "whatsapp", "atendimento virtual", "assistente virtual",
                "inteligencia conversacional", "atendimento automatizado",
                "autoatendimento", "bot de atendimento", "agente virtual",
                "central de atendimento digital", "multiatendimento"],
    "ouvidoria / e-SIC": ["canal de denuncia", "ouvidoria", "e-sic", "esic",
                          "acesso a informacao", "carta de servicos",
                          "servico de informacao ao cidadao", "fala.br"],
    "portal / site": ["site institucional", "portal institucional",
                      "portal da transparencia", "website", "web site",
                      "sitio eletronico", "sitios eletronicos", "sitio oficial",
                      "pagina na internet", "portal web", "portal da prefeitura",
                      "site da prefeitura", "site da camara", "portal do municipio",
                      "desenvolvimento de site", "manutencao de site",
                      "reformulacao de site", "criacao de site", "portal de servicos",
                      "gerenciador de conteudo"],
    "correo / hospedagem": ["correio eletronico", "e-mail institucional",
                            "email institucional", "hospedagem de site",
                            "hospedagem de sistema", "hospedagem web",
                            "servidor de e-mail", "caixa postal", "dominio proprio",
                            "registro de dominio", "servico de nuvem"],
    "diário oficial": ["diario oficial", "publicacoes oficiais", "atos oficiais",
                       "imprensa oficial", "publicacao de atos"],
    "GED / processos": ["gestao documental", "gestao eletronica de documentos",
                        "protocolo eletronico", "processo administrativo eletronico",
                        "ged", "peticionamento eletronico", "tramitacao de processos",
                        "assinatura digital", "arquivo digital", "digitalizacao de acervo"],
    "desarrollo / datos": ["clipping", "monitoramento de midia", "business intelligence",
                           "painel de indicadores", "workflow", "automacao de processos",
                           "dashboard", "painel gerencial",
                           "integracao de sistemas", "api de integracao"],

    # --- Desarrollo a medida ------------------------------------------------
    # Añadida el 14/09/2026 a petición de Jose: "en realidad todo lo que sea
    # desarrollo nuevo".
    #
    # Es una familia de naturaleza distinta a las otras ocho. Las demás filtran
    # por DOMINIO (portal, chatbot, cesta) y presuponen un producto ya hecho —
    # por eso su muro recurrente es el atestado del producto, el registro INPI
    # o el CMS propietario. Ésta filtra por MODALIDAD: el órgano paga por
    # construir, no por licenciar. No hace falta tener el producto de antemano,
    # que es exactamente el punto débil de la casa.
    #
    # Sus muros son otros y hay que vigilarlos al leer: métrica por ponto de
    # função o UST, certificación CMMI / MPS.BR, equipo mínimo con titulación y
    # certificaciones, y presencia física en la sede del órgano.
    # ⚠️ NO usar "sob demanda" ni "sob medida" a secas. Son modalidades
    # contractuales genéricas: la primera pasada con ellas trajo agua mineral,
    # billetes de autobús, luminarias LED, armarios y bicicletas eléctricas.
    # Toda clave de esta familia debe llevar el ancla de software dentro.
    "desarrollo a medida": ["desenvolvimento de software", "desenvolvimento de sistema",
                            "desenvolvimento e manutencao de sistema",
                            "desenvolvimento, manutencao", "fabrica de software",
                            "ponto de funcao", "pontos de funcao",
                            "unidade de servico tecnico",
                            "desenvolvimento de aplicativo", "desenvolvimento de aplicacao",
                            "desenvolvimento de solucao", "manutencao evolutiva",
                            "sustentacao de sistema", "sustentacao de aplicaco",
                            "analise e desenvolvimento de sistema",
                            "software sob demanda", "software sob medida",
                            "sistema sob demanda", "sistema sob medida",
                            "aplicativo sob demanda", "codificacao de sistema"],

    # --- Familias sectoriales -------------------------------------------------
    # Añadidas el 18/09/2026. Jose: "no puede ser que no haya licitaciones de
    # software interesantes... trabajo en lo mismo en España y cada día hay
    # cientos". Tenía razón y el clasificador era el culpable.
    #
    # Medido sobre las 1.084 contrataciones publicadas del 08 al 18/09: el
    # clasificador descartaba 1.029, y de ésas 97 eran contratos de software de
    # gestión — unas 10 al día, 49 de ellas dentro del techo de valor. El fallo
    # era de diseño: las nueve familias originales se construyeron alrededor de
    # lo que ya habíamos visto (portal, chatbot, cesta de preços) y dejaban
    # fuera la categoría MÁS GRANDE del mercado municipal brasileño, que es el
    # sistema integrado de gestión pública, más todos los sectoriales.
    #
    # ⚠️ Sobre "gestão pública / ERP": es el mercado de Betha, IPM, Elotech y
    # GovBR — multi-módulo, atestados duros, incumbentes de años. Entra en el
    # radar para VERLO, no porque sea ganable mañana. Los sectoriales de un solo
    # propósito (ponto, estoque, PACS, escolar) sí son del tamaño de la casa.
    "gestão pública / ERP": ["sistema integrado de gestao", "gestao publica municipal",
                             "software de gestao publica", "sistema de gestao publica",
                             "solucao integrada de gestao", "sistema de gestao municipal",
                             "gestao administrativa e financeira", "erp municipal",
                             "sistema de administracao publica"],
    "gestão educacional": ["gestao educacional", "gestao escolar", "sistema educacional",
                           "diario de classe eletronico", "diario eletronico de classe",
                           "sistema de matricula", "gestao da rede de ensino",
                           "software educacional de gestao"],
    "gestão de saúde": ["gestao de saude", "prontuario eletronico", "gestao hospitalar",
                        "regulacao de leitos", "sistema pacs", "sistema de saude publica",
                        "gestao da atencao basica", "sistema de agendamento de consulta"],
    "ponto eletrônico": ["ponto eletronico", "registro de ponto", "controle de jornada",
                         "controle de frequencia", "folha de ponto eletronica"],
    "almoxarifado / patrimônio": ["gestao de almoxarifado", "sistema de almoxarifado",
                                  "gestao de estoque", "controle de estoque",
                                  "gestao patrimonial", "sistema de patrimonio",
                                  "controle patrimonial", "inventario patrimonial"],
    "tributário / arrecadação": ["gestao tributaria", "sistema tributario",
                                 "arrecadacao municipal", "divida ativa",
                                 "nota fiscal eletronica de servico", "nfs-e", "nfse",
                                 "iss eletronico", "sistema de arrecadacao"],
    "RH / folha": ["folha de pagamento", "gestao de pessoal", "sistema de recursos humanos",
                   "gestao de recursos humanos", "sistema de folha"],
}

# ─────────────────────────────────────────────────────────────────────────────
# RUIDO EN DOS NIVELES — reescrito el 18/09/2026
#
# Hasta hoy había una sola lista y familia() la comprobaba ANTES de buscar la
# familia, devolviendo None de inmediato. Eso producía falsos negativos que no
# dejaban rastro en ninguna parte:
#
#   · "Contratação de software para gestão de estoque de MEDICAMENTOS"
#     → muerto por "medicament"  (era una candidata real de R$ 3.780)
#   · "LICENÇA DE USO DE SOFTWARE DE GESTÃO"
#     → muerto por "licenca de uso de software", que es literalmente cómo la
#       administración brasileña nombra un contrato SaaS municipal
#   · "sistema PACS" de imágenes → muerto por "laborator"/"medico-hospitalar"
#
# La entrada "licenca de uso de software" se había añadido para descartar
# reventa de licencias de terceros (Microsoft, Autodesk). El objetivo era
# correcto, la implementación no: lo que identifica una reventa es el FABRICANTE,
# no la palabra "licencia". Ahora se filtra por fabricante.
#
# RUIDO_DURO  : nunca es software. Veta siempre, aunque haya familia.
# RUIDO_BLANDO: indica un dominio ajeno (salud, laboratorio, obra) pero puede
#               acompañar a un contrato de software de ese dominio. Solo veta
#               si NO se ha encontrado ninguna familia.
# ─────────────────────────────────────────────────────────────────────────────
RUIDO_DURO = ["merenda", "combustivel", "locacao de veiculo", "vigilancia",
         "limpeza e conserva", "obra ", "pavimenta", "reforma", "cftv", "colchoes",
         "medico-hospitalar", "alimenticio", "aerofotogram", "rastreamento veicular",
         "telemetria", "documentos fisicos", "impressora", "mobiliario",
         # añadidos 11/09/2026
         "material de consumo", "uniforme", "genero alimenticio", "irrigac",
         "elevador", "plataforma elevatoria", "drywall", "divisoria",
         "mao de obra terceirizada", "agenciamento de viagens", "passagem aerea",
         "equipamento de tic", "microcomputador", "notebook", "monitor de video",
         "cirurgico", "anatomico", "forno", "lampada",
         "toner", "cartucho", "fotocondutor", "oleo lubrificante",
         "material eletrico", "salva-vidas", "crach", "absorvente",
         # Reventa de licencia de terceros: se identifica por el FABRICANTE.
         # "licenca de uso de software" a secas ya NO está aquí — era la frase
         # con la que Brasil nombra los contratos SaaS municipales.
         "licenca perpetua", "microsoft", "google cloud", "oracle", "sap ",
         "autocad", "adobe", "windows", "office 365", "antivirus",
         "final cut", "corel", "solidworks", "vmware",
         "telerradiologia", "censo previdenciario", "concurso publico",
         # añadidos 13/09/2026 — servicios no informáticos que se gestionan
         # "via portal web" y por eso casaban con la familia portal/site
         "transporte terrestre", "transporte individual de passageiros",
         "transporte de pacientes", "aplicativo de mobilidade", "motorista",
         "avaliacao educacional", "prova impressa", "correcao de provas",
         # "desenvolvimento" fuera de contexto informático: la palabra es
         # omnipresente en la administración brasileña y sin esto la familia
         # "desarrollo a medida" se llena de urbanismo y asistencia social.
         "desenvolvimento social", "desenvolvimento urbano", "desenvolvimento rural",
         "desenvolvimento economico", "desenvolvimento sustentavel",
         "desenvolvimento infantil", "desenvolvimento humano",
         "desenvolvimento regional", "desenvolvimento agrario",
         "secretaria de desenvolvimento", "desenvolvimento de pessoal",
         "desenvolvimento profissional", "desenvolvimento de projetos arquitet"]


# Términos SACADOS del veto duro el 18/09/2026. No se usan para filtrar: se
# dejan aquí escritos para que nadie los devuelva a RUIDO_DURO sin saber lo que
# costaron. Cada uno nombra un dominio (salud, laboratorio, escuela) que puede
# perfectamente ser el dominio de un contrato de software, y mientras estuvieron
# en la lista dura mataban esas candidatas antes de mirarles la familia.
# Un objeto de estos dominios que NO sea software se descarta solo: no casa con
# ninguna familia y familia() ya devuelve None.
SACADOS_DEL_VETO_18_09 = ["medicament", "laborator", "medico-hospitalar",
                          "licenca de uso de software", "licencas de software"]


# Las claves se buscan con frontera de palabra. Sin esto, "ged" casaba dentro
# de "alaGEDo" y "esic" dentro de cualquier cosa; el filtro traía pan francés.
# El "s?" final es imprescindible: el órgano escribe "pesquisa de preçoS" y sin
# tolerar el plural la frontera de palabra lo rechaza. Las claves se guardan en
# singular y el plural se admite aquí.
# Palabras a las que NO se les añade plural: son partículas y "des", "es" o
# "cons" no significan nada. Sin esta lista el patrón se ensancha sin ganar nada.
_PARTICULAS = {"de", "da", "do", "das", "dos", "e", "a", "o", "os", "as",
               "em", "no", "na", "por", "com", "para", "ao", "aos"}


def _con_plural(clave: str) -> str:
    """Convierte una clave en un patrón que tolera el plural en CADA palabra.

    Arreglado el 18/09/2026. Antes el `s?` iba solo al final de la clave
    completa, así que "cesta de preco" casaba con "cesta de precos" pero NO con
    "cestas de precos" — que es justo como estaba escrito el objeto del Pregão
    33/2026 de Sulina, la licitación que llegó más lejos. La familia estrella de
    la casa no clasificaba su propio caso de referencia.

    Los espacios se vuelven \\s+ de paso: los objetos vienen con saltos de línea
    y dobles espacios pegados del PDF.
    """
    partes = []
    for palabra in clave.split():
        p = re.escape(palabra)
        if palabra not in _PARTICULAS and len(palabra) >= 3:
            p += "s?"
        partes.append(p)
    return r"\s+".join(partes)


_CLAVES_RE = {
    nombre: re.compile(r"\b(?:" + "|".join(_con_plural(k) for k in claves) + r")\b")
    for nombre, claves in FAMILIAS.items()
}


def familia(objeto: str) -> str | None:
    """Devuelve la familia de producto, o None si no es negocio nuestro.

    Dos pasos. El veto duro primero —una obra de pavimentación no es software
    por mucho que el pliego hable de "sistema" de drenaje— y después la familia.

    El arreglo del 18/09/2026 fue adelgazar ese veto. Antes incluía términos de
    dominio ("medicament", "laborator") y la frase "licenca de uso de software",
    y como el veto corre ANTES de mirar la familia, mataba contratos de software
    de esos dominios sin dejar rastro en ningún log. Se medía en el 9% de todo
    lo que el bot traía: unas 10 contrataciones de software al día.
    Ver SACADOS_DEL_VETO_18_09.
    """
    t = _normalizar(objeto)
    if any(x in t for x in RUIDO_DURO):
        return None
    for nombre, patron in _CLAVES_RE.items():
        if patron.search(t):
            return nombre
    return None


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _plataforma_en_documentos(lic_id: str) -> str | None:
    """Segunda pasada: busca el dominio de la plataforma en el texto extraído."""
    txt = DESCARGAS / lic_id / "_texto_extraido.txt"
    if not txt.is_file():
        return None
    try:
        contenido = _normalizar(txt.read_text(errors="ignore")[:400_000])
    except OSError:
        return None
    for dominio, nombre in DOMINIOS.items():
        if dominio in contenido:
            return nombre
    return None


def detectar_plataforma(objeto: str, lic_id: str | None = None) -> str | None:
    """El PNCP prefija la descripción con [NOMBRE] cuando el certame corre en
    una plataforma privada. Sin prefijo, se mira dentro del edital descargado;
    si tampoco aparece, se asume compras.gov.br (gratuita)."""
    m = re.match(r"\s*\[([^\]]{2,40})\]", objeto or "")
    if not m:
        return _plataforma_en_documentos(lic_id) if lic_id else None
    bruto = m.group(1).strip()
    canon = _normalizar(bruto)
    for nombre in COSTE_ANUAL:
        if _normalizar(nombre) in canon or canon in _normalizar(nombre):
            return nombre
    return bruto


def medir(desde: str, hasta: str, victoria: float) -> dict:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    filas = con.execute(
        """
        SELECT l.id, l.objeto, l.valor_estimado, l.municipio, l.uf, l.data_encerramento
        FROM licitacoes l
        WHERE l.data_encerramento BETWEEN ? AND ?
        """,
        (desde, hasta),
    ).fetchall()
    con.close()

    por_plataforma: dict[str, dict] = {}
    for f in filas:
        plat = detectar_plataforma(f["objeto"], f["id"])
        if plat is None:
            continue
        fam = familia(f["objeto"])
        if fam is None:
            continue
        if (f["valor_estimado"] or 0) > TECHO_VALOR:
            continue
        d = por_plataforma.setdefault(plat, {"n": 0, "techo": 0.0, "casos": []})
        d["n"] += 1
        d["techo"] += f["valor_estimado"] or 0
        d["casos"].append(
            f"{f['data_encerramento']} · {f['municipio']}/{f['uf']} · "
            f"R$ {(f['valor_estimado'] or 0):,.0f} · {fam}"
        )

    for plat, d in por_plataforma.items():
        d["expectativa"] = d["techo"] * DESCUENTO_MEDIO * victoria
        coste = COSTE_ANUAL.get(plat)
        d["coste_anual"] = coste
        d["compensa"] = None if coste is None else d["expectativa"] > coste

    return por_plataforma


def main() -> None:
    hoy = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--desde", default=hoy.replace(day=1).isoformat())
    p.add_argument("--hasta", default=hoy.replace(day=28).isoformat()[:8] + "31")
    p.add_argument("--victoria", type=float, default=1 / 3,
                   help="tasa de victoria estimada (por defecto 1 de cada 3)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    r = medir(a.desde, a.hasta, a.victoria)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    print(f"\n📊 Valor bloqueado por plataforma · {a.desde} → {a.hasta}")
    print(f"   Solo familias de producto propias · techo R$ {TECHO_VALOR:,}")
    print(f"   Supuestos: cierre al {(1-DESCUENTO_MEDIO)*100:.0f}% del estimado · "
          f"victoria 1 de cada {1/a.victoria:.0f}\n")

    if not r:
        print("   Ninguna candidata en plataforma de pago en el período.\n")
        return

    for plat, d in sorted(r.items(), key=lambda x: -x[1]["techo"]):
        coste = d["coste_anual"]
        if coste is None:
            veredicto = "coste anual sin confirmar"
        elif d["compensa"]:
            veredicto = f"✅ compensa — expectativa {d['expectativa']:,.0f} vs coste {coste:,.0f}"
        else:
            veredicto = f"❌ no compensa — expectativa {d['expectativa']:,.0f} vs coste {coste:,.0f}"
        print(f"── {plat}")
        print(f"   {d['n']} editais · techo R$ {d['techo']:,.0f} · "
              f"expectativa realista R$ {d['expectativa']:,.0f}")
        print(f"   {veredicto}")
        for c in d["casos"][:6]:
            print(f"     · {c}")
        if len(d["casos"]) > 6:
            print(f"     · … y {len(d['casos'])-6} más")
        print()

    total = sum(d["techo"] for d in r.values())
    exp = sum(d["expectativa"] for d in r.values())
    print(f"   TOTAL techo bloqueado: R$ {total:,.0f}")
    print(f"   TOTAL expectativa realista: R$ {exp:,.0f}\n")
    print("   Recordatorio: el techo no es ingreso. La expectativa ya descuenta")
    print("   el cierre medio y la tasa de victoria, y sigue siendo optimista.\n")


if __name__ == "__main__":
    main()

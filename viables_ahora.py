"""
VIABLES AHORA — qué podemos ganar hoy, con el perfil que tenemos hoy
=====================================================================
La pregunta que responde: de todo lo que está abierto, ¿qué podemos presentar
SIN atestado de nicho, SIN índices financieros y SIN plataforma de pago?

Existe porque la escalera es al revés de como parece. Para São Caetano (portal
institucional, R$61.188) hace falta un atestado de 6 meses operando un portal
con CMS propio; para tener ese atestado hace falta ganar antes un portal
pequeño que no lo exija. Este script busca esos peldaños de abajo.

La regla de Poá está metida en el código
----------------------------------------
Poá estuvo semanas marcada como "sin muro económico" sin que nadie lo hubiera
leído: la carpeta se había borrado y la BD seguía diciendo que los documentos
estaban. Exigía ILG >= 1,00, ILC >= 1,00 y GE <= 0,80, los tres imposibles.

Por eso aquí ningún veredicto se emite de memoria:

  · si no hay texto extraído en disco -> estado SIN_DOCS, nunca "viable"
  · cada muro detectado viene con LA LÍNEA LITERAL del edital que lo levanta
  · la ausencia de cláusulas no es prueba de ausencia de muros; si el edital
    es corto o remite al instrumento convocatório, sale AVISO_TEXTO_CORTO

Uso:
    python viables_ahora.py                      # todo lo abierto
    python viables_ahora.py --dias 7             # solo lo encontrado esta semana
    python viables_ahora.py --familia "portal / site"
    python viables_ahora.py --incluir-pago       # también plataformas de pago
    python viables_ahora.py --todo               # muestra también las bloqueadas
"""

import argparse
import re
import sqlite3
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from medir_plataformas import (
    DESCARGAS,
    FAMILIAS,
    TECHO_VALOR,
    _normalizar,
    detectar_plataforma,
    familia,
)

BASE = Path(__file__).resolve().parent
DB = BASE / "licitacoes.db"

# Plataformas que NO cuestan dinero. Todo lo demás exige suscripción.
PLATAFORMAS_GRATIS = {None, "compras.gov.br", "e-mail", "portal propio"}

# Por debajo de esto el contrato no paga ni el papeleo de presentarse.
PISO_VALOR = 1_000

# Un edital con menos texto que esto no describe sus propias exigencias:
# casi siempre remite al instrumento convocatório, que no está publicado.
MINIMO_TEXTO_FIABLE = 6_000


# ---------------------------------------------------------------------------
# Muros. Cada uno: (clave, etiqueta, gravedad, patrones)
#   gravedad "bloquea" -> BLOQUEADA, no se presenta
#   gravedad "reserva" -> hay que leerlo a mano antes de decidir
# Los patrones se aplican sobre el texto normalizado (minúsculas, sin tildes).
# ---------------------------------------------------------------------------
MUROS = [
    (
        "indices",
        "índices financieros",
        "bloquea",
        r"(indice de liquidez|liquidez (geral|corrente)|solvencia geral|"
        r"grau de endividamento|\bilg\b|\bilc\b|\bisg\b|\bge\b\s*[<=≤])",
    ),
    (
        "patrimonio",
        "patrimonio líquido mínimo",
        "bloquea",
        r"patrimonio liquido[^.]{0,80}(minimo|igual ou superior|nao inferior|"
        r"correspondente a|[0-9]{1,2}\s*%)",
    ),
    (
        "capital",
        "capital social mínimo",
        "bloquea",
        r"capital social[^.]{0,80}(minimo|integralizado[^.]{0,40}(minimo|"
        r"igual ou superior)|nao inferior|[0-9]{1,2}\s*%\s*do valor)",
    ),
    # ── garantía ──────────────────────────────────────────────────────────
    # Reescrito el 18/09/2026. El patrón anterior era
    #     r"garantia (de|da) (proposta|participacao|licitacao)"
    # y se disparaba con la cláusula de penalidades que llevan TODOS los
    # editais derivados de la IN SEGES/ME 73/2022:
    #     "...o sujeitará às penalidades e à imediata perda da garantia de
    #      proposta em favor do órgão ou entidade promotora da licitação"
    # Esa frase MENCIONA la garantía dentro de una condición; no la exige.
    # Al leer los 14 editais del 18/09 salieron tres 🔴 seguidos —Curitiba,
    # Terra Rica, Araraquara— y los tres eran esa misma plantilla.
    # Ahora se exige el verbo de exigencia cerca de la palabra: "será exigida
    # garantia de proposta", "deverá apresentar/recolher/prestar garantia",
    # "comprovação do recolhimento de garantia". Y se excluye la frase de
    # penalidades con un lookahead sobre "perda da".
    (
        "garantia",
        "garantía de propuesta exigida",
        "bloquea",
        r"(?:(?:sera|será) exigid[ao]\s+(?:d[oa]s?\s+licitantes?\s+)?"
        r"(?:a\s+)?garantia\s+(?:de|da)\s+(?:proposta|participacao|licitacao)"
        r"|(?:devera|deverá)\s+(?:apresentar|recolher|prestar|comprovar)\s+"
        r"(?:a\s+)?garantia\s+(?:de|da)\s+(?:proposta|participacao)"
        r"|comprovacao do recolhimento de garantia)",
    ),
    # La garantía de EJECUCIÓN es distinta: se presta al firmar, no al ofertar,
    # y un 5% de un contrato pequeño puede ser asumible. Va como reserva, no
    # como bloqueo, y con su importe a la vista.
    (
        "garantia_ejecucion",
        "garantía de ejecución del contrato",
        "reserva",
        r"(?:(?:sera|será) exigid[ao].{0,40}garantia\s+(?:de\s+execucao|da\s+contratacao)"
        r"|garantia\s+(?:de\s+execucao|da\s+contratacao).{0,60}[0-9]{1,2}\s*%)",
    ),
    # Cláusula del 85%: no es una garantía al ofertar, es una trampa de caja.
    # Si ganas por debajo del 85% del valor orçado tienes que depositar la
    # diferencia. Para una empresa sin caja equivale a no poder competir en
    # precio, que es justo nuestra única ventaja. Detectada en Terra Rica/PR
    # el 18/09: umbral R$36.524 sobre R$42.970.
    (
        "garantia_adicional_85",
        "garantía adicional si la propuesta baja del 85% (bloquea competir en precio)",
        "bloquea",
        r"garantia adicional.{0,120}(?:85|oitenta e cinco)\s*%"
        r"|(?:85|oitenta e cinco)\s*%.{0,120}garantia adicional",
    ),
    (
        "atestado_duro",
        "atestado con cantidad o plazo mínimo",
        "bloquea",
        # El "no mínimo, 0N (cinco) atestados" se añadió el 16/09/2026: la
        # republicación de Iracemápolis exigía CINCO atestados con 18 requisitos
        # nominados y el detector la dejó en amarillo, porque solo buscaba
        # porcentajes y plazos. Contar atestados es otra forma de cerrar la
        # puerta, y más dura: KC tiene uno.
        r"(atestado[^.]{0,200}(1[0-9]{2}\s*%|100\s*%|no minimo\s*[0-9]{1,3}\s*%|"
        r"quantidade minima|periodo minimo|prazo minimo de [0-9]+\s*(mes|ano)|"
        r"minimo de [0-9]+\s*(meses|anos))"
        r"|no minimo,?\s*0?[2-9]\s*\([a-z]+\)\s*atestados"
        r"|apresentacao de,?\s*no minimo,?\s*0?[2-9])",
    ),
    (
        "equipo_minimo",
        "exige plantilla mínima con titulación",
        "bloquea",
        # Añadido el 16/09/2026 (Iracemápolis): "no mínimo 2 profissionais
        # graduados em análises de sistema (…) e no mínimo 1 graduado em
        # Direito". Para una empresa de una persona no hay forma de cumplirlo,
        # y no había detector para esto.
        r"(quadro (de funcionarios|permanente|tecnico)[^.]{0,120}no minimo\s*[0-9]"
        r"|no minimo\s*[0-9]+\s*\(?[a-z]*\)?\s*(profissionai?s?|tecnicos?)"
        r"[^.]{0,60}(graduad|formad|com nivel superior|diploma)"
        r"|comprovar[^.]{0,60}com diplomas)",
    ),
    (
        "inpi",
        "certificado INPI / registro de software",
        "reserva",
        r"(instituto nacional da propriedade industrial|\binpi\b)",
    ),
    (
        "homologacao",
        "homologación de fabricante o tercero",
        "bloquea",
        r"(homologad[oa]s? pel[oa]|certificad[oa]s? pel[oa] fabricante|"
        r"revenda autorizada|parceria (oficial|certificada) com)",
    ),
    (
        "poc_infra",
        "PoC con infraestructura certificada",
        "bloquea",
        r"(iso[/ ]?iec\s*270(01|17|18)|pentest|teste de intrusao)",
    ),
    # --- Prova de conceito ---------------------------------------------------
    # Añadido el 14/09/2026 tras Quatiguá/PR (R$2.347.218). Aquel edital salió
    # con UNA sola reserva y cero muros económicos —la cualificación económica
    # entera era "certidão negativa de falência"— y sin embargo era el más
    # cerrado de todos: PoC con aprobación del 100% sobre 481 requisitos
    # funcionales, convocada con 5 días hábiles de aviso, y subcontratación
    # prohibida. El detector miraba la habilitación y no el TR.
    #
    # La lección: un edital sin muro de papeles puede estar filtrando por
    # producto. Cuando el órgano no te pide documentos, suele ser porque va a
    # pedirte que enseñes el sistema funcionando.
    # NO bloquea. Corrección de Jose, 14/09/2026: "no es un problema, es hacer
    # el sistema completo y listo". Tiene razón — una PoC no mide si sabes
    # programar, y este detector no debe decidir por él.
    #
    # Lo que sí hay que mirar, y por eso sigue siendo reserva, es el CALENDARIO:
    # la PoC se convoca DESPUÉS de ganar la disputa, con 3 a 5 días hábiles de
    # aviso. No pregunta si puedes construirlo; pregunta si ya está construido
    # ese día. Quatiguá eran 481 requisitos de contabilidad pública, tributos y
    # nómina; Palhoça eran 15 de un helpdesk que el sistema de Clube Susi ya
    # cubre casi entero. El detector cuenta requisitos y días, y decide Jose.
    (
        "poc_total",
        "prova de conceito con aprobación del 100% — ¿está ya construido?",
        "reserva",
        # Dos anclas, porque el "100%" no siempre va pegado a "prova de
        # conceito": en Quatiguá la frase era "aprovada a solução que demonstrar
        # atendimento a 100% das funcionalidades obrigatórias integrantes do
        # Roteiro de Testes", con la PoC nombrada mucho antes en el documento.
        r"(prova de conceito[^.]{0,400}(100\s*%|cem por cento|integralidade|"
        r"todos os (itens|requisitos)|qualquer (item|requisito)[^.]{0,40}"
        r"(reprova|desclassific))"
        r"|(100\s*%|cem por cento)[^.]{0,120}(funcionalidades|requisitos|itens)"
        r"[^.]{0,120}(obrigatori|roteiro de testes|prova de conceito)"
        r"|(atender|atendimento a|atenda)[^.]{0,40}(100\s*%|cem por cento)"
        r"[^.]{0,80}(funcionalidade|requisito|item))",
    ),
    (
        "poc",
        "prova de conceito / demostración del producto",
        "reserva",
        r"(prova de conceito|\bpoc\b|roteiro de testes|demonstracao (pratica|"
        r"da solucao)|apresentacao da solucao)",
    ),
    (
        "visita_obrigatoria",
        "visita técnica obligatoria",
        "reserva",
        r"(vistoria|visita) tecnica[^.]{0,60}obrigatori|"
        r"sera obrigatoria a (vistoria|visita)",
    ),
    (
        "balanco",
        "balanço patrimonial (expone el PL negativo)",
        "reserva",
        r"balanco patrimonial",
    ),
    (
        "atestado_blando",
        "atestado de capacidad técnica",
        "reserva",
        # Exigir el verbo de exigencia, no la mera mención. Corregido el
        # 18/09/2026: en Salto do Lontra/PR, que NO pide atestado, saltaba por
        # la cláusula de formato "todos os documentos deverão estar em nome da
        # matriz, EXCETO para atestados de capacidade técnica" — una frase que
        # habla de atestados justamente para decir que no aplica la regla.
        r"(?:apresentac?[ao]o? de|apresentar|comprovac?[ao]o?.{0,30}mediante|"
        r"devera.{0,40}apresentar|exigid[ao].{0,30})"
        r"[^.]{0,80}atestado[s]? de capacidade tecnica"
        r"|atestado[s]? de capacidade tecnica[^.]{0,60}"
        r"(?:emitido|fornecido|expedido|em nome da (?:licitante|proponente|empresa))",
    ),
    # --- Muros de OBJETO -----------------------------------------------------
    # Añadidos el 11/09/2026 después de que Rio Real y Cândido Godói salieran
    # ambas ✅ con la primera versión del detector. Ninguna tenía muro de dinero
    # ni de atestado: lo que las cerraba era que el objeto incluye cosas que una
    # software house de una persona no puede entregar. El detector solo miraba
    # si nos dejaban entrar, no si sabíamos hacer lo que piden.
    (
        "objeto_publicidad",
        "el objeto incluye publicación pagada en prensa",
        "bloquea",
        r"(centimetro[s]?\s*(x\s*)?coluna|cm\s*x\s*col|jornal de grande circulacao|"
        r"publicacao[^.]{0,60}diario oficial da uniao|veiculacao de materia)",
    ),
    (
        "objeto_legislativo",
        "el objeto incluye plenario o proceso legislativo",
        "bloquea",
        # "painel eletronico" a secas se quitó el 18/09/2026: en Santa
        # Mariana/PR (sistema de gestión de salud) el "Painel Eletrônico" es la
        # pantalla que llama al paciente en la sala de espera, y el detector lo
        # marcaba como plenario de cámara municipal y bloqueaba la licitación
        # entera. Ahora el painel solo cuenta si va acompañado del contexto
        # legislativo, que es lo que de verdad nos cierra la puerta.
        r"(votacao eletronica|processo legislativo eletronico|"
        r"modulo do parlamentar|quorum"
        r"|painel eletronico[^.]{0,120}(plenario|sessao|vereador|camara)"
        r"|(plenario|vereador|camara municipal)[^.]{0,120}painel eletronico)",
    ),
    (
        "objeto_marca_propia",
        "exige producto de marca propia",
        "bloquea",
        r"(marca do sistema:?\s*propria|cms proprietario|software de propriedade "
        r"da (licitante|contratada)|desenvolvedora dos sistemas)",
    ),
    (
        "sin_subcontratacion",
        "subcontratación prohibida (cierra la salida del muro de objeto)",
        "reserva",
        r"(nao sera admitida a subcontratacao|vedada a subcontratacao|"
        r"nem subcontratar|e vedado subcontratar)",
    ),
    (
        "plazo_corto",
        "plazo de implantación muy corto",
        "reserva",
        r"(implantacao|execucao do servico)[^.]{0,80}ate\s*(1[0-9]|[1-9])\s*"
        r"\([a-z ]{3,12}\)\s*dias",
    ),
    # --- Ya no es una oportunidad -------------------------------------------
    # Descubierto el 11/09/2026: de las 12 licitaciones que bajamos, las 6 que
    # venían con data_encerramento NULL resultaron ser TODAS publicaciones
    # posteriores a la adjudicación —contrato firmado, término de ratificación,
    # proveedor ya nombrado—, no editais abiertos. Una tenía fecha de 2024.
    # La correlación es fuerte y tiene sentido: el PNCP no pone plazo de
    # propuestas a algo que ya se adjudicó. Detectarlo ahorra bajar y leer.
    (
        "ya_adjudicada",
        "publicación POSTERIOR a la adjudicación, no es un edital abierto",
        "bloquea",
        # Solo marcadores inequívocos. "contratada:" a secas no vale: aparece
        # en cualquier TR abierto ("a quantidade que será contratada:") y marcó
        # Maceió como adjudicada estando viva. Se exige el nombre del proveedor
        # o un encabezado de acto de cierre.
        r"(termo de ratificacao|ratifico a (mencionada|referida|presente)|"
        r"extrato de contrato|termo de formalizacao da dispensa|"
        # "em favor da empresa X" se descartó: en Maceió aparecía hablando de
        # un contrato ANTERIOR que perdió vigencia, y la marcaba como cerrada
        # estando viva. Ante la duda, preferimos el falso negativo: colarse una
        # adjudicada solo cuesta una descarga; descartar una viva cuesta el
        # contrato.
        r"autorizo a empresa|contratada:\s*[a-z][^,\n]{3,60},?\s*"
        r"(inscrita|cnpj|sediada))",
    ),
]

# Frases que desactivan un muro: el edital lo nombra para decir que NO se exige.
NEGACIONES = [
    "nao sera exigida", "nao sera exigido", "nao se exigira", "fica dispensada",
    "dispensada a apresentacao", "nao havera exigencia", "e facultativa",
    "sera facultativa", "poderao realizar", "nao e obrigatoria",
]


def _contexto(texto: str, inicio: int, fin: int, margen: int = 110) -> str:
    """Devuelve la frase alrededor del match, para poder citarla."""
    a = max(0, inicio - margen)
    b = min(len(texto), fin + margen)
    return " ".join(texto[a:b].split())


def _negado(fragmento: str) -> bool:
    return any(n in fragmento for n in NEGACIONES)


def _aplanar(texto: str) -> str:
    """Minúsculas y sin tildes, CONSERVANDO la longitud y las posiciones.

    Añadido el 18/09/2026. Hasta hoy analizar_texto() decía en su docstring que
    recibía "el texto ya normalizado", pero nadie lo normalizaba: evaluar() le
    pasaba el .txt tal cual salía del PDF, con mayúsculas y acentos, mientras
    todos los patrones de MUROS están escritos en minúsculas y sin tildes.
    El detector solo acertaba cuando el PDF venía ya sin acentos —cosa que pasa
    con los escaneados y el OCR, pero no con los PDF nativos—, así que su
    fiabilidad dependía de cómo estuviera hecho el documento. Los editais mejor
    maquetados eran justo los que peor se analizaban.

    No se usa _normalizar() porque NFD + descartar diacríticos cambia la
    longitud de la cadena y desalinea los índices, y entonces las citas salen
    cortadas por el sitio equivocado. Aquí se sustituye carácter a carácter,
    así que la posición N del texto plano es la posición N del original y la
    cita se puede extraer del texto de verdad, con sus tildes.
    """
    salida = []
    for ch in texto:
        d = unicodedata.normalize("NFD", ch)
        base = d[0] if d else ch
        salida.append(base.lower())
    return "".join(salida)


def analizar_texto(texto: str) -> tuple[list[dict], list[dict]]:
    """Busca muros en el texto. Devuelve (bloqueos, reservas).

    La búsqueda se hace sobre una versión aplanada (minúsculas, sin tildes) que
    conserva las posiciones; las citas se recortan del texto original.
    """
    plano = _aplanar(texto)
    bloqueos, reservas = [], []
    vistos = set()
    for clave, etiqueta, gravedad, patron in MUROS:
        # Recorrer TODAS las apariciones, no solo la primera.
        #
        # Corregido el 18/09/2026, y era un agujero grave. Antes se hacía
        # re.search() —primera coincidencia— y si esa venía negada, el muro se
        # daba por ausente y no se miraba ninguna otra. Bastaba UNA mención
        # negada al principio del edital para cegar el muro completo.
        #
        # Caso real: Itaguaçu/ES (PNCP-27357128000163-2026-5). La primera
        # aparición de "índices" es
        #   "...o fato de o licitante encontrar-se em situação de recuperação
        #    judicial NÃO o exime de comprovar sua qualificação
        #    econômico-financeira, pela apresentação de índices..."
        # El anti-negación la descartaba —correctamente— y el detector nunca
        # llegaba a la cláusula b.2.3, que exige LG/SG/LC ≥ 1,0 y patrimônio
        # líquido mínimo del 5%. Resultado: "sin muros" en una licitación
        # que es imposible para nosotros.
        #
        # Ahora se busca la primera aparición NO negada. Si todas están
        # negadas, entonces sí se considera ausente.
        cita = None
        for m in re.finditer(patron, plano):
            # Se busca en el plano, pero la negación se evalúa sobre el plano
            # (las NEGACIONES están sin tildes) y la cita se saca del original.
            if _negado(_contexto(plano, m.start(), m.end())):
                continue
            cita = _contexto(texto, m.start(), m.end())
            break
        if cita is None:
            continue
        # Si ya hay un atestado duro, el blando no aporta nada.
        if clave == "atestado_blando" and "atestado_duro" in vistos:
            continue
        # Prohibir la subcontratación es cláusula de estilo en casi todo edital
        # brasileño: por sí sola no dice nada. Solo importa cuando hay un muro
        # de objeto, porque entonces cierra la única salida posible (traer a un
        # socio con el producto que nos falta). Fuera de ese caso es ruido.
        if clave == "sin_subcontratacion" and not any(
            k.startswith("objeto_") for k in vistos
        ):
            continue
        vistos.add(clave)
        (bloqueos if gravedad == "bloquea" else reservas).append(
            {"clave": clave, "etiqueta": etiqueta, "cita": cita}
        )
    return bloqueos, reservas


def _dimensionar_poc(texto: str) -> dict:
    """Mide la PoC en vez de juzgarla.

    Lo que decide si una PoC es asumible no es la dificultad de programar, sino
    cuánto hay que tener ya hecho y cuántos días dan para presentarlo. Devuelve
    esos dos números y las funcionalidades que nombra, para que la decisión sea
    de Jose y no del regex.
    """
    # Normalizar es imprescindible y faltaba. Esta función recibe el texto en
    # crudo —con mayúsculas y acentos— mientras todos sus patrones están en
    # minúsculas y sin tildes. Resultado: desde que se escribió (11/09/2026) no
    # detectó absolutamente nada, ni días ni requisitos, y devolvía un dict de
    # None que además nunca se imprimía. Dos fallos tapándose el uno al otro.
    texto = _normalizar(texto)

    dias = None
    m = re.search(
        r"(convocad|antecedencia)[^.]{0,120}?([0-9]{1,2})\s*\(?[a-z]*\)?\s*"
        r"\(?[a-z ]*\)?\s*dias?\s*(uteis|corridos)?", texto)
    if m:
        dias = int(m.group(2))

    requisitos = None
    for pat in (r"([0-9]{2,4})\s*\(?[a-z ]*\)?\s*(requisitos|funcionalidades|"
                r"itens)\s*(obrigatori|a serem (testad|avaliad))",
                r"(?:roteiro de testes|prova de conceito)[^.]{0,200}?"
                r"([0-9]{2,4})\s*(requisitos|funcionalidades|itens)"):
        m = re.search(pat, texto)
        if m:
            requisitos = int(m.group(1))
            break

    # Plan B, añadido el 18/09/2026: casi ningún edital dice "son 386
    # requisitos". Los numera y ya está. Al leer los 14 del 18/09 aparecieron
    # matrices de 138, 386, 441, 515, 688, 1.244 y 1.963 requisitos, y el
    # detector no vio ninguna porque ninguno llevaba la cifra escrita.
    # Contar las líneas numeradas da el orden de magnitud, que es lo que
    # importa: la diferencia entre 38 y 1.244 no es de grado, es de si existe
    # o no la posibilidad de presentarse.
    if requisitos is None:
        marcas = len(re.findall(r"\brt\s?[0-9]{1,4}\b", texto))
        if marcas < 30:
            marcas = len(re.findall(r"\n\s*[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}\.?\s",
                                    texto))
        if marcas >= 30:
            requisitos = marcas

    # Presencial o remota, y quién lo decide. Es coste real: una PoC presencial
    # en Bahía o Espírito Santo son dos días de viaje y el desplazamiento corre
    # por cuenta del licitante. Si además la elige la Administración, no se
    # puede planificar.
    presencial = None
    if re.search(r"prova de conceito[^.]{0,300}(presencialmente|nas dependencias|"
                 r"na sede d)", texto):
        presencial = "presencial"
    if re.search(r"(prova de conceito|poc)[^.]{0,300}(remota|videoconferencia|"
                 r"virtual)", texto):
        presencial = "presencial o remota" if presencial else "remota"
    if presencial and re.search(r"(a criterio d|conforme definido (pel|na convocacao)|"
                                r"conforme definid[oa] pela administracao)", texto):
        presencial += " — lo decide la Administración"

    # Qué módulos nombra: da idea del alcance sin tener que leer el TR entero.
    AMBITOS = {
        "contabilidade publica": "contabilidad pública",
        "folha de pagamento": "nómina", "e-social": "e-Social",
        "tributa": "tributos", "patrimonio": "patrimonio",
        "licitac": "licitaciones", "almoxarifado": "almacén",
        "omnichannel": "omnichannel", "chatbot": "chatbot",
        "webchat": "webchat", "multiatendimento": "multiatendimento",
        "pesquisa de preco": "pesquisa de preços",
        "transparencia": "transparencia", "ouvidoria": "ouvidoria",
    }
    ambitos = sorted({v for k, v in AMBITOS.items() if k in texto})
    caros = _requisitos_caros(texto)
    return {"dias_aviso": dias, "requisitos": requisitos,
            "ambitos": ambitos, "modalidad": presencial,
            "caros": caros}


# ─────────────────────────────────────────────────────────────────────────────
# REQUISITOS CAROS — añadido el 19/09/2026
#
# Hasta hoy la PoC se juzgaba contando requisitos, y la regla era "más de 200,
# fuera de alcance". Jose la tumbó con el argumento correcto: doscientos
# requisitos del tipo "permitir cadastrar" son menos trabajo que veinte de
# verdad. El contraejemplo estaba en nuestros propios datos: Jacobina tenía 38
# requisitos y era imposible, porque seis eran proyecciones de FUNDEB con
# escenarios VAAF y VAAT; Medianeira tiene 386 y buena parte son altas, bajas y
# listados.
#
# Contar requisitos mide la longitud del pliego, no la dificultad. Lo que
# encarece de verdad son seis cosas, y todas aparecen en los editais leídos
# entre el 17 y el 19/09:
#
#   1. Integración con un sistema NOMBRADO de un tercero. Dependes de que
#      alguien te dé documentación y acceso, y puede ser el incumbente.
#   2. Transmisión a un sistema del gobierno federal. Esquema, validaciones y
#      ciclo de homologación ajenos.
#   3. Formato regulado por norma, con firma digital.
#   4. Algoritmo de dominio con norma detrás.
#   5. App móvil nativa, sobre todo con sincronización offline.
#   6. Suministro de hardware.
#
# Lo barato es todo lo demás: cadastros, movimientos, filtros, informes sobre
# datos propios, exportación, perfiles. Eso escala aunque haya trescientos.
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ Todo va con frontera de palabra, y los acrónimos cortos además exigen
# CONTEXTO. La primera versión de esta tabla (19/09/2026) no lo hacía y el
# resultado fue cómico: "gal" casaba dentro de "leGAL", "apac" dentro de
# "caPACidade", "rais" dentro de "mateRIAIS". Y "pncp" salía en todas las
# licitaciones de Brasil, porque todas se publican en el PNCP — mencionar un
# sistema no es integrarse con él.
#
# Regla: un acrónimo solo cuenta como requisito caro si aparece cerca de un
# verbo de integración o de envío. Los nombres largos e inequívocos (equiplano,
# educacenso) no necesitan contexto.
# ⚠️ Verbos escritos con precisión quirúrgica, y por una razón. La primera
# versión usaba `integra\w*`, que casa con "INTEGRAnte", "INTEGRAl" e
# "INTEGRAlmente" — palabras que salen en todos los editais ("fazem parte
# integrante deste edital"). Y `comunica\w*` casaba con "COMUNICAdo de débito".
# Resultado: Curitiba, que no tiene ninguna integración, aparecía con una.
_VERBOS_INTEGRACION = (
    r"(?:integra[çc][ãa]o|integrar|integrad[oa]s?|integrav[ée]l|"
    r"transmiss[ãa]o|transmitir|transmiti\w*|"
    r"\benvio\b|enviar|envia\b|remessa|"
    r"exporta[çc][ãa]o|exportar|importa[çc][ãa]o|importar|"
    r"sincroniza[çc][ãa]o|sincronizar|interoperabilidade|"
    r"gera[çc][ãa]o de arquivo|layout|leiaute|webservice|web service|"
    r"\bapi\b|\bapis\b)"
)

# Inequívocos: el nombre solo ya identifica el trabajo.
_CAROS_DIRECTOS = {
    "integración con sistema de terceros nombrado": [
        r"equiplano", r"d[ií]gitro", r"\binteract\b", r"elotech", r"fiorilli",
        r"atende\.net", r"cohapar", r"prodemge", r"celepar", r"secullum",
        r"topdata", r"ahgora", r"\bsiafic\b", r"\bsigtap\b", r"\bsisreg\b",
        r"\bmanad\b", r"\baudesp\b", r"\blicitacon\b", r"\bsiconfi\b",
        r"\bsiope\b", r"\bsiops\b", r"educacenso", r"\bcadunico\b",
        r"cad[uú]nico", r"\bsisaih\b", r"\bsesa[- ]?pr\b",
    ],
    "formato regulado con firma digital": [
        r"arquivo fonte de dados", r"arquivo eletr[oô]nico de jornada",
        r"\bafd\b", r"\baej\b", r"portaria mtp", r"portaria n?[ºo]?\s*671",
        r"icp-brasil", r"\bcades\b", r"\bp7s\b", r"\bptrp\b",
    ],
    "algoritmo de dominio normado": [
        r"folha de pagamento", r"\bfundeb\b", r"\bvaaf\b", r"\bvaat\b",
        r"hora noturna", r"adicional noturno", r"banco de horas",
        r"d[eé]cimo terceiro", r"c[aá]lculo atuarial",
        r"rescis[oõ]es? (?:de )?contrat(?:o|ual) de trabalho",
        r"rescis[aã]o trabalhista",
    ],
    "app móvil nativa": [
        r"aplicativo nativo", r"linguagem nativa",
        r"(?:ios e android|android e ios)",
        r"app store", r"google play",
        r"(?:funcionamento|modo) offline",
        r"sincroniza\w* (?:posterior|offline)",
    ],
    "suministro de hardware": [
        r"terminal de reconhecimento facial", r"rel[oó]gio de ponto",
        r"coletor de dados", r"impressora t[eé]rmica", r"hidr[oô]metro",
        r"leitor biom[eé]trico", r"\bcatraca\b", r"bobina t[eé]rmica",
        r"fornecimento de equipamento", r"\brep-c\b", r"\brep-p\b",
    ],
}

# Ambiguos: el acrónimo es corto o aparece en texto de trámite. Exigen un verbo
# de integración a menos de 120 caracteres.
_CAROS_CON_CONTEXTO = {
    "transmisión a sistema del gobierno": [
        r"e-?social", r"e-?sus", r"\bpncp\b", r"\brndS?\b", r"\bcnes\b",
        r"\bapac\b", r"\bbpa\b", r"\brais\b", r"\bdirf\b", r"\bsefip\b",
        r"\bcaged\b", r"\bsped\b", r"\bsisab\b", r"\bsi-?pni\b",
        r"nota fiscal eletr[oô]nica", r"\bnfs-?e\b",
    ],
    "integración con sistema de terceros nombrado": [
        r"\bgal\b", r"\bsiga\b", r"\btcm\b", r"\btce\b", r"\bbetha\b",
        r"\bipm\b", r"banrisul",
    ],
    "algoritmo de dominio normado": [
        r"d[ií]vida ativa",   # aparece en toda CND: "Dívida Ativa da União"
    ],
}

_CAROS_DIRECTOS_RE = {
    k: re.compile("|".join(v)) for k, v in _CAROS_DIRECTOS.items()
}
_CAROS_CTX_RE = {
    k: re.compile(
        r"(?:" + "|".join(v) + r")(?=.{0,120}?" + _VERBOS_INTEGRACION + r")"
        r"|" + _VERBOS_INTEGRACION + r".{0,120}?(?:" + "|".join(v) + r")",
        re.S)
    for k, v in _CAROS_CON_CONTEXTO.items()
}

# Fórmulas de requisito barato. Sirven para dar contexto: si el pliego está
# lleno de "permitir cadastrar" y no tiene ningún caro, el número alto es ruido.
_BARATOS_RE = re.compile(
    r"permitir (?:cadastr|consult|inclu|alter|exclu|emitir|exportar|filtr|"
    r"visualiz|listar|pesquis)"
    r"|possibilitar (?:a )?(?:emissao|consulta|cadastr|exportac)"
    r"|emitir relatorio|gerar relatorio|exportar (?:em|para|os dados)"
)


def _requisitos_caros(texto: str) -> list[dict]:
    """Devuelve las categorías caras presentes, con lo que las dispara.

    No cuenta: identifica. Una sola integración con EQUIPLANO pesa más que
    trescientos 'permitir cadastrar'.
    """
    hallazgos: dict[str, set[str]] = {}

    for categoria, patron in _CAROS_DIRECTOS_RE.items():
        for m in patron.finditer(texto):
            hallazgos.setdefault(categoria, set()).add(m.group(0).strip())

    for categoria, patron in _CAROS_CTX_RE.items():
        for m in patron.finditer(texto):
            # El grupo puede traer el verbo de contexto pegado; se recorta a lo
            # que de verdad identifica el sistema, para que la evidencia sea
            # legible en el panel.
            frag = m.group(0).strip()
            hallazgos.setdefault(categoria, set()).add(
                frag if len(frag) <= 40 else frag[:40] + "…")

    return [{"categoria": c, "evidencia": sorted(v)[:6]}
            for c, v in sorted(hallazgos.items())]


def _texto_de(lic_id: str) -> str | None:
    f = DESCARGAS / lic_id / "_texto_extraido.txt"
    if not f.is_file():
        return None
    try:
        bruto = f.read_text(errors="ignore")
    except OSError:
        return None
    return bruto if bruto.strip() else None


def evaluar(fila: sqlite3.Row) -> dict:
    """Veredicto de una licitación. Nunca dice 'viable' sin haber leído."""
    fam = familia(fila["objeto"])
    plat = detectar_plataforma(fila["objeto"], fila["id"])
    valor = fila["valor_estimado"] or 0

    r = {
        "id": fila["id"],
        "municipio": f"{fila['municipio']}/{fila['uf']}",
        "objeto": " ".join((fila["objeto"] or "").split())[:150],
        "valor": valor,
        "cierre": fila["data_encerramento"] or "",
        "familia": fam,
        "plataforma": plat or "compras.gov.br / e-mail",
        "de_pago": plat not in PLATAFORMAS_GRATIS,
        "bloqueos": [],
        "reservas": [],
        "avisos": [],
        "poc": None,
    }

    if valor > TECHO_VALOR:
        r["avisos"].append(
            f"R$ {valor:,.0f} supera el techo orientativo de R$ {TECHO_VALOR:,}. "
            "No es motivo de descarte por sí solo: si el edital no exige índices, "
            "patrimonio líquido ni garantía, el tamaño no bloquea. Leer con lupa "
            "la cualificación económico-financiera."
        )

    if not fila["data_encerramento"]:
        r["cierre"] = f"SIN FECHA (pub. {fila['data_publicacao'] or '?'})"
        r["avisos"].append(
            "El PNCP no publicó fecha de cierre. Puede estar abierta o cerrada: "
            "hay que comprobarlo en el edital o con rellenar_fechas.py."
        )

    bruto = _texto_de(fila["id"])
    if bruto is None:
        r["estado"] = "SIN_DOCS"
        r["avisos"].append(
            "No hay texto extraído en disco. Sin leer el edital no hay veredicto "
            "— es exactamente el fallo de Poá. Bajarlo antes de decidir."
        )
        return r

    texto = _normalizar(bruto)
    if len(texto) < MINIMO_TEXTO_FIABLE:
        r["avisos"].append(
            f"Solo {len(texto):,} caracteres de texto. Probablemente remite al "
            "instrumento convocatório, que no está publicado: la ausencia de "
            "cláusulas NO prueba que no haya muros."
        )
    r["bloqueos"], r["reservas"] = analizar_texto(texto)
    r["poc"] = _dimensionar_poc(texto) if any(
        x["clave"].startswith("poc") for x in r["bloqueos"] + r["reservas"]
    ) else None

    if r["bloqueos"]:
        r["estado"] = "BLOQUEADA"
    elif r["reservas"] or r["avisos"]:
        r["estado"] = "RESERVAS"
    else:
        r["estado"] = "VIABLE"
    return r


def buscar(dias: int | None, fam_filtro: str | None, incluir_pago: bool) -> list[dict]:
    hoy = date.today().isoformat()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # Ojo con el IS NULL: la primera versión filtraba solo por
    # "data_encerramento >= hoy", y eso tiraba a la basura en silencio las 2.414
    # filas sin fecha de cierre — entre ellas São Caetano (R$61.188) y una
    # decena más de familias propias. Una licitación sin fecha no está cerrada:
    # está sin verificar, que es distinto, y hay que mirarla.
    sql = (
        "SELECT id, objeto, valor_estimado, municipio, uf, data_encerramento, "
        "data_publicacao, data_encontrada FROM licitacoes "
        "WHERE (data_encerramento >= ? OR (data_encerramento IS NULL "
        "       AND date(data_publicacao) >= ?))"
    )
    params: list = [hoy, (date.today() - timedelta(days=30)).isoformat()]
    if dias:
        sql += " AND date(data_encontrada) >= ?"
        params.append((date.today() - timedelta(days=dias)).isoformat())
    filas = con.execute(sql, params).fetchall()
    con.close()

    salida = []
    for f in filas:
        if familia(f["objeto"]) is None:
            continue
        valor = f["valor_estimado"] or 0
        if valor < PISO_VALOR:
            continue
        # NO se descarta por valor alto. El techo de R$50.000 no es una regla:
        # es una consecuencia del muro económico. Un edital de R$180.000 que no
        # exige índices, ni patrimonio líquido, ni garantía, se puede presentar
        # igual que uno de R$8.000 — el tamaño no habilita ni inhabilita a nadie
        # por sí solo. Lo que cierra la puerta son las cláusulas, y ésas se leen
        # abajo, en el texto. Descartar por cifra era el mismo error silencioso
        # que descartar por fecha nula.
        r = evaluar(f)
        if fam_filtro and r["familia"] != fam_filtro:
            continue
        if r["de_pago"] and not incluir_pago:
            continue
        salida.append(r)

    orden = {"VIABLE": 0, "RESERVAS": 1, "SIN_DOCS": 2, "BLOQUEADA": 3}
    salida.sort(key=lambda x: (orden[x["estado"]], (x["cierre"] or "9999")))
    return salida


ICONO = {"VIABLE": "✅", "RESERVAS": "🟡", "SIN_DOCS": "⬜", "BLOQUEADA": "🔴"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dias", type=int, help="solo lo encontrado en los últimos N días")
    p.add_argument("--familia", choices=sorted(FAMILIAS), help="filtrar por familia")
    p.add_argument("--incluir-pago", action="store_true")
    p.add_argument("--todo", action="store_true", help="mostrar también las bloqueadas")
    a = p.parse_args()

    res = buscar(a.dias, a.familia, a.incluir_pago)
    if not res:
        print("\n   Nada abierto que encaje con el perfil.\n")
        return

    print(f"\n🪜  VIABLES AHORA · {date.today().isoformat()}")
    print(f"   Valor entre R$ {PISO_VALOR:,} y R$ {TECHO_VALOR:,}"
          f"{'' if a.incluir_pago else ' · solo plataformas gratuitas'}")
    print(f"   {sum(1 for r in res if r['estado']=='VIABLE')} viables · "
          f"{sum(1 for r in res if r['estado']=='RESERVAS')} con reservas · "
          f"{sum(1 for r in res if r['estado']=='SIN_DOCS')} sin leer · "
          f"{sum(1 for r in res if r['estado']=='BLOQUEADA')} bloqueadas\n")

    sin_docs = []
    for r in res:
        if r["estado"] == "BLOQUEADA" and not a.todo:
            continue
        print(f"{ICONO[r['estado']]} {r['municipio']} · R$ {r['valor']:,.0f} · "
              f"cierra {r['cierre']} · {r['familia']}")
        print(f"   {r['objeto']}")
        print(f"   plataforma: {r['plataforma']}")
        for b in r["bloqueos"]:
            print(f"   🔴 {b['etiqueta']}")
            print(f"      « …{b['cita']}… »")
        for v in r["reservas"]:
            print(f"   🟡 {v['etiqueta']}")
            print(f"      « …{v['cita']}… »")

        # Dimensionado de la PoC. Se calculaba desde el 11/09 y no se imprimía
        # nunca — el bug se vio el 18/09, al leer a mano 14 editais y descubrir
        # matrices de 386, 688, 1.244 y 1.963 requisitos que el panel no
        # mostraba. Es el dato que más decide y era el único invisible.
        p = r.get("poc")
        if p:
            partes = []
            if p.get("requisitos"):
                # El número se informa, pero ya no dictamina. Ver la nota de
                # _requisitos_caros: mide la longitud del pliego, no el trabajo.
                partes.append(f"~{p['requisitos']} requisitos")
            if p.get("dias_aviso"):
                partes.append(f"{p['dias_aviso']} días de aviso")
            if p.get("modalidad"):
                partes.append(p["modalidad"])
            if partes:
                print(f"      📐 PoC: {' · '.join(partes)}")

            caros = p.get("caros") or []
            if not caros:
                print("      💰 sin requisitos caros detectados — "
                      "el tamaño de la matriz no bloquea por sí solo")
            else:
                print(f"      💰 {len(caros)} tipos de requisito caro:")
                for c in caros:
                    print(f"         · {c['categoria']}: "
                          f"{', '.join(c['evidencia'])}")
            if p.get("ambitos"):
                print(f"      📐 ámbitos: {', '.join(p['ambitos'])}")

        for av in r["avisos"]:
            print(f"   ⚠️  {av}")
        print(f"   {r['id']}")
        print()
        if r["estado"] == "SIN_DOCS":
            sin_docs.append(r["id"])

    if sin_docs:
        print("Para poder juzgarlas hay que bajarlas primero:")
        print(f"   ./bajar_lote.sh {' '.join(sin_docs[:10])}\n")

    print("Recordatorio: un ✅ significa 'no encontré muros en el texto', no")
    print("'no hay muros'. Antes de invertir horas, leer el edital entero.\n")


if __name__ == "__main__":
    main()

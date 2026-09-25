"""
APIS DE LICITACIONES DE BRASIL - VERSIÓN CORREGIDA
===================================================
Basado en código funcional de: https://github.com/thiagosy/PNCP

Parámetros de la API (endpoint /v1/contratacoes/publicacao):
- dataInicial: YYYYMMDD (obligatorio)
- dataFinal: YYYYMMDD (obligatorio)
- codigoModalidadeContratacao: int (obligatorio)
- uf: string (opcional)
- pagina: int (obligatorio)
- tamanhoPagina: int (opcional, max ~50)
"""

import re
import requests
import unicodedata
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from urllib.parse import quote
import time


def _normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar términos sin perder por una tilde."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


@dataclass
class Licitacao:
    """Estructura de datos para una licitación"""
    id: str
    titulo: str
    objeto: str
    orgao: str
    valor_estimado: Optional[float]
    modalidade: str
    uf: str
    municipio: Optional[str]
    data_publicacao: str
    data_abertura: Optional[str]
    data_encerramento: Optional[str]
    url: Optional[str]
    fonte: str
    cnpj_orgao: Optional[str] = None
    situacao: Optional[str] = None
    
    def to_dict(self) -> dict:
        return self.__dict__
    
    def contem_termo(self, termos: List[str]) -> bool:
        """Verifica si la licitación contiene alguno de los términos"""
        if not termos:
            return True
        # Quita prefijos/tags de metadato del sistema de origen, p. ej.
        # "[Portal de Compras Públicas] - ...", para no generar falsos positivos.
        bruto = re.sub(r"\[[^\]]*\]", " ", f"{self.titulo} {self.objeto}")
        texto = _normalizar(bruto)
        # Quita el boilerplate "Sistema de Registro de Preços" (es el método de
        # contratación, no el objeto) para que "sistema" no genere falsos positivos.
        texto = re.sub(r"sistema de registro de preco[s]?", " ", texto)
        texto = re.sub(r"registro de preco[s]?", " ", texto)
        return any(
            re.search(r"\b" + re.escape(_normalizar(termo)) + r"(s|es)?\b", texto)
            for termo in termos
        )


class PNCP_API:
    """
    API de consulta del PNCP
    URL base: https://pncp.gov.br/api/consulta/v1
    """
    
    BASE_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
    # Fallback: endpoint por data de publicação. Usado quando /proposta
    # devolve vazio (índice do PNCP fora do ar, como ocorreu em ago/2026).
    BASE_URL_PUBLICACAO = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
    # Janela do fallback /publicacao, em dias para trás.
    #
    # Estava em 3 porque a API pagina do mais antigo para o mais recente: com
    # 30 dias eram ~6.400 páginas e o bot nunca chegava às do próprio dia.
    #
    # Esse motivo deixou de existir em 16/09/2026, quando a paginação passou a
    # percorrer de trás para a frente (ver o comentário "ORDEN DESCENDENTE" em
    # buscar_licitacoes). Agora as páginas antigas ficam no FIM do percurso, e
    # alargar a janela não custa nada: se a execução for cortada, perde-se o
    # velho, não o de hoje.
    #
    # E passou a importar: o componente /proposta do PNCP está fora do ar desde
    # 14/09/2026 06:13 (página de estado oficial), pelo que /publicacao é a
    # única via. Com 3 dias, bastava o bot falhar uma manhã para que tudo o que
    # foi publicado nesse dia se perdesse para sempre.
    DIAS_FALLBACK = 10

    # Modalidades de contratación (códigos oficiales del PNCP)
    MODALIDADES = {
        1: "Leilão - Eletrônico",
        2: "Diálogo Competitivo",
        3: "Concurso",
        4: "Concorrência - Eletrônica",
        5: "Concorrência - Presencial",
        6: "Pregão - Eletrônico",
        7: "Pregão - Presencial",
        8: "Dispensa de Licitação",
        9: "Inexigibilidade",
        10: "Manifestação de Interesse",
        11: "Pré-qualificação",
        12: "Credenciamento",
        13: "Leilão - Presencial",
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (compatible; MonitorLicitacoes/2.0)"
        })
    
    def buscar(
        self,
        data_final: str,
        codigo_modalidade: int,
        uf: Optional[str] = None,
        pagina: int = 1,
        tamanho_pagina: int = 50,
    ) -> Dict[str, Any]:
        """
        Busca licitações com propostas em aberto.

        Tenta primeiro o endpoint /proposta (filtra por janela de propostas
        aberta). Se ele devolver VAZIO — sintoma de índice fora do ar no PNCP —
        cai automaticamente para /publicacao (por data de publicação) e filtra
        localmente as que ainda estão com propostas abertas.
        """
        r = self._buscar_proposta(data_final, codigo_modalidade, uf,
                                  pagina, tamanho_pagina)
        if r.get("data"):
            return r

        # /proposta veio vazio -> tentar fallback só na primeira página
        if pagina == 1:
            print("   ↩️  /proposta vazio, usando /publicacao...", end=" ", flush=True)
        return self._buscar_publicacao(data_final, codigo_modalidade, uf,
                                       pagina, tamanho_pagina)

    def _buscar_publicacao(
        self,
        data_final: str,
        codigo_modalidade: int,
        uf: Optional[str] = None,
        pagina: int = 1,
        tamanho_pagina: int = 50,
    ) -> Dict[str, Any]:
        """
        Fallback: consulta por DATA DE PUBLICAÇÃO (últimos 30 dias) e devolve
        apenas as contratações cujo prazo de propostas ainda não encerrou.
        """
        # Janela CURTA (3 dias). Antes eram 30 dias: a API devolve os
        # resultados por ordem de publicação ASCENDENTE, e 30 dias dão ~6.400
        # páginas — o bot paginava a partir das mais antigas e nunca chegava
        # às do próprio dia. Com 3 dias são ~400 páginas, todas recentes.
        hoje = datetime.now()
        d_ini = (hoje - timedelta(days=self.DIAS_FALLBACK)).strftime("%Y%m%d")
        d_fim = hoje.strftime("%Y%m%d")

        url = (f"{self.BASE_URL_PUBLICACAO}?dataInicial={d_ini}&dataFinal={d_fim}"
               f"&codigoModalidadeContratacao={codigo_modalidade}"
               f"&pagina={pagina}&tamanhoPagina={min(tamanho_pagina, 50)}")
        if uf:
            url += f"&uf={uf}"

        for tentativa in range(1, 4):
            try:
                resp = self.session.get(url, timeout=60)
                if resp.status_code == 200:
                    dados = resp.json()
                    # manter só as que ainda recebem propostas
                    hoje_str = hoje.strftime("%Y-%m-%d")
                    filtradas = []
                    for it in dados.get("data", []):
                        enc = (it.get("dataEncerramentoProposta") or "")[:10]
                        if not enc or enc >= hoje_str:
                            filtradas.append(it)
                    # Marca: a página TINHA registos, mas todos já encerraram.
                    # Serve para o laço de paginação não parar por engano.
                    dados["_pagina_tinha_dados"] = bool(dados.get("data")) or bool(filtradas)
                    dados["data"] = filtradas
                    return dados
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(15 * tentativa)
                    continue
                return {"data": [], "error": resp.status_code}
            except Exception as e:
                if tentativa == 3:
                    return {"data": [], "error": str(e)}
                time.sleep(5)
        return {"data": [], "error": "max_retries"}

    def _buscar_proposta(
        self,
        data_final: str,
        codigo_modalidade: int,
        uf: Optional[str] = None,
        pagina: int = 1,
        tamanho_pagina: int = 50
    ) -> Dict[str, Any]:
        """
        Busca contrataciones con RECEBIMENTO DE PROPOSTAS EM ABERTO en el PNCP.
        Devuelve solo licitaciones cuya ventana de propuestas sigue abierta
        (cierre entre hoy y data_final), sin importar cuándo se publicaron.

        Args:
            data_final: Fecha límite del cierre de propuestas YYYYMMDD (horizonte)
            codigo_modalidade: Código de modalidad PNCP (6=Pregão Eletrônico, 8=Dispensa)
            uf: Sigla del estado (opcional)
            pagina: Número de página
            tamanho_pagina: Registros por página
        """

        # Endpoint /proposta: filtra por período de propuesta abierto
        url = f"{self.BASE_URL}?dataFinal={data_final}&codigoModalidadeContratacao={codigo_modalidade}&pagina={pagina}&tamanhoPagina={tamanho_pagina}"

        if uf:
            url += f"&uf={uf}"
        
        # Reintentos con backoff ante 429 (límite), 5xx (errores transitorios
        # del servidor) y timeouts.
        for tentativa in range(1, 6):
            try:
                response = self.session.get(url, timeout=60)

                if response.status_code == 200:
                    return response.json()

                # 429 y 5xx son transitorios: reintentar con backoff
                if response.status_code == 429 or response.status_code >= 500:
                    espera = 15 * tentativa  # 15, 30, 45, 60, 75s
                    print(f"⏳ {response.status_code}, aguardando {espera}s...", end=" ", flush=True)
                    time.sleep(espera)
                    continue

                # 4xx (salvo 429): error no recuperable, no insistir
                print(f"   ⚠️ Error {response.status_code}: {response.text[:120]}")
                return {"data": [], "error": response.status_code}

            except requests.exceptions.Timeout:
                print("⏳ timeout, reintentando...", end=" ", flush=True)
                time.sleep(5)
                continue
            except Exception as e:
                print(f"   ⚠️ Error: {e}")
                return {"data": [], "error": str(e)}

        # Agotados los reintentos
        return {"data": [], "error": "max_retries"}
    
    # URL del detalle de una compra concreta. Es la ÚNICA vía que devuelve la
    # fecha de cierre de una licitación que ya no está abierta: /proposta solo
    # lista las vigentes, así que para rellenar huecos antiguos hace falta esto.
    URL_DETALHE = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"

    def buscar_propostas_abertas(
        self,
        data_final: str,
        codigo_modalidade: int,
        uf: Optional[str] = None,
        tamanho_pagina: int = 50,
        max_paginas: Optional[int] = None,
        on_pagina=None,
    ) -> List["Licitacao"]:
        """
        Recorre TODAS las páginas de /contratacoes/proposta y devuelve las
        licitaciones ya normalizadas (mismo dataclass que el resto del fichero).

        Por qué existe aparte de buscar(): buscar() lleva incorporado el fallback
        a /publicacao, que es precisamente la vía que metió 2.242 filas con
        data_encerramento NULL en agosto de 2026 (mantiene a propósito los
        registros sin fecha de cierre para no perderlos, y salvar_licitacao()
        es INSERT-only, así que el NULL se queda para siempre). Aquí NO hay
        fallback: si /proposta no responde, preferimos no traer nada a traer
        filas mutiladas.

        Args:
            data_final: horizonte de cierre de propuestas, YYYYMMDD
            codigo_modalidade: 6=Pregão Eletrônico, 8=Dispensa (ver MODALIDADES)
            max_paginas: tope opcional de páginas (para pruebas / barridos cortos)
            on_pagina: callback(lote) por página, para guardado incremental
        """
        recolhidas: List[Licitacao] = []
        pagina = 1
        total_paginas = None

        while True:
            resultado = self._buscar_proposta(
                data_final=data_final,
                codigo_modalidade=codigo_modalidade,
                uf=uf,
                pagina=pagina,
                tamanho_pagina=tamanho_pagina,
            )

            if total_paginas is None:
                total_paginas = resultado.get("totalPaginas") or 0

            if resultado.get("error"):
                # Un 5xx puntual no debe costarnos el resto del barrido:
                # saltamos la página y seguimos si aún quedan por delante.
                if total_paginas and pagina < total_paginas:
                    pagina += 1
                    time.sleep(5)
                    continue
                break

            dados = resultado.get("data") or []
            if not dados:
                break

            lote = [self.parse_item(it) for it in dados]
            recolhidas.extend(lote)
            if on_pagina:
                try:
                    on_pagina(lote)
                except Exception as e:
                    print(f"   ⚠️ callback: {e}", end=" ")

            if max_paginas and pagina >= max_paginas:
                break
            if total_paginas and pagina >= total_paginas:
                break
            # Salvaguarda cuando la API no reporta total: página incompleta = fin
            if not total_paginas and len(dados) < tamanho_pagina:
                break

            pagina += 1
            time.sleep(5)  # pausa cortés entre páginas (el PNCP responde 429 fácil)

        return recolhidas

    def consultar_compra(self, cnpj: str, ano: str, seq: str) -> Optional[dict]:
        """
        Detalle de una compra concreta. Devuelve el JSON crudo o None.

        Se usa para rellenar filas antiguas: a diferencia de /proposta, responde
        también para licitaciones ya cerradas, que son la mayoría de las 2.414
        filas sin data_encerramento.
        """
        url = self.URL_DETALHE.format(cnpj=cnpj, ano=ano, seq=seq)
        for tentativa in range(1, 4):
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(10 * tentativa)
                    continue
                return None  # 404 y demás 4xx: la compra no existe, no insistir
            except requests.exceptions.RequestException:
                if tentativa == 3:
                    return None
                time.sleep(5)
        return None

    def parse_item(self, item: dict) -> Licitacao:
        """Convierte item de la API a objeto Licitacao"""
        
        orgao = item.get("orgaoEntidade", {})
        # La UF y el municipio vienen en unidadeOrgao, no en orgaoEntidade
        unidade = item.get("unidadeOrgao", {}) or {}
        cnpj = orgao.get("cnpj", "")
        ano = item.get("anoCompra", "")
        seq = item.get("sequencialCompra", "")
        
        url = None
        if cnpj and ano and seq:
            url = f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
        
        return Licitacao(
            id=f"PNCP-{cnpj}-{ano}-{seq}",
            titulo=item.get("objetoCompra", "")[:200],
            objeto=item.get("objetoCompra", ""),
            orgao=orgao.get("razaoSocial", "N/A"),
            valor_estimado=item.get("valorTotalEstimado"),
            modalidade=item.get("modalidadeNome", ""),
            uf=unidade.get("ufSigla") or orgao.get("uf", ""),
            municipio=unidade.get("municipioNome") or orgao.get("municipioNome"),
            data_publicacao=item.get("dataPublicacaoPncp", "")[:10] if item.get("dataPublicacaoPncp") else "",
            data_abertura=item.get("dataAberturaProposta", "")[:10] if item.get("dataAberturaProposta") else None,
            data_encerramento=item.get("dataEncerramentoProposta", "")[:10] if item.get("dataEncerramentoProposta") else None,
            url=url,
            fonte="PNCP",
            cnpj_orgao=cnpj,
            situacao=item.get("situacaoCompraNome")
        )

    # ------------------------------------------------------------------
    # TERCERA VÍA — el buscador del portal (/api/search)
    # ------------------------------------------------------------------
    # Añadida el 17/09/2026. Las SEIS rutas de /api/consulta/v1 llevan caídas
    # desde el lunes 15 a las 00:17 (confirmado por el monitor independiente
    # statuslicitacoes.com.br y por consultas desde dos IP distintas). Con las
    # dos vías habituales muertas, el bot corría 20 minutos, leía dos páginas,
    # fallaba las dos y terminaba diciendo "0 nuevas" como si no hubiera
    # licitaciones en Brasil.
    #
    # /api/search es el buscador que alimenta el portal web. Es otra
    # infraestructura —por eso sigue en pie— y devuelve lo esencial:
    # descripción, órgano, UASG, municipio, UF, modalidad, fechas de vigencia
    # y el prefijo [PLATAFORMA] con que el PNCP etiqueta las privadas.
    #
    # Sus límites, para no confundirlos con los de las otras vías:
    #   · no filtra por "propuestas abiertas": hay que mirar data_fim_vigencia
    #   · no trae valor estimado — queda en None y lo rellena el triaje
    #   · busca por texto libre, así que depende de los términos que se pasen
    # Es una red de emergencia, no un sustituto. En cuanto /consulta vuelva,
    # las dos vías principales siguen teniendo preferencia.
    URL_SEARCH = "https://pncp.gov.br/api/search/"

    def _get_con_retries(self, url: str, tentativas: int = 3,
                         timeout: int = 45):
        """GET con reintentos. Devuelve la Response o None.

        Añadido el 18/09/2026. La red de emergencia la llamaba desde el
        primer día, pero el método nunca llegó a escribirse: el 18/09 el
        /consulta del PNCP dio timeout, el bot cayó al /api/search y
        reventó con AttributeError, registrando "0 licitações novas"
        como si Brasil no hubiera publicado nada. El fallo se comió una
        mañana entera de licitaciones.
        """
        for intento in range(1, tentativas + 1):
            try:
                r = self.session.get(url, timeout=timeout)
                if r.status_code == 200:
                    return r
                # 429 y 5xx son transitorios: merece la pena reintentar
                if r.status_code == 429 or r.status_code >= 500:
                    if intento < tentativas:
                        time.sleep(10 * intento)
                        continue
                return r
            except Exception:
                if intento == tentativas:
                    return None
                time.sleep(10 * intento)
        return None

    def buscar_por_texto(self, termo: str, pagina: int = 1) -> Dict[str, Any]:
        """Consulta el buscador del portal. Devuelve el JSON tal cual."""
        url = (f"{self.URL_SEARCH}?q={quote(termo)}"
               f"&tipos_documento=edital&pagina={pagina}")
        r = self._get_con_retries(url)
        if r is None or r.status_code != 200:
            return {"items": [], "total": 0,
                    "error": f"HTTP {r.status_code}" if r else "sin respuesta"}
        try:
            return r.json()
        except ValueError:
            return {"items": [], "total": 0, "error": "respuesta no es JSON"}

    def parse_item_search(self, item: dict) -> Optional[Licitacao]:
        """Normaliza un resultado de /api/search al mismo dataclass.

        Devuelve None si la contratación está cancelada o anulada: el buscador
        las incluye y las otras vías no, así que hay que descartarlas aquí para
        que no ensucien la base.
        """
        if item.get("cancelado"):
            return None
        if (item.get("situacao_nome") or "").lower() in ("anulada", "revogada"):
            return None

        cnpj = item.get("orgao_cnpj") or ""
        ano = item.get("ano") or ""
        seq = item.get("numero_sequencial") or ""
        if not (cnpj and ano and seq):
            return None

        def _fecha(v):
            return v[:10] if v else None

        return Licitacao(
            id=f"PNCP-{cnpj}-{ano}-{seq}",
            titulo=(item.get("description") or "")[:200],
            objeto=item.get("description") or "",
            orgao=item.get("orgao_nome") or "N/A",
            valor_estimado=None,          # /api/search no lo expone
            modalidade=item.get("modalidade_licitacao_nome") or "",
            uf=item.get("uf") or "",
            municipio=item.get("municipio_nome"),
            data_publicacao=_fecha(item.get("data_publicacao_pncp")) or "",
            data_abertura=_fecha(item.get("data_inicio_vigencia")),
            data_encerramento=_fecha(item.get("data_fim_vigencia")),
            url=f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}",
            fonte="PNCP-search",
            cnpj_orgao=cnpj,
            situacao=item.get("situacao_nome"),
        )


class BuscadorLicitacoes:
    """Buscador principal de licitaciones"""
    
    def __init__(self):
        self.api = PNCP_API()

    def _fecha_por_detalle(self, lic: Licitacao) -> Optional[str]:
        """
        Pide el cierre de propuestas al detalle de la compra. Devuelve
        'YYYY-MM-DD' (el formato que ya guarda la columna data_encerramento,
        TEXT) o None si el PNCP tampoco lo tiene.
        """
        try:
            _, cnpj, ano, seq = lic.id.split("-")
        except ValueError:
            return None  # id con formato raro: no arriesgamos una petición
        dados = self.api.consultar_compra(cnpj, ano, seq)
        if not dados:
            return None
        return (dados.get("dataEncerramentoProposta") or "")[:10] or None


    def buscar(
        self,
        termos: Optional[List[str]] = None,
        exclusoes: Optional[List[str]] = None,
        ufs: Optional[List[str]] = None,
        modalidades: Optional[List[int]] = None,
        dias_adelante: int = 7,
        on_pagina=None,
        completar_fechas: bool = True
    ) -> List[Licitacao]:
        """
        Busca licitaciones
        
        Args:
            termos: Palabras clave para filtrar (filtro LOCAL)
            ufs: Estados a buscar
            modalidades: Códigos de modalidad (default: 6=Pregão Eletrônico, 8=Dispensa)
            dias_adelante: Horizonte de días hacia ADELANTE (cierre de propuestas)
            completar_fechas: si una licitación llega sin data_encerramento
                (sucede con el fallback /publicacao), consulta el detalle de esa
                compra para rellenarla antes de guardarla. Cuesta una petición
                extra por hueco, pero evita repetir el agujero de agosto/2026.

        Recorre TODAS las páginas que reporta la API (campo totalPaginas),
        sin tope artificial, para no perder licitaciones.
        """

        # Horizonte: propuestas que cierran de hoy a 'dias_adelante' días hacia adelante.
        # El endpoint /proposta solo devuelve las que siguen ABIERTAS hoy.
        data_final = (datetime.now() + timedelta(days=dias_adelante)).strftime("%Y%m%d")

        # Modalidades por defecto: las relevantes para servicios de TI
        if not modalidades:
            modalidades = [6, 8]  # 6=Pregão Eletrônico, 8=Dispensa de Licitação
            # Opcionales útiles: 5 (Concorrência Eletrônica), 9 (Inexigibilidade), 12 (Credenciamento)

        print(f"\n📅 Propostas abertas até: {data_final}")
        print(f"📋 Modalidades: {modalidades}")
        print(f"📍 Estados: {ufs or ['Todos']}")
        
        todas: List[Licitacao] = []
        
        # Iterar por cada combinación
        estados = ufs if ufs else [None]  # None = todos
        
        for uf in estados:
            for mod in modalidades:
                mod_nome = self.api.MODALIDADES.get(mod, f"Mod. {mod}")
                uf_str = uf or "BR"
                print(f"\n🔍 {uf_str} - {mod_nome}")
                
                # Recorrer TODAS las páginas que reporte la API (totalPaginas),
                # descubierto tras la 1ª página. Sin tope artificial.
                #
                # ORDEN DESCENDENTE — corregido el 16/09/2026, y es importante.
                # /contratacoes/proposta devuelve los resultados ordenados de
                # MÁS ANTIGUO a más reciente. Paginando 1, 2, 3… se empieza por
                # lo de hace un año y lo de hoy queda al final. Con 344 páginas
                # y el PNCP devolviendo 500 y timeouts, el barrido nunca llega.
                # El 16/09 se leyeron 49 páginas en una hora: 2.450 registros,
                # CERO nuevos, todos de agosto de 2025.
                #
                # El mismo fallo ya se había corregido en el fallback
                # /publicacao (ver DIAS_FALLBACK) pero no aquí, que es la vía
                # principal. Ahora: página 1 para descubrir el total, y desde
                # ahí hacia atrás. Lo reciente entra primero, así que si la
                # ejecución se corta, se ha perdido lo viejo, no lo de hoy.
                pagina = 1
                total_paginas = None
                descendente = False

                def _siguiente(actual, total, desc):
                    """Siguiente página a pedir, o None si ya no quedan."""
                    if not desc:
                        # acabamos de hacer la 1; saltamos a la última
                        return total if total and total > 1 else None
                    # bajando: 2 es la última que falta (la 1 ya se hizo)
                    return actual - 1 if actual > 2 else None
                while True:
                    sufixo = f"/{total_paginas}" if total_paginas else ""
                    print(f"   📄 Página {pagina}{sufixo}...", end=" ")

                    resultado = self.api.buscar(
                        data_final=data_final,
                        codigo_modalidade=mod,
                        uf=uf,
                        pagina=pagina,
                        tamanho_pagina=50
                    )

                    # En la 1ª página descubrimos el total real que ofrece la API
                    if total_paginas is None:
                        total_paginas = resultado.get("totalPaginas") or 0
                        total_reg = resultado.get("totalRegistros") or 0
                        print(f"({total_reg} reg / {total_paginas} pág)", end=" ")

                    erro = resultado.get("error")
                    dados = resultado.get("data", [])

                    # Error tras agotar los reintentos internos: si aún quedan
                    # páginas por delante, NO abortamos el barrido; omitimos esta
                    # página y seguimos (un 5xx puntual no nos cuesta el resto).
                    if erro:
                        sig = _siguiente(pagina, total_paginas, descendente)
                        if sig:
                            print(f"⏭️  página {pagina} omitida (error {erro})")
                            pagina = sig; descendente = True
                            time.sleep(5)
                            continue
                        print(f"⚠️  detenido en página {pagina} (error {erro})")
                        break

                    if not dados:
                        # Página sem registos ÚTEIS. Se veio do fallback
                        # /publicacao e a página original TINHA registos (todos
                        # já encerrados), seguimos para a página seguinte.
                        sig = _siguiente(pagina, total_paginas, descendente)
                        if resultado.get("_pagina_tinha_dados") and sig:
                            print("0 vigentes, seguindo")
                            pagina = sig; descendente = True
                            time.sleep(2)
                            continue
                        # Página vacía SIN error = no hay más resultados reales
                        print("vazio")
                        break

                    print(f"{len(dados)} registros")

                    lote = []
                    for item in dados:
                        lic = self.api.parse_item(item)
                        # Corte de la hemorragia en origen: el fallback
                        # /publicacao deja pasar a propósito las filas sin
                        # dataEncerramentoProposta (para no perderlas), y
                        # salvar_licitacao() es INSERT-only -> ese NULL ya no se
                        # corrige nunca. Así se acumularon 2.414 filas ciegas,
                        # 2.242 de ellas en agosto de 2026. Preguntamos el
                        # detalle ANTES de guardar; si tampoco lo tiene, se
                        # guarda NULL igual y lo recoge rellenar_fechas.py.
                        if completar_fechas and lic.data_encerramento is None:
                            lic.data_encerramento = self._fecha_por_detalle(lic)
                        todas.append(lic)
                        # Só entra no lote (gravação incremental) o que passa
                        # o MESMO filtro de termos/exclusões aplicado no fim.
                        if termos and not lic.contem_termo(termos):
                            continue
                        if exclusoes and lic.contem_termo(exclusoes):
                            continue
                        lote.append(lic)

                    # Guardado incremental: entrega o lote já processado para
                    # que o chamador o persista. Assim, se a API cair a meio,
                    # não se perde o que já foi descarregado.
                    if on_pagina and lote:
                        try:
                            on_pagina(lote)
                        except Exception as e:
                            print(f"   ⚠️ callback: {e}", end=" ")

                    # Salvaguarda cuando la API no reporta total: página incompleta = fin
                    if not total_paginas and len(dados) < 50:
                        break

                    sig = _siguiente(pagina, total_paginas, descendente)
                    if not sig:
                        break
                    pagina = sig
                    descendente = True
                    time.sleep(5)  # Rate limiting entre páginas

                time.sleep(5)  # Pausa entre modalidades
        
        print(f"\n📊 Total da API: {len(todas)}")

        # --------------------------------------------------------------
        # TERCERA VÍA: si /consulta no devolvió NADA, tirar del buscador
        # del portal. Añadido el 17/09/2026, cuando las seis rutas de
        # /api/consulta/v1 llevaban caídas desde el lunes y el bot terminaba
        # anunciando "0 nuevas" como si Brasil hubiera dejado de licitar.
        # Solo se activa con el resultado en cero: mientras la API oficial
        # responda, ésta ni se toca.
        # --------------------------------------------------------------
        if not todas and termos:
            print("\n🆘 /consulta no devolvió nada en ninguna modalidad.")
            print("   La API oficial parece caída. Usando el buscador del portal")
            print("   (/api/search) como red de emergencia.\n")
            vistos_search = set()
            # Filtro de vigencia ANTES de guardar. Añadido el 21/09/2026: el
            # buscador ordena por relevancia de texto y mezcla todas las
            # épocas. La pasada de las 15:00 del 21/09 grabó 1.443 "novas"
            # (851 ya cerradas, 577 sin fecha, desde 2021) y se puso a bajar
            # pliegos de 2023 mientras las 15 abiertas esperaban en la cola.
            # salvar_licitacao() es INSERT-only: lo que entra aquí se queda.
            hoy_s = datetime.now().strftime("%Y-%m-%d")
            # Sin fecha de cierre solo se acepta si se publicó hace poco;
            # si no, es casi siempre un proceso viejo que nunca la tuvo.
            corte_pub = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            descartadas_search = 0

            def _vigente_search(l):
                if l.data_encerramento:
                    return l.data_encerramento >= hoy_s
                return (l.data_publicacao or "") >= corte_pub

            for termo in termos:
                pagina = 1
                while pagina <= 5:          # tope prudente por término
                    r = self.api.buscar_por_texto(termo, pagina)
                    items = r.get("items") or []
                    if r.get("error"):
                        print(f"   ⚠️  '{termo}': {r['error']}")
                        break
                    if not items:
                        break
                    nuevos = 0
                    lote = []
                    for it in items:
                        lic = self.api.parse_item_search(it)
                        if lic and lic.id not in vistos_search:
                            vistos_search.add(lic.id)
                            # Mismo criterio que la vía principal: solo entra
                            # en el lote (y en la base) lo vigente que pasa el
                            # filtro de términos/exclusiones.
                            if not _vigente_search(lic):
                                descartadas_search += 1
                                continue
                            if not lic.contem_termo(termos):
                                continue
                            if exclusoes and lic.contem_termo(exclusoes):
                                continue
                            todas.append(lic)
                            lote.append(lic)
                            nuevos += 1
                    if pagina == 1:
                        print(f"   🔎 '{termo}': {r.get('total', 0)} resultados", end="")
                    print(f" · pág {pagina} (+{nuevos})", end="", flush=True)
                    if on_pagina and lote:
                        try:
                            on_pagina(lote)
                        except Exception as e:
                            print(f" ⚠️ callback: {e}", end="")
                    if len(items) < 10:
                        break
                    pagina += 1
                    time.sleep(2)
                print()
                time.sleep(2)
            print(f"\n📊 Recuperadas por el buscador: {len(todas)} vigentes"
                  f" ({descartadas_search} cerradas o antiguas descartadas antes de guardar)")

        # Filtrar licitaciones aún abiertas (data_encerramento > hoy o sin fecha)
        hoy = datetime.now().strftime("%Y-%m-%d")
        abiertas = [
            l for l in todas
            if l.data_encerramento is None or l.data_encerramento >= hoy
        ]
        print(f"📅 Aún abiertas: {len(abiertas)} (encerramento > {hoy})")

        # Filtrar por términos (LOCAL)
        if termos:
            print(f"🔎 Filtrando por: {termos}")
            filtradas = [l for l in abiertas if l.contem_termo(termos)]
            if exclusoes:
                antes = len(filtradas)
                filtradas = [l for l in filtradas if not l.contem_termo(exclusoes)]
                print(f"   🚫 Excluídas por termos negativos: {antes - len(filtradas)}")
            print(f"   → {len(filtradas)} coinciden")
        else:
            filtradas = abiertas
        
        # Eliminar duplicados
        vistos = set()
        unicas = []
        for l in filtradas:
            if l.id not in vistos:
                vistos.add(l.id)
                unicas.append(l)
        
        print(f"✅ Total final: {len(unicas)}")
        
        return unicas


# ============================================================================
# TEST
# ============================================================================

if __name__ == "__main__":
    print("="*60)
    print("TEST: Buscando licitaciones de TI en SP")
    print("="*60)
    
    buscador = BuscadorLicitacoes()
    
    licitacoes = buscador.buscar(
        termos=["software", "tecnologia", "sistema", "informática"],
        ufs=["SP"],
        dias_adelante=7
    )
    
    print(f"\n{'='*60}")
    print(f"RESULTADOS: {len(licitacoes)}")
    print("="*60)
    
    for i, lic in enumerate(licitacoes[:5], 1):
        print(f"\n{i}. {lic.titulo[:70]}...")
        print(f"   🏢 {lic.orgao[:50]}")
        print(f"   📍 {lic.municipio or 'N/A'} - {lic.uf}")
        valor = f"R$ {lic.valor_estimado:,.2f}" if lic.valor_estimado else "N/I"
        print(f"   💰 {valor}")
        print(f"   🔗 {lic.url}")
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
    
    def parse_item(self, item: dict) -> Licitacao:
        """Convierte item de la API a objeto Licitacao"""
        
        orgao = item.get("orgaoEntidade", {})
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
            uf=orgao.get("uf", ""),
            municipio=orgao.get("municipioNome"),
            data_publicacao=item.get("dataPublicacaoPncp", "")[:10] if item.get("dataPublicacaoPncp") else "",
            data_abertura=item.get("dataAberturaProposta", "")[:10] if item.get("dataAberturaProposta") else None,
            data_encerramento=item.get("dataEncerramentoProposta", "")[:10] if item.get("dataEncerramentoProposta") else None,
            url=url,
            fonte="PNCP",
            cnpj_orgao=cnpj,
            situacao=item.get("situacaoCompraNome")
        )


class BuscadorLicitacoes:
    """Buscador principal de licitaciones"""
    
    def __init__(self):
        self.api = PNCP_API()
    
    def buscar(
        self,
        termos: Optional[List[str]] = None,
        exclusoes: Optional[List[str]] = None,
        ufs: Optional[List[str]] = None,
        modalidades: Optional[List[int]] = None,
        dias_adelante: int = 7
    ) -> List[Licitacao]:
        """
        Busca licitaciones
        
        Args:
            termos: Palabras clave para filtrar (filtro LOCAL)
            ufs: Estados a buscar
            modalidades: Códigos de modalidad (default: 6=Pregão Eletrônico, 8=Dispensa)
            dias_adelante: Horizonte de días hacia ADELANTE (cierre de propuestas)

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
                pagina = 1
                total_paginas = None
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
                        if total_paginas and pagina < total_paginas:
                            print(f"⏭️  página {pagina} omitida (error {erro})")
                            pagina += 1
                            time.sleep(5)
                            continue
                        print(f"⚠️  detenido en página {pagina} (error {erro})")
                        break

                    if not dados:
                        # Página vacía SIN error = no hay más resultados reales
                        print("vazio")
                        break

                    print(f"{len(dados)} registros")

                    for item in dados:
                        lic = self.api.parse_item(item)
                        todas.append(lic)

                    # Parar al llegar a la última página que reporta la API
                    if total_paginas and pagina >= total_paginas:
                        break

                    # Salvaguarda cuando la API no reporta total: página incompleta = fin
                    if not total_paginas and len(dados) < 50:
                        break

                    pagina += 1
                    time.sleep(5)  # Rate limiting entre páginas

                time.sleep(5)  # Pausa entre modalidades
        
        print(f"\n📊 Total da API: {len(todas)}")

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
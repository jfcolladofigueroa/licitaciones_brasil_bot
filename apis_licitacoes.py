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

import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import time


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
        texto = f"{self.titulo} {self.objeto}".lower()
        return any(termo.lower() in texto for termo in termos)


class PNCP_API:
    """
    API de consulta del PNCP
    URL base: https://pncp.gov.br/api/consulta/v1
    """
    
    BASE_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
    
    # Modalidades de contratación
    MODALIDADES = {
        1: "Leilão - Loss (Licitação Presencial)",
        2: "Diálogo Competitivo",
        3: "Concurso",
        4: "Concorrência - Loss (Licitação Presencial)",
        5: "Concorrência - Eletrônica",
        6: "Dispensa de Licitação",
        7: "Inexigibilidade",
        8: "Pregão - Eletrônico",
        9: "Pregão - Loss (Licitação Presencial)",
        10: "Pré-qualificação",
        11: "Credenciamento",
        12: "Leilão - Eletrônico",
        13: "Manifestação de Interesse",
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (compatible; MonitorLicitacoes/2.0)"
        })
    
    def buscar(
        self,
        data_inicial: str,
        data_final: str,
        codigo_modalidade: int,
        uf: Optional[str] = None,
        pagina: int = 1,
        tamanho_pagina: int = 50
    ) -> Dict[str, Any]:
        """
        Busca contrataciones en el PNCP
        
        Args:
            data_inicial: Fecha inicial YYYYMMDD
            data_final: Fecha final YYYYMMDD
            codigo_modalidade: Código de modalidad (1-13)
            uf: Sigla del estado (opcional)
            pagina: Número de página
            tamanho_pagina: Registros por página (max 50)
        """
        
        # Construir URL exactamente como el ejemplo que funciona
        url = f"{self.BASE_URL}?dataInicial={data_inicial}&dataFinal={data_final}&codigoModalidadeContratacao={codigo_modalidade}&pagina={pagina}&tamanhoPagina={tamanho_pagina}"
        
        if uf:
            url += f"&uf={uf}"
        
        try:
            response = self.session.get(url, timeout=60)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"   ⚠️ Error {response.status_code}: {response.text[:200]}")
                return {"data": [], "error": response.status_code}
                
        except requests.exceptions.Timeout:
            print(f"   ⚠️ Timeout")
            return {"data": [], "error": "timeout"}
        except Exception as e:
            print(f"   ⚠️ Error: {e}")
            return {"data": [], "error": str(e)}
    
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
        ufs: Optional[List[str]] = None,
        modalidades: Optional[List[int]] = None,
        dias_atras: int = 7,
        max_paginas: int = 5
    ) -> List[Licitacao]:
        """
        Busca licitaciones
        
        Args:
            termos: Palabras clave para filtrar (filtro LOCAL)
            ufs: Estados a buscar
            modalidades: Códigos de modalidad (default: 6=Dispensa, 8=Pregão Eletrônico)
            dias_atras: Días hacia atrás
            max_paginas: Máximo de páginas por combinación UF/modalidad
        """
        
        # Calcular fechas
        data_final = datetime.now().strftime("%Y%m%d")
        data_inicial = (datetime.now() - timedelta(days=dias_atras)).strftime("%Y%m%d")
        
        # Modalidades por defecto: las más comunes
        if not modalidades:
            modalidades = [2,5,8, 12, 13]  # Dispensa y Pregão Eletrônico Dispensa "6"
        
        print(f"\n📅 Período: {data_inicial} - {data_final}")
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
                
                # Buscar páginas
                for pagina in range(1, max_paginas + 1):
                    print(f"   📄 Página {pagina}...", end=" ")
                    
                    resultado = self.api.buscar(
                        data_inicial=data_inicial,
                        data_final=data_final,
                        codigo_modalidade=mod,
                        uf=uf,
                        pagina=pagina,
                        tamanho_pagina=50
                    )
                    
                    dados = resultado.get("data", [])
                    
                    if not dados:
                        print("vazio")
                        break
                    
                    print(f"{len(dados)} registros")
                    
                    for item in dados:
                        lic = self.api.parse_item(item)
                        todas.append(lic)
                    
                    # Si recibimos menos de 50, no hay más páginas
                    if len(dados) < 50:
                        break
                    
                    time.sleep(0.3)  # Rate limiting
                
                time.sleep(0.5)  # Pausa entre modalidades
        
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
        dias_atras=7,
        max_paginas=3
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
"""
BASE DE DATOS PARA LICITACIONES
================================
Guarda el histórico de licitaciones encontradas para:
- Evitar duplicados
- Detectar nuevas licitaciones
- Analizar tendencias
- Exportar reportes
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path
from contextlib import contextmanager

from apis_licitacoes import Licitacao


class DatabaseLicitacoes:
    """
    Base de datos SQLite para almacenar licitaciones
    """
    
    def __init__(self, db_path: str = "licitacoes.db"):
        self.db_path = Path(db_path)
        self._criar_tabelas()
    
    @contextmanager
    def _conexao(self):
        """Context manager para conexiones"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _criar_tabelas(self):
        """Crea las tablas necesarias"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            
            # Tabla de licitaciones
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS licitacoes (
                    id TEXT PRIMARY KEY,
                    titulo TEXT NOT NULL,
                    objeto TEXT,
                    orgao TEXT,
                    valor_estimado REAL,
                    modalidade TEXT,
                    uf TEXT,
                    municipio TEXT,
                    data_publicacao TEXT,
                    data_abertura TEXT,
                    url TEXT,
                    fonte TEXT,
                    cnpj_orgao TEXT,
                    situacao TEXT,
                    data_encontrada TEXT DEFAULT CURRENT_TIMESTAMP,
                    notificado INTEGER DEFAULT 0,
                    favorito INTEGER DEFAULT 0,
                    notas TEXT
                )
            """)
            
            # Tabla de filtros/alertas guardados
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alertas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    termos TEXT,
                    ufs TEXT,
                    modalidades TEXT,
                    valor_minimo REAL,
                    valor_maximo REAL,
                    ativo INTEGER DEFAULT 1,
                    criado_em TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Tabla de notificaciones enviadas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notificacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    licitacao_id TEXT,
                    canal TEXT,
                    enviado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                    sucesso INTEGER,
                    erro TEXT,
                    FOREIGN KEY (licitacao_id) REFERENCES licitacoes(id)
                )
            """)
            
            # Migración: columna para marcar documentos ya descargados
            cursor.execute("PRAGMA table_info(licitacoes)")
            colunas = [row["name"] for row in cursor.fetchall()]
            if "docs_baixados" not in colunas:
                cursor.execute(
                    "ALTER TABLE licitacoes ADD COLUMN docs_baixados INTEGER DEFAULT 0"
                )

            # Migración: fecha de cierre de propuestas (clave para "próximas a vencer")
            if "data_encerramento" not in colunas:
                cursor.execute(
                    "ALTER TABLE licitacoes ADD COLUMN data_encerramento TEXT"
                )

            # Tabla de triaje por reglas (buckets + flags de muros)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS triaje (
                    licitacao_id TEXT PRIMARY KEY,
                    bucket TEXT,               -- CANDIDATA / NO_SOFTWARE
                    muro_economico TEXT,       -- SI / NO / ?  (único que descarta)
                    poc TEXT,                  -- SI / NO (etiqueta, nunca descarta)
                    fabrica_pf TEXT,           -- SI / NO
                    spec_pesada TEXT,          -- SI / NO
                    plataforma TEXT,
                    me_epp TEXT,               -- exclusiva / ampla / ?
                    valor_extraido REAL,       -- fallback por regex si la API no trae valor
                    texto_ok INTEGER,          -- 1 si se pudo extraer texto de la carpeta
                    fecha_triaje TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (licitacao_id) REFERENCES licitacoes(id)
                )
            """)

            # Migración: etiquetas nuevas del triaje (regla de oro: solo el
            # muro económico descarta; el resto se etiqueta para revisión)
            cursor.execute("PRAGMA table_info(triaje)")
            cols_triaje = [row["name"] for row in cursor.fetchall()]
            for col in ("software_publico", "atestado_exigido", "poc_roteiro"):
                if col not in cols_triaje:
                    cursor.execute(f"ALTER TABLE triaje ADD COLUMN {col} TEXT")

            # Índices para búsquedas rápidas
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_uf ON licitacoes(uf)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_data ON licitacoes(data_publicacao)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_valor ON licitacoes(valor_estimado)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_notificado ON licitacoes(notificado)")
    
    # =========================================================================
    # OPERACIONES CON LICITACIONES
    # =========================================================================
    
    def salvar_licitacao(self, licitacao: Licitacao) -> bool:
        """
        Guarda una licitación. Retorna True si es nueva, False si ya existía.
        """
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            
            # Verificar si ya existe
            cursor.execute("SELECT id FROM licitacoes WHERE id = ?", (licitacao.id,))
            if cursor.fetchone():
                return False  # Ya existe
            
            # Insertar nueva
            cursor.execute("""
                INSERT INTO licitacoes (
                    id, titulo, objeto, orgao, valor_estimado, modalidade,
                    uf, municipio, data_publicacao, data_abertura,
                    data_encerramento, url, fonte, cnpj_orgao, situacao
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                licitacao.id,
                licitacao.titulo,
                licitacao.objeto,
                licitacao.orgao,
                licitacao.valor_estimado,
                licitacao.modalidade,
                licitacao.uf,
                licitacao.municipio,
                licitacao.data_publicacao,
                licitacao.data_abertura,
                licitacao.data_encerramento,
                licitacao.url,
                licitacao.fonte,
                licitacao.cnpj_orgao,
                licitacao.situacao
            ))
            
            return True  # Nueva licitación
    
    def salvar_varias(self, licitacoes: List[Licitacao]) -> Dict[str, int]:
        """
        Guarda múltiples licitaciones.
        Retorna conteo de nuevas vs existentes.
        """
        
        nuevas = 0
        existentes = 0
        
        for lic in licitacoes:
            if self.salvar_licitacao(lic):
                nuevas += 1
            else:
                existentes += 1
        
        return {"nuevas": nuevas, "existentes": existentes}
    
    def buscar(
        self,
        termo: Optional[str] = None,
        uf: Optional[str] = None,
        modalidade: Optional[str] = None,
        valor_minimo: Optional[float] = None,
        valor_maximo: Optional[float] = None,
        solo_no_notificadas: bool = False,
        favoritos: bool = False,
        limite: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Busca licitaciones en la base de datos local
        """
        
        query = "SELECT * FROM licitacoes WHERE 1=1"
        params = []
        
        if termo:
            query += " AND (titulo LIKE ? OR objeto LIKE ? OR orgao LIKE ?)"
            termo_like = f"%{termo}%"
            params.extend([termo_like, termo_like, termo_like])
        
        if uf:
            query += " AND uf = ?"
            params.append(uf)
        
        if modalidade:
            query += " AND modalidade LIKE ?"
            params.append(f"%{modalidade}%")
        
        if valor_minimo is not None:
            query += " AND valor_estimado >= ?"
            params.append(valor_minimo)
        
        if valor_maximo is not None:
            query += " AND valor_estimado <= ?"
            params.append(valor_maximo)
        
        if solo_no_notificadas:
            query += " AND notificado = 0"
        
        if favoritos:
            query += " AND favorito = 1"
        
        query += " ORDER BY data_encontrada DESC LIMIT ?"
        params.append(limite)
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def obtener_no_notificadas(self) -> List[Dict[str, Any]]:
        """Obtiene licitaciones que aún no fueron notificadas"""
        return self.buscar(solo_no_notificadas=True)
    
    def marcar_notificada(self, licitacao_id: str, canal: str, sucesso: bool, erro: str = None):
        """Marca una licitación como notificada"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            
            # Actualizar flag
            cursor.execute(
                "UPDATE licitacoes SET notificado = 1 WHERE id = ?",
                (licitacao_id,)
            )
            
            # Registrar notificación
            cursor.execute("""
                INSERT INTO notificacoes (licitacao_id, canal, sucesso, erro)
                VALUES (?, ?, ?, ?)
            """, (licitacao_id, canal, 1 if sucesso else 0, erro))
    
    def marcar_docs_baixados(self, licitacao_id: str):
        """Marca que los documentos de una licitación ya fueron descargados"""

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE licitacoes SET docs_baixados = 1 WHERE id = ?",
                (licitacao_id,)
            )

    def obtener_pendientes_descarga(self, limite: Optional[int] = None) -> List[Dict[str, Any]]:
        """Licitaciones con link cuyos documentos aún no se descargaron.

        Excluye las ya clasificadas NO_SOFTWARE: nunca se re-descargan,
        aunque sus carpetas desaparezcan de descargas/.
        """

        query = """
            SELECT l.id, l.titulo, l.url FROM licitacoes l
            LEFT JOIN triaje t ON t.licitacao_id = l.id
            WHERE l.url IS NOT NULL AND l.docs_baixados = 0
              AND (t.bucket IS NULL OR t.bucket != 'NO_SOFTWARE')
            ORDER BY
                CASE WHEN l.data_encerramento IS NULL THEN 1 ELSE 0 END,
                l.data_encerramento ASC,
                l.data_encontrada DESC
        """
        params = []
        if limite:
            query += " LIMIT ?"
            params.append(limite)

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # TRIAJE POR REGLAS
    # =========================================================================

    def guardar_triaje(self, licitacao_id: str, resultado: Dict[str, Any]):
        """Guarda (o actualiza) el resultado del triaje de una licitación."""

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO triaje (
                    licitacao_id, bucket, muro_economico, poc, fabrica_pf,
                    spec_pesada, software_publico, atestado_exigido, poc_roteiro,
                    plataforma, me_epp, valor_extraido, texto_ok
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                licitacao_id,
                resultado.get("bucket"),
                resultado.get("muro_economico"),
                resultado.get("poc"),
                resultado.get("fabrica_pf"),
                resultado.get("spec_pesada"),
                resultado.get("software_publico"),
                resultado.get("atestado_exigido"),
                resultado.get("poc_roteiro"),
                resultado.get("plataforma"),
                resultado.get("me_epp"),
                resultado.get("valor_extraido"),
                1 if resultado.get("texto_ok") else 0,
            ))

    def obtener_por_id(self, licitacao_id: str) -> Optional[Dict[str, Any]]:
        """Una licitación por su id, o None."""

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM licitacoes WHERE id = ?", (licitacao_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def ids_no_software(self) -> List[str]:
        """Ids ya clasificados NO_SOFTWARE (registro anti re-descarga)."""

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT licitacao_id FROM triaje WHERE bucket = 'NO_SOFTWARE'")
            return [row["licitacao_id"] for row in cursor.fetchall()]

    def pendientes_triaje(self) -> List[str]:
        """Ids con documentos descargados que aún no pasaron por el triaje."""

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT l.id FROM licitacoes l
                LEFT JOIN triaje t ON t.licitacao_id = l.id
                WHERE l.docs_baixados = 1 AND t.licitacao_id IS NULL
                ORDER BY l.data_encontrada DESC
            """)
            return [row["id"] for row in cursor.fetchall()]

    def obtener_candidatas(self) -> List[Dict[str, Any]]:
        """Candidatas con sus flags de triaje (para el CSV y las alertas)."""

        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT l.id, l.objeto, l.orgao, l.cnpj_orgao, l.uf, l.municipio,
                       l.valor_estimado, l.modalidade, l.data_abertura,
                       l.data_encerramento, l.url, l.notificado,
                       t.bucket, t.muro_economico, t.poc, t.fabrica_pf,
                       t.spec_pesada, t.software_publico, t.atestado_exigido,
                       t.poc_roteiro, t.plataforma, t.me_epp,
                       t.valor_extraido, t.texto_ok
                FROM triaje t
                JOIN licitacoes l ON l.id = t.licitacao_id
                WHERE t.bucket = 'CANDIDATA'
            """)
            return [dict(row) for row in cursor.fetchall()]

    def marcar_favorita(self, licitacao_id: str, favorito: bool = True):
        """Marca/desmarca una licitación como favorita"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE licitacoes SET favorito = ? WHERE id = ?",
                (1 if favorito else 0, licitacao_id)
            )
    
    def agregar_nota(self, licitacao_id: str, nota: str):
        """Agrega una nota a una licitación"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE licitacoes SET notas = ? WHERE id = ?",
                (nota, licitacao_id)
            )
    
    # =========================================================================
    # ALERTAS / FILTROS GUARDADOS
    # =========================================================================
    
    def criar_alerta(
        self,
        nome: str,
        termos: List[str] = None,
        ufs: List[str] = None,
        modalidades: List[str] = None,
        valor_minimo: float = None,
        valor_maximo: float = None
    ) -> int:
        """Crea un alerta/filtro guardado"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alertas (nome, termos, ufs, modalidades, valor_minimo, valor_maximo)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                nome,
                json.dumps(termos) if termos else None,
                json.dumps(ufs) if ufs else None,
                json.dumps(modalidades) if modalidades else None,
                valor_minimo,
                valor_maximo
            ))
            return cursor.lastrowid
    
    def listar_alertas(self, solo_ativos: bool = True) -> List[Dict[str, Any]]:
        """Lista todos los alertas guardados"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM alertas"
            if solo_ativos:
                query += " WHERE ativo = 1"
            cursor.execute(query)
            
            alertas = []
            for row in cursor.fetchall():
                alerta = dict(row)
                # Deserializar JSON
                if alerta["termos"]:
                    alerta["termos"] = json.loads(alerta["termos"])
                if alerta["ufs"]:
                    alerta["ufs"] = json.loads(alerta["ufs"])
                if alerta["modalidades"]:
                    alerta["modalidades"] = json.loads(alerta["modalidades"])
                alertas.append(alerta)
            
            return alertas
    
    def desativar_alerta(self, alerta_id: int):
        """Desactiva un alerta"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE alertas SET ativo = 0 WHERE id = ?",
                (alerta_id,)
            )
    
    # =========================================================================
    # ESTADÍSTICAS Y REPORTES
    # =========================================================================
    
    def estatisticas(self) -> Dict[str, Any]:
        """Retorna estadísticas de la base de datos"""
        
        with self._conexao() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Total de licitaciones
            cursor.execute("SELECT COUNT(*) FROM licitacoes")
            stats["total"] = cursor.fetchone()[0]
            
            # Por estado
            cursor.execute("""
                SELECT uf, COUNT(*) as total 
                FROM licitacoes 
                GROUP BY uf 
                ORDER BY total DESC
            """)
            stats["por_uf"] = {row["uf"]: row["total"] for row in cursor.fetchall()}
            
            # Por modalidad
            cursor.execute("""
                SELECT modalidade, COUNT(*) as total 
                FROM licitacoes 
                GROUP BY modalidade 
                ORDER BY total DESC
            """)
            stats["por_modalidade"] = {row["modalidade"]: row["total"] for row in cursor.fetchall()}
            
            # Valor total
            cursor.execute("SELECT SUM(valor_estimado) FROM licitacoes WHERE valor_estimado IS NOT NULL")
            stats["valor_total"] = cursor.fetchone()[0] or 0
            
            # No notificadas
            cursor.execute("SELECT COUNT(*) FROM licitacoes WHERE notificado = 0")
            stats["nao_notificadas"] = cursor.fetchone()[0]
            
            # Favoritas
            cursor.execute("SELECT COUNT(*) FROM licitacoes WHERE favorito = 1")
            stats["favoritas"] = cursor.fetchone()[0]
            
            return stats
    
    def exportar_csv(self, arquivo: str, **filtros) -> int:
        """Exporta licitaciones para CSV"""
        
        import csv
        
        licitacoes = self.buscar(**filtros, limite=10000)
        
        if not licitacoes:
            return 0
        
        with open(arquivo, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=licitacoes[0].keys())
            writer.writeheader()
            writer.writerows(licitacoes)
        
        return len(licitacoes)
    
    def exportar_json(self, arquivo: str, **filtros) -> int:
        """Exporta licitaciones para JSON"""
        
        licitacoes = self.buscar(**filtros, limite=10000)
        
        with open(arquivo, 'w', encoding='utf-8') as f:
            json.dump(licitacoes, f, ensure_ascii=False, indent=2, default=str)
        
        return len(licitacoes)


# ============================================================================
# EXEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Crear base de datos
    db = DatabaseLicitacoes("teste_licitacoes.db")
    
    # Crear un alerta de ejemplo
    alerta_id = db.criar_alerta(
        nome="TI em São Paulo",
        termos=["software", "tecnologia", "sistemas"],
        ufs=["SP"],
        valor_minimo=10000
    )
    print(f"✅ Alerta creado con ID: {alerta_id}")
    
    # Listar alertas
    alertas = db.listar_alertas()
    print(f"\n📋 Alertas configurados: {len(alertas)}")
    for a in alertas:
        print(f"   - {a['nome']}: {a['termos']}")
    
    # Mostrar estadísticas
    stats = db.estatisticas()
    print(f"\n📊 Estadísticas:")
    print(f"   Total: {stats['total']} licitaciones")
    print(f"   No notificadas: {stats['nao_notificadas']}")

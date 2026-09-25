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
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from contextlib import contextmanager

from apis_licitacoes import Licitacao

# Versión del esquema, guardada en PRAGMA user_version. Cada migración sube
# un número; _migrar() aplica solo las que faltan, así que es idempotente.
ESQUEMA_VERSION = 1

# Resultado de salvar_licitacao()
NUEVA, ACTUALIZADA, IGUAL = "nueva", "actualizada", "igual"


def _ahora() -> str:
    """Hora local, legible. CURRENT_TIMESTAMP de SQLite es UTC y confunde."""
    return datetime.now().isoformat(sep=" ", timespec="seconds")


class DatabaseLicitacoes:
    """
    Base de datos SQLite para almacenar licitaciones
    """

    def __init__(self, db_path: str = "licitacoes.db"):
        self.db_path = Path(db_path)
        # Solo se hace copia si la base ya existía (con datos que perder)
        existia = self.db_path.exists() and self.db_path.stat().st_size > 0
        self._criar_tabelas()
        self._migrar(copia_previa=existia)
    
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
    # MIGRACIONES DE ESQUEMA
    # =========================================================================

    def _copia_de_seguridad(self, version_destino: int) -> Path:
        """Copia consistente de la base (API de backup de SQLite, no cp)."""
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = self.db_path.with_name(
            f"{self.db_path.name}.bak_{sello}_antes_migracion_v{version_destino}"
        )
        origen = sqlite3.connect(self.db_path)
        copia = sqlite3.connect(destino)
        try:
            origen.backup(copia)
        finally:
            copia.close()
            origen.close()
        print(f"💾 Copia previa a la migración: {destino.name}")
        return destino

    def _migrar(self, copia_previa: bool = True):
        """Aplica las migraciones pendientes según PRAGMA user_version."""
        with self._conexao() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= ESQUEMA_VERSION:
            return

        if copia_previa:
            self._copia_de_seguridad(ESQUEMA_VERSION)

        with self._conexao() as conn:
            if version < 1:
                n = self._migracion_v1(conn)
                conn.execute("PRAGMA user_version = 1")
                if copia_previa:
                    print(f"🔧 Migración v1 aplicada: {n} filas del buscador de "
                          f"emergencia pasan a vigencia (fechas de propuestas → nulas)")

    def _migracion_v1(self, conn: sqlite3.Connection) -> int:
        """v1 (24/09/2026): UPSERT con histórico y fechas con fuente.

        - Fecha y hora completas del plazo de propuestas (*_ts).
        - Fuente de cada fecha: consulta / search / edital / manual.
        - Vigencia del edital (lo que trae /api/search) en campos propios.
        - Tabla historico_cambios.
        - Las filas que entraron por /api/search tenían la VIGENCIA metida en
          data_abertura / data_encerramento. Se mueve a vigencia_* y las
          fechas de propuestas quedan nulas, marcadas con fuente 'search',
          hasta que /consulta dé las de verdad. Queda anotado en el histórico.
        """
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(licitacoes)")}
        nuevas = [
            ("data_abertura_ts", "TEXT"),
            ("data_encerramento_ts", "TEXT"),
            ("fuente_abertura", "TEXT"),
            ("fuente_encerramento", "TEXT"),
            ("vigencia_inicio", "TEXT"),
            ("vigencia_fim", "TEXT"),
            ("actualizada_em", "TEXT"),
        ]
        for col, tipo in nuevas:
            if col not in cols:
                conn.execute(f"ALTER TABLE licitacoes ADD COLUMN {col} {tipo}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS historico_cambios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                licitacao_id TEXT NOT NULL,
                campo TEXT NOT NULL,
                valor_anterior TEXT,
                valor_nuevo TEXT,
                fuente TEXT,          -- consulta / search / edital / manual / migracion_v1
                tipo TEXT,            -- cambio / relleno / correccion
                detectado_em TEXT,
                alertado INTEGER DEFAULT 0,   -- 1 = ya avisado (o no avisable)
                FOREIGN KEY (licitacao_id) REFERENCES licitacoes(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_lic "
                     "ON historico_cambios(licitacao_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_alertado "
                     "ON historico_cambios(alertado)")

        ahora = _ahora()
        # Filas del buscador de emergencia: la "fecha" era la vigencia.
        filas = conn.execute("""
            SELECT id, data_abertura, data_encerramento FROM licitacoes
            WHERE fonte = 'PNCP-search' AND fuente_encerramento IS NULL
        """).fetchall()
        for f in filas:
            for campo in ("data_abertura", "data_encerramento"):
                if f[campo]:
                    conn.execute("""
                        INSERT INTO historico_cambios (licitacao_id, campo,
                            valor_anterior, valor_nuevo, fuente, tipo,
                            detectado_em, alertado)
                        VALUES (?, ?, ?, NULL, 'migracion_v1', 'correccion', ?, 1)
                    """, (f["id"], campo, f[campo], ahora))
        conn.execute("""
            UPDATE licitacoes
               SET vigencia_inicio = data_abertura,
                   vigencia_fim = data_encerramento,
                   data_abertura = NULL,
                   data_encerramento = NULL,
                   fuente_abertura = 'search',
                   fuente_encerramento = 'search'
             WHERE fonte = 'PNCP-search' AND fuente_encerramento IS NULL
        """)
        # El resto de fechas existentes vinieron de /api/consulta (proposta,
        # publicacao o el detalle de la compra vía rellenar_fechas.py).
        conn.execute("""
            UPDATE licitacoes SET fuente_encerramento = 'consulta'
             WHERE data_encerramento IS NOT NULL AND fuente_encerramento IS NULL
        """)
        conn.execute("""
            UPDATE licitacoes SET fuente_abertura = 'consulta'
             WHERE data_abertura IS NOT NULL AND fuente_abertura IS NULL
        """)
        return len(filas)

    # =========================================================================
    # OPERACIONES CON LICITACIONES
    # =========================================================================

    def salvar_licitacao(self, licitacao: Licitacao) -> str:
        """
        Inserta la licitación o, si ya existe, actualiza lo que el PNCP haya
        cambiado. Devuelve 'nueva', 'actualizada' o 'igual'.

        Hasta el 24/09/2026 era INSERT-only: una sesión aplazada (Virginópolis
        22/09 → 02/10) o una suspensión se quedaban con el dato viejo para
        siempre. Ahora, si la fila existe:

        - Solo se tocan cierre, apertura, situación y valor estimado.
          Nunca notas, favorito, notificado ni docs_baixados.
        - Nunca se sobrescribe un valor con un nulo.
        - Las fechas de propuestas solo las escribe /consulta: lo que viene
          de /api/search es vigencia y va a vigencia_*. Una fecha puesta a
          mano (fuente 'manual') no se pisa.
        - Cada cambio queda en historico_cambios. Los de tipo 'cambio' son
          los que se avisan por email; los 'relleno' (un hueco que se llena,
          o la hora que se añade a una fecha igual) no.
        """
        lic = licitacao
        with self._conexao() as conn:
            fila = conn.execute(
                "SELECT * FROM licitacoes WHERE id = ?", (lic.id,)
            ).fetchone()

            if fila is None:
                self._insertar(conn, lic)
                return NUEVA

            actualizar: Dict[str, Any] = {}
            historial: List[Tuple[str, Any, Any, str, str]] = []

            for base, col_fuente in (("data_encerramento", "fuente_encerramento"),
                                     ("data_abertura", "fuente_abertura")):
                upd, hist = self._diff_fecha(fila, lic, base, col_fuente)
                actualizar.update(upd)
                historial.extend(hist)

            fuente = lic.fuente_fechas or "consulta"

            if lic.situacao and lic.situacao != fila["situacao"]:
                actualizar["situacao"] = lic.situacao
                historial.append(("situacao", fila["situacao"], lic.situacao, fuente,
                                  "cambio" if fila["situacao"] else "relleno"))

            # 0 o None = "no informado" (el PNCP pone 0 en los sigilosos):
            # no pisa un valor conocido.
            nuevo_valor = lic.valor_estimado
            if nuevo_valor:
                viejo_valor = fila["valor_estimado"]
                if viejo_valor is None or abs(float(viejo_valor) - float(nuevo_valor)) > 0.005:
                    actualizar["valor_estimado"] = nuevo_valor
                    historial.append(("valor_estimado", viejo_valor, nuevo_valor, fuente,
                                      "cambio" if viejo_valor else "relleno"))

            # Vigencia (solo del buscador): se guarda sin histórico, es contexto
            for col in ("vigencia_inicio", "vigencia_fim"):
                nuevo = getattr(lic, col, None)
                if nuevo and nuevo != fila[col]:
                    actualizar[col] = nuevo

            if not actualizar:
                return IGUAL

            ahora = _ahora()
            actualizar["actualizada_em"] = ahora
            sets = ", ".join(f"{c} = ?" for c in actualizar)
            conn.execute(f"UPDATE licitacoes SET {sets} WHERE id = ?",
                         (*actualizar.values(), lic.id))
            for campo, antes, despues, fuente_h, tipo in historial:
                conn.execute("""
                    INSERT INTO historico_cambios (licitacao_id, campo,
                        valor_anterior, valor_nuevo, fuente, tipo,
                        detectado_em, alertado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (lic.id, campo,
                      None if antes is None else str(antes),
                      None if despues is None else str(despues),
                      fuente_h, tipo, ahora, 0 if tipo == "cambio" else 1))
            return ACTUALIZADA

    @staticmethod
    def _diff_fecha(fila: sqlite3.Row, lic: Licitacao, base: str,
                    col_fuente: str) -> Tuple[Dict[str, Any], list]:
        """Compara una fecha de propuestas (date + ts) y decide qué escribir."""
        fuente = lic.fuente_fechas or "consulta"
        if fuente == "search":
            return {}, []          # el buscador no sabe el plazo de propuestas
        if fila[col_fuente] == "manual":
            return {}, []          # lo puesto a mano no se pisa

        nuevo_ts = getattr(lic, base + "_ts", None)
        nuevo_d = getattr(lic, base) or (nuevo_ts[:10] if nuevo_ts else None)
        if not nuevo_d:
            return {}, []          # nunca sobrescribir con nulo

        viejo_d, viejo_ts = fila[base], fila[base + "_ts"]
        upd: Dict[str, Any] = {}
        if nuevo_d != viejo_d:
            upd[base] = nuevo_d
        if nuevo_ts and nuevo_ts != viejo_ts:
            upd[base + "_ts"] = nuevo_ts
        if not upd:
            if fila[col_fuente] != fuente:
                upd[col_fuente] = fuente   # misma fecha, ahora confirmada
            return upd, []
        upd[col_fuente] = fuente

        if not viejo_d:
            tipo = "relleno"
        elif nuevo_d != viejo_d:
            tipo = "cambio"
        elif viejo_ts and nuevo_ts and viejo_ts != nuevo_ts:
            tipo = "cambio"        # mismo día, otra hora
        else:
            tipo = "relleno"       # misma fecha, solo se añade la hora
        antes = viejo_ts or viejo_d
        despues = nuevo_ts or nuevo_d
        return upd, [(base, antes, despues, fuente, tipo)]

    @staticmethod
    def _insertar(conn: sqlite3.Connection, lic: Licitacao):
        fuente = lic.fuente_fechas or "consulta"
        if fuente == "search":
            # Sin plazo de propuestas: nulas, marcadas como del buscador
            ab, ab_ts, enc, enc_ts = None, None, None, None
            f_ab = f_enc = "search"
        else:
            ab, ab_ts = lic.data_abertura, lic.data_abertura_ts
            enc, enc_ts = lic.data_encerramento, lic.data_encerramento_ts
            f_ab = fuente if ab else None
            f_enc = fuente if enc else None
        conn.execute("""
            INSERT INTO licitacoes (
                id, titulo, objeto, orgao, valor_estimado, modalidade,
                uf, municipio, data_publicacao, data_abertura,
                data_encerramento, url, fonte, cnpj_orgao, situacao,
                data_abertura_ts, data_encerramento_ts,
                fuente_abertura, fuente_encerramento,
                vigencia_inicio, vigencia_fim
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lic.id, lic.titulo, lic.objeto, lic.orgao, lic.valor_estimado,
            lic.modalidade, lic.uf, lic.municipio, lic.data_publicacao,
            ab, enc, lic.url, lic.fonte, lic.cnpj_orgao, lic.situacao,
            ab_ts, enc_ts, f_ab, f_enc,
            lic.vigencia_inicio, lic.vigencia_fim,
        ))

    def salvar_varias(self, licitacoes: List[Licitacao]) -> Dict[str, int]:
        """
        Guarda múltiples licitaciones.
        Retorna conteo de nuevas / actualizadas / iguales ('existentes' =
        actualizadas + iguales, por compatibilidad).
        """
        conteo = {NUEVA: 0, ACTUALIZADA: 0, IGUAL: 0}
        for lic in licitacoes:
            conteo[self.salvar_licitacao(lic)] += 1
        return {
            "nuevas": conteo[NUEVA],
            "actualizadas": conteo[ACTUALIZADA],
            "iguales": conteo[IGUAL],
            "existentes": conteo[ACTUALIZADA] + conteo[IGUAL],
        }

    # =========================================================================
    # HISTÓRICO DE CAMBIOS (aplazamientos, suspensiones...)
    # =========================================================================

    def historico(self, licitacao_id: str) -> List[Dict[str, Any]]:
        """Todos los cambios registrados de una licitación, del más antiguo."""
        with self._conexao() as conn:
            filas = conn.execute(
                "SELECT * FROM historico_cambios WHERE licitacao_id = ? ORDER BY id",
                (licitacao_id,),
            ).fetchall()
            return [dict(f) for f in filas]

    def cambios_pendientes_aviso(self, dias_gracia: int = 1) -> List[Dict[str, Any]]:
        """Cambios de tipo 'cambio' aún no avisados, con datos de la licitación.

        Excluye lo clasificado NO_SOFTWARE y lo que ya cerró hace más de
        dias_gracia días (un cambio en una licitación muerta no le sirve a
        nadie). Sin fecha de cierre conocida sí se avisa: no se sabe si vive.
        """
        corte = (datetime.now() - timedelta(days=dias_gracia)).strftime("%Y-%m-%d")
        with self._conexao() as conn:
            filas = conn.execute("""
                SELECT h.id AS cambio_id, h.licitacao_id, h.campo,
                       h.valor_anterior, h.valor_nuevo, h.fuente, h.detectado_em,
                       l.objeto, l.orgao, l.municipio, l.uf, l.url, l.modalidade,
                       l.situacao, l.data_encerramento, l.data_encerramento_ts,
                       t.plataforma, t.muro_economico, t.bucket
                FROM historico_cambios h
                JOIN licitacoes l ON l.id = h.licitacao_id
                LEFT JOIN triaje t ON t.licitacao_id = h.licitacao_id
                WHERE h.alertado = 0 AND h.tipo = 'cambio'
                  AND (t.bucket IS NULL OR t.bucket != 'NO_SOFTWARE')
                  AND (l.data_encerramento IS NULL OR l.data_encerramento >= ?)
                ORDER BY l.data_encerramento, h.licitacao_id, h.id
            """, (corte,)).fetchall()
            return [dict(f) for f in filas]

    def marcar_cambios_avisados(self, cambio_ids: List[int]):
        if not cambio_ids:
            return
        with self._conexao() as conn:
            conn.executemany(
                "UPDATE historico_cambios SET alertado = 1 WHERE id = ?",
                [(i,) for i in cambio_ids],
            )
    
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

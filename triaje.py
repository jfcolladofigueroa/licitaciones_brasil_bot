"""
TRIAJE POR REGLAS (SIN API DE IA)
==================================
Clasifica cada carpeta descargada en buckets y marca banderas de "muros"
según el perfil de la empresa. Nada se borra: las NO_SOFTWARE se mueven a
descargas/NO_SOFTWARE/ y todo queda registrado en la tabla `triaje` y en
el shortlist candidatas.csv (ordenado con las sin-muros arriba).

Buckets (recall-first: solo se aparta lo confirmado no-software):
    NO_SOFTWARE  objeto con keyword de alta confianza y sin señal de software
    CANDIDATA    todo lo demás (señal de software o ambiguo)

Flags por candidata — REGLA DE ORO: el ÚNICO muro que descarta es el
económico (PL); todo lo demás se ETIQUETA para revisión manual de Jose:
    muro_economico   SI/NO/?  balanço + índices/PL → única causa de descarte
    poc              SI/NO    prova de conceito/amostra/demonstração (etiqueta)
    fabrica_pf       SI/NO    pontos de função (etiqueta)
    spec_pesada      SI/NO    gov.br login/ICP-Brasil/app nativo/migração massiva
    software_publico SI/NO    i-Educar/e-Cidade/livre → ACCESIBLE, sube arriba
    atestado_exigido texto    requisito de atestado extraído (Jose decide)
    poc_roteiro      texto    roteiro/funcionalidades exigidas na PoC

Uso:
    python triaje.py                  # tría las pendientes (según la DB)
    python triaje.py --todo           # (re)tría todas las carpetas de descargas/
    python triaje.py --carpeta PNCP-… # una sola carpeta
    python triaje.py --solo-csv       # solo regenerar candidatas.csv
    python triaje.py --forzar         # ignora la caché de texto extraído
"""

import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from apis_licitacoes import _normalizar
from database import DatabaseLicitacoes
from extractor_texto import ExtractorTexto

NO_SOFTWARE_DIR = "NO_SOFTWARE"
LISTAS_DIR = "LISTAS_PARA_MI"   # candidatas sin muro económico -> listas para revisar
CSV_SALIDA = "candidatas.csv"

# Listas por defecto (se sobreescriben desde config.json > triaje)
NO_SOFTWARE_DEFAULT = [
    "medicamento", "obra", "veículo", "combustível", "gêneros alimentícios",
    "alimento", "merenda", "pneu", "mobiliário", "móveis", "limpeza",
    "uniforme", "hospitalar", "equipamento médico", "equipamentos médicos",
    "tubo", "brita", "pedra", "bomba", "tratamento de água",
    "tratamento de esgoto", "esgoto", "livro", "coleta de resíduos",
    "resíduos sólidos",
    # --- ampliación 19/08: categorías que colaban por "sistema"/"plataforma" ---
    "extintor", "extintores", "iluminação pública", "luminária", "luminárias",
    "marketing", "publicidade", "propaganda", "assessoria de imprensa",
    "treinamento organizacional", "curso", "cursos", "capacitação presencial",
    "material didático", "passagens aéreas", "agenciamento de viagens",
    "seguro", "vigilância", "portaria", "recepcionista", "motorista",
    "mão de obra", "terceirização", "reforma", "pavimentação", "asfalto",
    "construção", "ar condicionado", "climatização", "elevador",
    "gás liquefeito", "água mineral", "cesta básica", "fardamento",
    "papelaria", "expediente", "toner", "cartucho", "impressora",
    "manutenção predial", "jardinagem", "poda", "roçada", "dedetização",
    "exame", "consulta médica", "odontológic", "laboratorial",
    "telemedicina", "radiologia", "aerofotogrametria",
]
# Señales INEQUÍVOCAS: por sí solas bastan para rescatar el objeto
SENAL_FUERTE_DEFAULT = [
    "software", "aplicativo", "saas", "fábrica de software",
    "licenciamento de uso", "cessão de programa", "chatbot",
    "assistente virtual", "canal de denúncia", "cesta de preços",
    "banco de preços", "pesquisa de preços", "painel de preços",
    "solução digital", "solução tecnológica", "tecnologia da informação",
    "governo digital", "transformação digital", "nuvem", "web",
    "ponto de função", "pontos de função", "informatizado",
    "gestão eletrônica de documentos", "whatsapp", "erp",
]

# Señales AMBIGUAS: aparecen en cualquier objeto ("sistema de iluminação",
# "plataforma elevatória", "desenvolvimento urbano"...). Solo cuentan como
# señal de software si además hay un término de CONTEXTO_TI en el objeto.
SENAL_DEBIL_DEFAULT = [
    "sistema", "plataforma", "desenvolvimento", "portal", "site",
    "website", "app", "hospedagem", "solução", "licença", "digital",
]
CONTEXTO_TI_DEFAULT = [
    "software", "web", "nuvem", "saas", "informatizado", "informática",
    "tecnologia da informação", "aplicativo", "dados", "banco de dados",
    "usuário", "usuários", "login", "senha", "módulo", "módulos",
    "implantação", "licenciamento", "licença de uso", "servidor",
    "internet", "online", "eletrônico", "eletrônica", "digital",
    "integração", "api", "gestão pública", "computador",
]
# Contextos donde la palabra-señal NO indica software: se quitan solo para
# la búsqueda de señales (el chequeo no-software ve el texto entero)
FALSOS_AMIGOS_DEFAULT = [
    "plataforma elevatória", "plataforma de embarque",
    "desenvolvimento urbano", "desenvolvimento rural",
    "desenvolvimento social", "desenvolvimento econômico",
    "desenvolvimento sustentável", "desenvolvimento humano",
    "desenvolvimento infantil", "desenvolvimento agrário",
    "desenvolvimento regional", "desenvolvimento da educação",
    "sistema de abastecimento", "sistema de esgotamento", "sistema de esgoto",
    "sistema viário", "sistema de drenagem", "sistema fotovoltaico",
    "sistema de climatização", "sistema de irrigação",
    "sistema de combate a incêndio",
    "sistema de iluminação", "sistema de fixação", "sistema de ar condicionado",
    "sistema de tratamento", "sistema de bombeamento", "sistema construtivo",
    "sistema de refrigeração", "sistema de aquecimento", "sistema de som",
    "sistema de segurança patrimonial", "sistema de proteção",
    "sistema prisional", "sistema único de saúde", "sistema nacional",
    "sistema de ensino", "sistema de transporte", "sistema de captação",
    "plataforma de perfuração", "plataforma giratória",
]


def _contiene(termo: str, texto_norm: str) -> bool:
    """Busca el término (normalizado, con tolerancia de plural) en el texto."""
    return bool(re.search(
        r"\b" + re.escape(_normalizar(termo)) + r"(s|es)?\b", texto_norm
    ))


def _quitar_frase(frase: str, texto_norm: str) -> str:
    """Elimina la frase (normalizada, con tolerancia de plural por palabra)."""
    patron = r"\s+".join(
        re.escape(p) + r"(s|es)?" for p in _normalizar(frase).split()
    )
    return re.sub(patron, " ", texto_norm)


def _sesion_futura(*fechas) -> bool:
    """True si la sesión (cierre de propuestas) es hoy o futura (o desconocida).

    OJO con los campos del PNCP:
      - dataAberturaProposta  -> cuándo EMPIEZAN a recibirse propuestas (fecha temprana)
      - dataEncerramentoProposta -> CIERRE / sesión pública de disputa (fecha real)
    El filtro debe usar el CIERRE, no la apertura. Se aceptan varias fechas y se
    usa la más tardía conocida (la de cierre); si ninguna es parseable, no descarta.
    """
    from datetime import date, datetime
    mejor = None
    for f in fechas:
        if not f:
            continue
        s = str(f)[:10]
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                d = datetime.strptime(s, fmt).date()
                if mejor is None or d > mejor:
                    mejor = d
                break
            except ValueError:
                continue
    if mejor is None:
        return True  # sin fecha conocida: no descartar
    return mejor >= date.today()


class TriadorLicitacoes:
    """Aplica las reglas de triaje sobre las carpetas descargadas"""

    def __init__(self, config: dict, db: DatabaseLicitacoes):
        self.config = config
        self.db = db
        self.extractor = ExtractorTexto()

        triaje_cfg = config.get("triaje", {})
        self.no_software = triaje_cfg.get("no_software", NO_SOFTWARE_DEFAULT)
        self.senal_fuerte = triaje_cfg.get("senal_software", SENAL_FUERTE_DEFAULT)
        self.falsos_amigos = triaje_cfg.get("falsos_amigos", FALSOS_AMIGOS_DEFAULT)
        self.senal_debil = triaje_cfg.get("senal_debil", SENAL_DEBIL_DEFAULT)
        self.contexto_ti = triaje_cfg.get("contexto_ti", CONTEXTO_TI_DEFAULT)
        self.dias_alerta = triaje_cfg.get("dias_alerta_vencimiento", 7)

        perfil = config.get("perfil", {})
        self.pl_positivo = perfil.get("PL_positivo", False)

        self.pasta = Path(config.get("descargas", {}).get("pasta_salida", "descargas"))

    # =========================================================================
    # REGLAS
    # =========================================================================

    def clasificar(self, objeto: str) -> str:
        """Bucket a partir del OBJETO (recall-first: lo dudoso se queda)."""
        # Sin los tags de metadato ni el boilerplate "registro de preços",
        # igual que apis_licitacoes.Licitacao.contem_termo
        texto = re.sub(r"\[[^\]]*\]", " ", objeto or "")
        texto = _normalizar(texto)
        texto = re.sub(r"(sistema de )?registro de preco[s]?", " ", texto)

        texto_senal = texto
        for fa in self.falsos_amigos:
            texto_senal = _quitar_frase(fa, texto_senal)

        es_no_software = any(_contiene(t, texto) for t in self.no_software)

        # Señal inequívoca: basta por sí sola
        tiene_senal = any(_contiene(t, texto_senal) for t in self.senal_fuerte)

        # Señal ambigua ("sistema", "plataforma", "desenvolvimento"...):
        # solo cuenta si el objeto trae además contexto informático.
        if not tiene_senal:
            hay_debil = any(_contiene(t, texto_senal) for t in self.senal_debil)
            hay_contexto = any(_contiene(t, texto_senal) for t in self.contexto_ti)
            tiene_senal = hay_debil and hay_contexto

        if es_no_software and not tiene_senal:
            return "NO_SOFTWARE"
        return "CANDIDATA"

    def detectar_muros(self, texto_norm: str) -> Dict[str, str]:
        """Flags sobre el texto completo (edital + anexos/ETP).

        REGLA DE ORO: solo muro_economico manda a descarte; el resto etiqueta.
        """
        return {
            "muro_economico": self._muro_economico(texto_norm),
            "poc": self._poc(texto_norm),
            "fabrica_pf": "SI" if re.search(r"ponto(s)? de funcao", texto_norm) else "NO",
            "spec_pesada": "SI" if re.search(
                r"icp[- ]brasil|play store|app store|aplicativo nativo"
                r"|migracao massiva|login unico|conta gov\.br|autenticacao gov\.br",
                texto_norm
            ) else "NO",
            "software_publico": "SI" if re.search(
                r"i-?educar|e-?cidade|software publico|software livre|codigo aberto",
                texto_norm
            ) else "NO",
        }

    def _muro_economico(self, texto: str) -> str:
        if not texto.strip():
            return "?"

        balanco = re.search(r"balanco patrimonial", texto)
        indices = re.search(
            r"indices? de liquidez|liquidez geral|liquidez corrente"
            r"|solvencia geral|patrimonio liquido|capital social minimo|capital minimo",
            texto
        )
        pl_directo = re.search(
            r"patrimonio liquido (minimo|de \d|nao inferior|igual ou superior)", texto
        )
        if (balanco and indices) or pl_directo:
            return "SI"

        dispensa = re.search(
            r"nao sera exigid[ao][^.]{0,80}economico[- ]financeira"
            r"|dispensad[ao][^.]{0,80}economico[- ]financeira",
            texto
        )
        solo_falencia = re.search(r"certidao (negativa )?de falencia", texto)
        if dispensa or solo_falencia:
            return "NO"

        return "?"

    def _poc(self, texto: str) -> str:
        """PoC/amostra/demonstração: etiqueta SI/NO (con o sin desclassificação).

        Nunca descarta: si el producto es alcanzable lo juzga Jose leyendo
        poc_roteiro en Cowork.
        """
        # "demonstracao" a secas también matchea "demonstração prática";
        # se evita el falso positivo contable ("demonstrações contábeis")
        return "SI" if re.search(
            r"prova de conceito|amostra|demonstracao(?! contab)", texto
        ) else "NO"

    def extraer_campos(self, texto_norm: str) -> Dict[str, Any]:
        """Plataforma, ME/EPP, valor (fallback), atestado y roteiro de PoC."""
        return {
            "plataforma": self._plataforma(texto_norm),
            "me_epp": self._me_epp(texto_norm),
            "valor_extraido": self._valor(texto_norm),
            "atestado_exigido": self._atestado(texto_norm),
            "poc_roteiro": self._roteiro_poc(texto_norm),
        }

    def _atestado(self, texto: str) -> str:
        """Recorte del requisito de atestado (etiqueta: nunca descarta)."""
        m = re.search(
            r"atestad[oa]s?\b[^.]{0,80}?"
            r"(capacidade tecnica|capacitacao tecnica|aptidao|desempenho anterior)",
            texto
        )
        if not m:
            m = re.search(r"atestad[oa]s? (de|que comprove|emitid|fornecid|expedid)", texto)
        if not m:
            return ""
        recorte = texto[m.start():m.start() + 600]
        return re.sub(r"\s+", " ", recorte).strip()

    def _roteiro_poc(self, texto: str) -> str:
        """Bloque del roteiro/funcionalidades exigidas na PoC (para juzgar
        en Cowork si el producto es alcanzable)."""
        m = re.search(
            r"roteiro d[ae][^\n]{0,40}(prova de conceito|poc\b|demonstracao|amostra)"
            r"|(prova de conceito|demonstracao(?! contab))[^\n]{0,80}"
            r"(roteiro|funcionalidade|requisito|criterio)",
            texto
        )
        if not m:
            m = re.search(r"prova de conceito|demonstracao(?! contab)|amostra", texto)
        if not m:
            return ""
        recorte = texto[m.start():m.start() + 800]
        return re.sub(r"\s+", " ", recorte).strip()

    def _plataforma(self, texto: str) -> str:
        plataformas = [
            ("comprasnet.ba", r"comprasnet\.ba"),
            ("compras.gov.br", r"compras\.gov\.br|comprasnet|portal de compras do governo federal"),
            ("portaldecompraspublicas", r"portal de compras publicas|portaldecompraspublicas"),
            ("bll", r"\bbll\b|bllcompras"),
            ("bnc", r"\bbnc\b|bnccompras|bolsa nacional de compras"),
            ("licitanet", r"licitanet"),
            ("licitar.digital", r"licitar\.digital"),
            ("banrisul", r"pregao ?online ?banrisul|pregaobanrisul"),
        ]
        for nome, patron in plataformas:
            if re.search(patron, texto):
                return nome
        return "?"

    def _me_epp(self, texto: str) -> str:
        exclusiva = re.search(
            r"(participacao|disputa|licitacao|item|lote|contratacao) exclusiv\w+"
            r"[^.\n]{0,120}(me/epp|me e epp|microempresa|empresa de pequeno porte)"
            r"|exclusiv\w+ (para|as?|aos?) [^.\n]{0,80}"
            r"(me/epp|microempresa|empresa de pequeno porte)"
            r"|destinad[ao] exclusivamente[^.\n]{0,80}"
            r"(me/epp|microempresa|empresa de pequeno porte)",
            texto
        )
        if exclusiva:
            return "exclusiva"
        if re.search(r"ampla (concorrencia|participacao|disputa)", texto):
            return "ampla"
        return "?"

    def _valor(self, texto: str) -> Optional[float]:
        valores = []
        for m in re.finditer(
            r"valor (?:total|global|maximo|estimado|de referencia)"
            r"[^\n]{0,60}?r\$ ?([\d\.]+,\d{2})",
            texto
        ):
            try:
                valores.append(float(m.group(1).replace(".", "").replace(",", ".")))
            except ValueError:
                continue
        return max(valores) if valores else None

    # =========================================================================
    # TRIAJE DE CARPETAS
    # =========================================================================

    def triar_id(self, licitacao_id: str, forzar: bool = False) -> Dict[str, Any]:
        """Tría una licitación: extrae texto, clasifica, marca flags y guarda."""
        fila = self.db.obtener_por_id(licitacao_id)
        objeto = (fila or {}).get("objeto") or ""

        carpeta = self.pasta / licitacao_id
        movida = self.pasta / NO_SOFTWARE_DIR / licitacao_id
        if not carpeta.exists() and movida.exists():
            carpeta = movida

        texto = ""
        if carpeta.exists():
            try:
                texto = self.extractor.extraer_carpeta(carpeta, forzar=forzar)
            except Exception as e:
                print(f"   ⚠️ Extracción falló en {licitacao_id}: {type(e).__name__}: {e}")

        texto_norm = _normalizar(texto)
        bucket = self.clasificar(objeto or texto[:2000])

        resultado: Dict[str, Any] = {"bucket": bucket, "texto_ok": bool(texto.strip())}
        if bucket == "CANDIDATA":
            resultado.update(self.detectar_muros(texto_norm))
            resultado.update(self.extraer_campos(texto_norm))

        self.db.guardar_triaje(licitacao_id, resultado)

        if bucket == "NO_SOFTWARE":
            self._mover_no_software(licitacao_id)
        else:
            self._restaurar_candidata(licitacao_id)
            # NOTA: el bot NO copia automáticamente a LISTAS_PARA_MI.
            # Flujo acordado: el bot solo escribe en descargas/ (y NO_SOFTWARE/);
            # la curaduría hacia LISTAS_PARA_MI la hace Claude/Jose manualmente.
            # (Se conserva _marcar_lista por si se quiere reactivar en el futuro.)

        return resultado

    def _marcar_lista(self, licitacao_id: str):
        """Copia la carpeta a descargas/LISTAS_PARA_MI/ (candidata sin muro-PL,
        lista para revisar). Copia, no mueve: el original sigue en descargas/."""
        origen = self.pasta / licitacao_id
        if not origen.exists():
            return
        destino_dir = self.pasta / LISTAS_DIR
        destino_dir.mkdir(exist_ok=True)
        destino = destino_dir / licitacao_id
        if destino.exists():
            return  # ya copiada en una corrida anterior
        try:
            shutil.copytree(str(origen), str(destino))
            print(f"   ⭐ → {LISTAS_DIR}/{licitacao_id}")
        except Exception as e:
            print(f"   ⚠️ No se pudo copiar a {LISTAS_DIR}: {type(e).__name__}: {e}")

    def _mover_no_software(self, licitacao_id: str):
        """Mueve la carpeta a descargas/NO_SOFTWARE/ (nada se borra)."""
        origen = self.pasta / licitacao_id
        if not origen.exists():
            return
        destino_dir = self.pasta / NO_SOFTWARE_DIR
        destino_dir.mkdir(exist_ok=True)
        destino = destino_dir / licitacao_id
        if destino.exists():
            return  # ya movida en una corrida anterior
        shutil.move(str(origen), str(destino))
        print(f"   📦 → {NO_SOFTWARE_DIR}/{licitacao_id}")

    def _restaurar_candidata(self, licitacao_id: str):
        """Devuelve a descargas/ una carpeta que había ido a NO_SOFTWARE/
        (p.ej. tras ampliar las señales de software)."""
        origen = self.pasta / NO_SOFTWARE_DIR / licitacao_id
        destino = self.pasta / licitacao_id
        if not origen.exists() or destino.exists():
            return
        shutil.move(str(origen), str(destino))
        print(f"   📦 ← restaurada de {NO_SOFTWARE_DIR}/")

    def triar_varias(self, ids: List[str], forzar: bool = False) -> Dict[str, int]:
        """Tría una lista de ids con resumen de buckets."""
        conteo = {"CANDIDATA": 0, "NO_SOFTWARE": 0, "sin_texto": 0}
        for i, lic_id in enumerate(ids, 1):
            print(f"[{i}/{len(ids)}] {lic_id}", end=" ")
            try:
                r = self.triar_id(lic_id, forzar=forzar)
            except Exception as e:
                print(f"⚠️ error: {type(e).__name__}: {e}")
                continue
            conteo[r["bucket"]] = conteo.get(r["bucket"], 0) + 1
            if not r["texto_ok"]:
                conteo["sin_texto"] += 1
            extra = "" if r["bucket"] == "NO_SOFTWARE" else (
                f"eco={r.get('muro_economico')} poc={r.get('poc')} "
                f"pf={r.get('fabrica_pf')} pub={r.get('software_publico')}"
            )
            print(f"→ {r['bucket']} {extra}")
        return conteo

    def triar_pendientes(self, forzar: bool = False) -> Dict[str, int]:
        """Tría lo descargado que aún no pasó por el triaje."""
        ids = self.db.pendientes_triaje()
        if not ids:
            print("✅ No hay licitaciones pendientes de triaje.")
            return {}
        print(f"🔎 Triando {len(ids)} licitaciones pendientes...")
        return self.triar_varias(ids, forzar=forzar)

    def triar_todo(self, forzar: bool = False) -> Dict[str, int]:
        """(Re)tría todas las carpetas de descargas/ (backlog completo)."""
        ids = sorted(
            d.name for d in self.pasta.glob("PNCP-*") if d.is_dir()
        )
        ids += sorted(
            d.name for d in (self.pasta / NO_SOFTWARE_DIR).glob("PNCP-*")
            if d.is_dir()
        ) if (self.pasta / NO_SOFTWARE_DIR).exists() else []
        print(f"🔎 Triando TODAS las carpetas: {len(ids)}")
        return self.triar_varias(ids, forzar=forzar)

    # =========================================================================
    # SHORTLIST (CSV + ALERTAS)
    # =========================================================================

    def _descarte(self, fila: Dict[str, Any]) -> str:
        """REGLA DE ORO: el ÚNICO muro que manda a descarte es el económico
        (mientras el perfil no tenga PL positivo). PoC, atestado, fábrica-PF
        y spec pesada son etiquetas para revisión manual."""
        if fila.get("muro_economico") == "SI" and not self.pl_positivo:
            return "SI"
        return "NO"

    def _candidatas_ordenadas(self) -> List[Dict[str, Any]]:
        hoy = datetime.now().strftime("%Y-%m-%d")
        filas = self.db.obtener_candidatas()
        for f in filas:
            f["descarte"] = self._descarte(f)
            f["valor"] = f.get("valor_estimado") or f.get("valor_extraido")
            f["piso_50"] = round(f["valor"] * 0.5, 2) if f["valor"] else None
            enc = f.get("data_encerramento")
            f["estado"] = "?" if not enc else ("ABIERTA" if enc >= hoy else "CERRADA")
        # Abiertas primero; software público (accesible) arriba; descartadas
        # por muro económico al fondo; dentro, por cierre más próximo
        filas.sort(key=lambda f: (
            f["estado"] == "CERRADA",
            f["descarte"] == "SI",
            f.get("software_publico") != "SI",
            f.get("data_encerramento") or "9999-12-31",
            f["id"],
        ))
        return filas

    def generar_csv(self, ruta: str = CSV_SALIDA) -> int:
        """Regenera el shortlist completo desde la DB. Devuelve nº de filas."""
        filas = self._candidatas_ordenadas()

        columnas = [
            "carpeta", "estado", "descarte", "software_publico",
            "muro_economico", "poc", "fabrica_pf", "spec_pesada",
            "objeto", "orgao", "cnpj", "uf", "municipio",
            "data_encerramento", "data_abertura", "valor", "piso_50",
            "plataforma", "me_epp", "modalidade",
            "atestado_exigido", "poc_roteiro", "texto_ok", "link",
        ]
        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(columnas)
            for fila in filas:
                writer.writerow([
                    fila["id"], fila["estado"], fila["descarte"],
                    fila.get("software_publico"), fila.get("muro_economico"),
                    fila.get("poc"), fila.get("fabrica_pf"), fila.get("spec_pesada"),
                    (fila.get("objeto") or "")[:400].replace("\n", " "),
                    fila.get("orgao"), fila.get("cnpj_orgao"),
                    fila.get("uf"), fila.get("municipio"),
                    fila.get("data_encerramento"), fila.get("data_abertura"),
                    fila.get("valor"), fila.get("piso_50"),
                    fila.get("plataforma"), fila.get("me_epp"),
                    fila.get("modalidade"),
                    (fila.get("atestado_exigido") or "")[:600],
                    (fila.get("poc_roteiro") or "")[:800],
                    fila.get("texto_ok"), fila.get("url"),
                ])
        print(f"📄 {ruta}: {len(filas)} candidatas "
              f"(software público arriba, muro económico al fondo)")
        return len(filas)

    def candidatas_alerta(self) -> List[Dict[str, Any]]:
        """Sin muro económico, no notificadas y con cierre dentro del horizonte."""
        hoy = datetime.now().strftime("%Y-%m-%d")
        limite = (datetime.now() + timedelta(days=self.dias_alerta)).strftime("%Y-%m-%d")
        return [
            f for f in self._candidatas_ordenadas()
            if f["descarte"] == "NO"
            and not f.get("notificado")
            and f.get("data_encerramento")
            and hoy <= f["data_encerramento"] <= limite
        ]


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Triaje por reglas de licitaciones")
    parser.add_argument("--todo", action="store_true", help="(Re)triar todas las carpetas")
    parser.add_argument("--carpeta", help="Triar una sola: PNCP-{cnpj}-{ano}-{seq}")
    parser.add_argument("--solo-csv", action="store_true", help="Solo regenerar el CSV")
    parser.add_argument("--forzar", action="store_true", help="Ignorar caché de texto")
    parser.add_argument("--config", default="config.json", help="Archivo de config")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    db = DatabaseLicitacoes(config.get("database", "licitacoes.db"))
    triador = TriadorLicitacoes(config, db)

    if args.solo_csv:
        triador.generar_csv()
        return

    if args.carpeta:
        r = triador.triar_id(args.carpeta, forzar=args.forzar)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.todo:
        conteo = triador.triar_todo(forzar=args.forzar)
        print(f"\n📊 Resumen: {conteo}")
    else:
        conteo = triador.triar_pendientes(forzar=args.forzar)
        if conteo:
            print(f"\n📊 Resumen: {conteo}")

    triador.generar_csv()


if __name__ == "__main__":
    main()

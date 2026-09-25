"""
EXTRACTOR DE TEXTO POR CARPETA
===============================
Extrae el texto de todos los documentos de una carpeta de licitación
(los zip/rar ya vienen extraídos por descargador_documentos.py):

- PDF con texto     → pdftotext -layout
- PDF escaneado     → OCR con tesseract (2 primeras páginas)
- docx / odt        → zipfile + limpieza de tags XML (stdlib)
- doc / rtf / html  → textutil (incluido en macOS)
- txt               → lectura directa

El resultado se cachea por carpeta en _texto_extraido.txt (+ _texto_meta.json
con la lista de archivos procesados): si la carpeta no cambió, no se reprocesa.
"""

import html
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

CACHE_TXT = "_texto_extraido.txt"
CACHE_META = "_texto_meta.json"
# Subido a 2 el 18/09/2026 al corregir el recorte: invalida todas las cachés
# antiguas, que se generaron cortando el final del edital y por tanto pueden no
# contener la habilitación. Sin esto, los .txt truncados seguirían usándose.
CACHE_VERSION = 2

# ─────────────────────────────────────────────────────────────────────────────
# TOPES DE TEXTO — reescritos el 18/09/2026
#
# El tope anterior era 300.000 por archivo y se aplicaba con `texto[:300_000]`,
# es decir, cortando por el FINAL. Eso es exactamente al revés de lo que hace
# falta: en un edital brasileño la habilitación va al final, después de todas
# las cláusulas de procedimiento. Lo que el corte tiraba era siempre la parte
# que decide si podemos presentarnos.
#
# Casos medidos el 18/09/2026:
#   · Santa Mariana/PR (PNCP-75392019000120-2026-59): el txt cortaba en la
#     página 67 de 112 y no contenía la sección 8.3 de habilitación. El
#     detector dio "sin muro económico" sin haber leído nunca ese apartado.
#   · Itaguaçu/ES (PNCP-27357128000163-2026-5): 311.202 de 402.340 caracteres.
#     La cláusula b.2.3, que es la que bloquea la licitación, estaba fuera.
#   · Aratuípe/BA: 310.336 de 629.375. Habilitación entera fuera.
#
# Ahora: tope mucho más alto y, si aun así hay que recortar, se conserva
# principio Y final con una marca visible en medio. El principio lleva el
# objeto y el procedimiento; el final, la habilitación y los anexos técnicos.
# ─────────────────────────────────────────────────────────────────────────────
MAX_TEXTO_ARCHIVO = 1_200_000  # caracteres por archivo
MAX_TEXTO_CARPETA = 6_000_000  # caracteres por carpeta
FRACCION_CABEZA = 0.55         # al recortar, qué parte se guarda del principio

MARCA_RECORTE = "\n[... RECORTE: se omitió el centro del documento ...]\n"


def _recortar(texto: str, tope: int) -> str:
    """Recorta por el CENTRO, nunca por el final.

    Guardar los primeros N caracteres es el error que se corrigió el
    18/09/2026: la habilitación (índices, patrimônio líquido, atestado) está
    casi siempre en el último tercio del edital, y era justo lo que se perdía.
    Conservando cabeza y cola, el objeto sigue al principio y los requisitos de
    habilitación siguen al final.

    La marca en medio es deliberada: si algo aguas abajo lee este texto, tiene
    que poder ver que falta material en vez de creer que lo tiene entero.
    """
    if len(texto) <= tope:
        return texto
    util = tope - len(MARCA_RECORTE)
    cabeza = int(util * FRACCION_CABEZA)
    cola = util - cabeza
    return texto[:cabeza] + MARCA_RECORTE + texto[-cola:]

# Umbral de PDF escaneado: menos de ~100 caracteres por página = sin capa de texto
MIN_CHARS_POR_PAGINA = 100
OCR_PAGINAS = 2      # solo las 2 primeras páginas (suficiente para objeto/encabezado)
OCR_DPI = 200

EXTENSIONES = {".pdf", ".docx", ".doc", ".rtf", ".odt", ".html", ".htm", ".txt"}

# Orden de lectura: primero los documentos con más señal
_PRIORIDAD = [
    (0, r"edital"),
    (1, r"termo[ _\-]?de[ _\-]?referencia|(^|[ _\-])tr([ _\-\.]|$)"),
    (2, r"etp|estudo[ _\-]?tecnico"),
    (3, r"anexo"),
]


def _normalizar_nombre(nombre: str) -> str:
    """minúsculas sin acentos para clasificar por nombre de archivo"""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", nombre.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class ExtractorTexto:
    """Extrae y cachea el texto de una carpeta de licitación"""

    def __init__(self, tessdata_dir: Optional[str] = None):
        # tessdata del proyecto (con por.traineddata) si existe
        self.tessdata_dir = None
        if tessdata_dir:
            candidato = Path(tessdata_dir)
        else:
            candidato = Path(__file__).parent / "tessdata"
        if (candidato / "por.traineddata").exists():
            self.tessdata_dir = candidato
        self._ocr_lang = "por" if self.tessdata_dir else "eng"

    # =========================================================================
    # API PRINCIPAL
    # =========================================================================

    def extraer_carpeta(self, carpeta: Path, forzar: bool = False) -> str:
        """
        Devuelve el texto completo de la carpeta (concatenado con separadores
        por archivo). Usa la caché si nada cambió.
        """
        carpeta = Path(carpeta)
        archivos = self._listar_archivos(carpeta)
        firma = {str(a.relative_to(carpeta)): a.stat().st_size for a in archivos}

        if not forzar and self._cache_valida(carpeta, firma):
            return (carpeta / CACHE_TXT).read_text(encoding="utf-8", errors="replace")

        partes: List[str] = []
        total = 0
        errores = 0
        for arquivo in archivos:
            if total >= MAX_TEXTO_CARPETA:
                partes.append("\n[... tope de texto por carpeta alcanzado ...]\n")
                break
            texto = self._extraer_archivo(arquivo)
            if texto is None:
                errores += 1
                continue
            texto = _recortar(texto, MAX_TEXTO_ARCHIVO)
            if not texto.strip():
                continue
            partes.append(f"\n===== {arquivo.relative_to(carpeta)} =====\n{texto}")
            total += len(texto)

        resultado = "".join(partes)

        # Guardar caché
        try:
            (carpeta / CACHE_TXT).write_text(resultado, encoding="utf-8")
            meta = {"version": CACHE_VERSION, "archivos": firma, "errores": errores}
            (carpeta / CACHE_META).write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError as e:
            print(f"   ⚠️ No se pudo guardar caché en {carpeta.name}: {e}")

        return resultado

    def _listar_archivos(self, carpeta: Path) -> List[Path]:
        """Archivos extraíbles de la carpeta, ordenados por prioridad de señal."""
        archivos = [
            a for a in carpeta.rglob("*")
            if a.is_file()
            and a.suffix.lower() in EXTENSIONES
            and not a.name.startswith("_texto_")
        ]

        def prioridad(a: Path) -> tuple:
            nombre = _normalizar_nombre(a.name)
            for orden, patron in _PRIORIDAD:
                if re.search(patron, nombre):
                    return (orden, len(a.parts), nombre)
            return (9, len(a.parts), nombre)

        return sorted(archivos, key=prioridad)

    def _cache_valida(self, carpeta: Path, firma: Dict[str, int]) -> bool:
        meta_path = carpeta / CACHE_META
        if not meta_path.exists() or not (carpeta / CACHE_TXT).exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return meta.get("version") == CACHE_VERSION and meta.get("archivos") == firma

    # =========================================================================
    # EXTRACCIÓN POR TIPO
    # =========================================================================

    def _extraer_archivo(self, arquivo: Path) -> Optional[str]:
        """Texto de un archivo, o None si falló la extracción."""
        ext = arquivo.suffix.lower()
        try:
            if ext == ".pdf":
                return self._extraer_pdf(arquivo)
            if ext == ".docx":
                return self._extraer_zip_xml(arquivo, "word/document.xml")
            if ext == ".odt":
                return self._extraer_zip_xml(arquivo, "content.xml")
            if ext in (".doc", ".rtf", ".html", ".htm"):
                return self._extraer_textutil(arquivo)
            if ext == ".txt":
                return arquivo.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"   ⚠️ Error extrayendo {arquivo.name}: {type(e).__name__}: {e}")
        return None

    def _extraer_pdf(self, arquivo: Path) -> Optional[str]:
        """pdftotext; si el PDF no tiene capa de texto (escaneado), OCR."""
        resultado = subprocess.run(
            ["pdftotext", "-layout", "-q", str(arquivo), "-"],
            capture_output=True, text=True, errors="replace", timeout=120
        )
        texto = resultado.stdout if resultado.returncode == 0 else ""

        # \f separa páginas en la salida de pdftotext
        paginas = max(texto.count("\f"), 1)
        if len(texto.strip()) / paginas >= MIN_CHARS_POR_PAGINA:
            return texto

        ocr = self._ocr_pdf(arquivo)
        if ocr and len(ocr.strip()) > len(texto.strip()):
            return f"[OCR {self._ocr_lang}, {OCR_PAGINAS} primeras páginas]\n{ocr}"
        return texto

    def _ocr_pdf(self, arquivo: Path) -> Optional[str]:
        """OCR de las primeras páginas con pdftoppm + tesseract."""
        with tempfile.TemporaryDirectory() as tmp:
            prefijo = Path(tmp) / "pag"
            render = subprocess.run(
                ["pdftoppm", "-png", "-r", str(OCR_DPI),
                 "-f", "1", "-l", str(OCR_PAGINAS), str(arquivo), str(prefijo)],
                capture_output=True, timeout=120
            )
            if render.returncode != 0:
                return None

            textos = []
            for png in sorted(Path(tmp).glob("pag*.png")):
                cmd = ["tesseract", str(png), "stdout", "-l", self._ocr_lang]
                if self.tessdata_dir:
                    cmd[1:1] = ["--tessdata-dir", str(self.tessdata_dir)]
                ocr = subprocess.run(
                    cmd, capture_output=True, text=True, errors="replace", timeout=120
                )
                if ocr.returncode == 0:
                    textos.append(ocr.stdout)
            return "\n".join(textos) if textos else None

    def _extraer_zip_xml(self, arquivo: Path, ruta_xml: str) -> Optional[str]:
        """docx/odt: son zips con un XML principal; se limpian los tags."""
        with zipfile.ZipFile(arquivo) as zf:
            with zf.open(ruta_xml) as f:
                xml = f.read().decode("utf-8", errors="replace")
        # Saltos de párrafo antes de quitar tags
        xml = re.sub(r"</(w:p|text:p|text:h)>", "\n", xml)
        xml = re.sub(r"<(w:tab|text:tab)[^>]*/?>", "\t", xml)
        texto = re.sub(r"<[^>]+>", "", xml)
        return html.unescape(texto)

    def _extraer_textutil(self, arquivo: Path) -> Optional[str]:
        """doc/rtf/html con textutil (incluido en macOS)."""
        resultado = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(arquivo)],
            capture_output=True, text=True, errors="replace", timeout=120
        )
        return resultado.stdout if resultado.returncode == 0 else None


# ============================================================================
# TEST MANUAL
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python extractor_texto.py descargas/PNCP-... [--forzar]")
        sys.exit(1)

    extractor = ExtractorTexto()
    texto = extractor.extraer_carpeta(Path(sys.argv[1]), forzar="--forzar" in sys.argv)
    print(f"OCR lang: {extractor._ocr_lang} | caracteres extraídos: {len(texto)}")
    print(texto[:2000])

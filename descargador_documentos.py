"""
DESCARGADOR DE DOCUMENTOS DEL PNCP
===================================
Descarga los archivos de la pestaña "Arquivos" de cada licitación usando
la API pública del PNCP (sin scraping) y descomprime recursivamente los
.zip/.rar (incluso anidados) en una carpeta de salida para su análisis.

API de listado:
    GET https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos
API de descarga (nombre real en el header content-disposition):
    GET .../arquivos/{sequencialDocumento}

Estructura de salida:
    descargas/
        PNCP-{cnpj}-{ano}-{seq}/
            edital.pdf
            anexos.zip
            anexos/          <- contenido extraído del zip
                planilha.rar
                planilha/    <- contenido extraído del rar anidado
"""

import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import List, Optional

import requests


# ============================================================================
# INTEGRIDAD DE LOS FICHEROS DESCARGADOS
# ============================================================================

def validar_arquivo_baixado(
    ruta: Path,
    tamano_esperado: Optional[int] = None,
) -> Optional[str]:
    """
    Comprueba que un fichero descargado está COMPLETO, no solo que existe.

    Por qué existe esta función: el bot daba por buena cualquier descarga con
    `size > 0` y nunca reintentaba un fichero ya presente. Una conexión cortada
    a medias dejaba un PDF truncado en disco que parecía perfectamente válido,
    y el "⏭️ Ya existe" de las siguientes ejecuciones lo volvía PERMANENTE.

    Coste real: São Caetano do Sul (PNCP-67175539000152-2026-9). El Termo de
    Referência se cortó a 2.621.440 bytes clavados —2,5 MiB exactos, la marca
    de una conexión rota, no de un PDF real—, sin `%%EOF` y con pdfinfo
    fallando por "Couldn't find trailer dictionary". El extractor de texto sacó
    lo poco que había, el análisis se hizo sobre media licitación y nadie se
    enteró, porque el fichero estaba ahí y pesaba 2,5 MB. Es el mismo patrón
    que ya nos costó Poá/SP: confiar en la PRESENCIA del dato en vez de en su
    INTEGRIDAD.

    Args:
        ruta: fichero a validar.
        tamano_esperado: Content-Length del servidor, si lo envió.

    Returns:
        None si el fichero está sano, o un texto explicando el problema.
    """
    if not ruta.is_file():
        return "no existe en disco"

    tamano = ruta.stat().st_size
    if tamano == 0:
        return "fichero vacío (0 bytes)"

    # El servidor nos dijo cuánto pesaba: si no cuadra, la descarga se cortó.
    if tamano_esperado is not None and tamano != tamano_esperado:
        return f"tamaño incompleto: {tamano} bytes de {tamano_esperado} esperados"

    # Los PDF terminan siempre en %%EOF. Si falta, está truncado, por mucho
    # que pese megas y que algunos visores lo abran "casi bien".
    if ruta.suffix.lower() == ".pdf" or _empieza_por(ruta, b"%PDF"):
        if not _tiene_marcador_final_pdf(ruta):
            return f"PDF truncado: sin %%EOF al final ({tamano} bytes)"

    return None


def _empieza_por(ruta: Path, firma: bytes) -> bool:
    """True si el fichero empieza por esa firma binaria (PDF sin extensión)."""
    try:
        with open(ruta, "rb") as f:
            return f.read(len(firma)) == firma
    except OSError:
        return False


def _tiene_marcador_final_pdf(ruta: Path, cola: int = 2048) -> bool:
    """Busca %%EOF en los últimos ~2 KB, que es donde el estándar lo pone."""
    try:
        with open(ruta, "rb") as f:
            f.seek(max(0, ruta.stat().st_size - cola))
            return b"%%EOF" in f.read()
    except OSError:
        return False


class DescargadorDocumentos:
    """Descarga y descomprime los documentos de licitaciones del PNCP"""

    API_ARQUIVOS = "https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos"
    # Metadatos de la contratación. Trae los dos enlaces de respaldo.
    API_COMPRA = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
    MAX_NIVEL_EXTRACCION = 4  # comprimidos dentro de comprimidos, tope de anidamiento
    MAX_INTENTOS_DESCARGA = 2  # un fichero que llega truncado merece otra oportunidad

    def __init__(self, pasta_salida: str = "descargas"):
        self.pasta_salida = Path(pasta_salida)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (compatible; MonitorLicitacoes/2.0)"
        })

    # =========================================================================
    # DESCARGA
    # =========================================================================

    def baixar_licitacao(self, licitacao_id: str) -> Optional[Path]:
        """
        Descarga TODOS los documentos de una licitación y los descomprime.

        Args:
            licitacao_id: id en formato PNCP-{cnpj}-{ano}-{seq}

        Returns:
            Carpeta de salida si todo fue bien, None si hubo algún error
            (para poder reintentar en la próxima ejecución).
        """
        partes = licitacao_id.split("-")
        if len(partes) != 4 or partes[0] != "PNCP":
            print(f"   ⚠️ Id no reconocido: {licitacao_id}")
            return None
        _, cnpj, ano, seq = partes

        arquivos = self._listar_arquivos(cnpj, ano, seq)
        if arquivos is None:
            return None  # error de API, reintentar otro día

        pasta = self.pasta_salida / licitacao_id
        pasta.mkdir(parents=True, exist_ok=True)

        if not arquivos:
            print(f"   📭 Sin archivos publicados")
            return pasta

        ok = True
        fallaron = []
        for arq in arquivos:
            if arq.get("statusAtivo") is False:
                continue
            destino = self._baixar_arquivo(arq, pasta)
            if destino is None:
                ok = False
                fallaron.append(arq.get("titulo") or "?")
                continue
            if self._es_comprimido(destino):
                self._extraer_recursivo(destino)

        # ── Cadena de respaldo ────────────────────────────────────────────
        # Añadida el 19/09/2026. Jose, con toda la razón: "lo primero es VER
        # las licitaciones". Ese día el endpoint de ficheros del PNCP devolvía
        # 503 mientras su API de metadatos respondía perfectamente, y el
        # descargador se rendía — dejando 8 licitaciones que cerraban en menos
        # de una semana sin texto y, por tanto, imposibles de juzgar.
        #
        # El PNCP publica para cada contratación DOS enlaces alternativos donde
        # el mismo edital está disponible:
        #   · linkSistemaOrigem      → compras.gov.br / plataforma de origen
        #   · linkProcessoEletronico → portal de transparencia del órgano
        #
        # Rendirse con el primer camino cuando hay otros dos es exactamente
        # perder licitaciones por una caída de una hora.
        if not ok:
            print(f"   ↩️  PNCP falló en {len(fallaron)} archivo(s). "
                  f"Probando enlaces alternativos…")
            if self._registrar_alternativas(cnpj, ano, seq, pasta, fallaron):
                # No marcamos ok=True: la descarga automática no se completó y
                # queremos que siga apareciendo como pendiente. Pero ahora hay
                # en la carpeta un fichero con las URL para abrirlas a mano.
                print(f"   📌 Enlaces alternativos guardados en "
                      f"{pasta.name}/_FUENTES_ALTERNATIVAS.txt")

        return pasta if ok else None

    def _registrar_alternativas(self, cnpj: str, ano: str, seq: str,
                                pasta: Path, fallaron: list) -> bool:
        """Consulta los enlaces de respaldo y los deja escritos en la carpeta.

        No intenta descargar de ellos automáticamente: son portales distintos,
        cada uno con su estructura, y un scraper genérico daría falsos
        positivos. Lo que sí hace es dejar la pista escrita para que el
        expediente se pueda recuperar a mano en treinta segundos en vez de
        perderse en silencio.
        """
        url = self.API_COMPRA.format(cnpj=cnpj, ano=ano, seq=seq)
        resp = self._get_con_retries(url)
        if resp is None or resp.status_code != 200:
            return False
        try:
            d = resp.json()
        except ValueError:
            return False

        origen = d.get("linkSistemaOrigem")
        processo = d.get("linkProcessoEletronico")
        if not origen and not processo:
            return False

        lineas = [
            "FUENTES ALTERNATIVAS",
            "",
            "El endpoint de ficheros del PNCP falló al descargar este",
            "expediente. El edital se puede recuperar aquí:",
            "",
        ]
        # Orden deliberado, comprobado el 19/09/2026 con el navegador:
        #
        #   · linkProcessoEletronico → portal de transparencia del órgano.
        #     AQUÍ SÍ están los PDF del edital. Es el enlace bueno.
        #   · linkSistemaOrigem → página "acompanhamento-compra" de
        #     compras.gov.br. Muestra ítems y estado, pero NO aloja los
        #     documentos. Sirve para confirmar fechas y situación, no para
        #     recuperar el edital.
        #
        # Se listan los dos, pero el primero es el que hay que abrir.
        if processo:
            lineas.append(f"  1) Portal del órgano (AQUÍ ESTÁN LOS PDF): {processo}")
        if origen:
            lineas.append(f"  2) Seguimiento en compras.gov.br (sin documentos, "
                          f"solo estado): {origen}")
        lineas += [
            "",
            f"  Órgano  : {(d.get('orgaoEntidade') or {}).get('razaoSocial', '?')}",
            f"  Objeto  : {(d.get('objetoCompra') or '')[:300]}",
            f"  Cierre  : {d.get('dataEncerramentoProposta') or '?'}",
            f"  Valor   : {d.get('valorTotalEstimado')}",
            "",
            f"  Archivos que no se pudieron bajar: {', '.join(fallaron[:10])}",
        ]
        (pasta / "_FUENTES_ALTERNATIVAS.txt").write_text(
            "\n".join(lineas), encoding="utf-8")
        return True

    def _listar_arquivos(self, cnpj: str, ano: str, seq: str) -> Optional[List[dict]]:
        """Lista los archivos de la pestaña 'Arquivos'. None si falla la API."""
        url = self.API_ARQUIVOS.format(cnpj=cnpj, ano=ano, seq=seq)
        todos = []
        pagina = 1
        while True:
            resp = self._get_con_retries(f"{url}?pagina={pagina}&tamanhoPagina=50")
            if resp is None:
                return None
            if resp.status_code in (204, 404):
                return []  # 204 = licitación sin archivos publicados
            try:
                dados = resp.json()
            except ValueError:
                # A veces la API devuelve una página de error no-JSON
                print(f"   ⚠️ Respuesta no válida de la API (status {resp.status_code})")
                return None
            if not isinstance(dados, list):
                dados = dados.get("data", [])
            todos.extend(dados)
            if len(dados) < 50:
                return todos
            pagina += 1

    def _baixar_arquivo(self, arq: dict, pasta: Path) -> Optional[Path]:
        """
        Descarga un archivo individual. Devuelve la ruta local o None.

        Escribe siempre a un `.parcial` y solo renombra al nombre definitivo
        cuando la descarga pasa `validar_arquivo_baixado`. Así una conexión
        cortada nunca deja en disco algo con pinta de fichero bueno.
        """
        url = arq.get("url") or arq.get("uri")
        # La API devuelve un puerto interno variable (p.ej. :21521) no accesible
        # desde fuera: quitarlo y usar el puerto estándar
        url = re.sub(r"^(https?://pncp\.gov\.br):\d+/", r"\1/", url)

        ultimo_motivo = None
        for tentativa in range(1, self.MAX_INTENTOS_DESCARGA + 1):
            resp = self._get_con_retries(url, stream=True)
            if resp is None or resp.status_code != 200:
                print(f"   ⚠️ No se pudo bajar: {arq.get('titulo')}")
                return None

            nome = self._nome_do_arquivo(resp, arq)
            destino = pasta / nome

            # Un fichero ya presente solo vale si además está ÍNTEGRO
            if destino.exists():
                motivo = validar_arquivo_baixado(destino)
                if motivo is None:
                    print(f"   ⏭️  Ya existe: {nome}")
                    resp.close()
                    return destino
                print(f"   🔁 Redescargando {nome}: {motivo}")

            motivo = self._escribir_y_validar(resp, destino)
            if motivo is None:
                print(f"   ⬇️  {nome} ({destino.stat().st_size // 1024} KB)")
                time.sleep(0.5)  # pausa cortés entre descargas
                return destino

            ultimo_motivo = motivo
            print(f"   ⚠️ Descarga inválida de {nome}: {motivo}"
                  f" (intento {tentativa}/{self.MAX_INTENTOS_DESCARGA})")
            time.sleep(3 * tentativa)

        print(f"   ❌ {arq.get('titulo')}: descarga corrupta tras "
              f"{self.MAX_INTENTOS_DESCARGA} intentos ({ultimo_motivo}). "
              f"No se deja nada en disco para no dar por bueno un fichero a medias.")
        return None

    def _escribir_y_validar(self, resp: requests.Response, destino: Path) -> Optional[str]:
        """
        Vuelca la respuesta a `destino.parcial`, la valida y solo entonces la
        renombra a `destino`. Devuelve None si fue bien, o el motivo del fallo.
        """
        parcial = destino.with_name(destino.name + ".parcial")

        # Solo podemos comparar con Content-Length si el cuerpo no viene
        # comprimido en tránsito (con gzip los bytes escritos son más).
        esperado = None
        if not resp.headers.get("content-encoding"):
            try:
                esperado = int(resp.headers["content-length"])
            except (KeyError, TypeError, ValueError):
                esperado = None

        try:
            resp.raw.decode_content = True  # por si el servidor aplica gzip
            with open(parcial, "wb") as f:
                shutil.copyfileobj(resp.raw, f)
        except (OSError, requests.exceptions.RequestException) as e:
            parcial.unlink(missing_ok=True)
            return f"{type(e).__name__} durante la escritura"
        finally:
            resp.close()

        motivo = validar_arquivo_baixado(parcial, esperado)
        if motivo is not None:
            parcial.unlink(missing_ok=True)
            return motivo

        parcial.replace(destino)  # atómico: o está entero, o no está
        return None

    def _nome_do_arquivo(self, resp: requests.Response, arq: dict) -> str:
        """Nombre real desde content-disposition, con fallback al título de la API."""
        cd = resp.headers.get("content-disposition", "")
        match = re.search(r'filename="?([^";]+)"?', cd)
        nome = match.group(1) if match else (arq.get("titulo") or f"documento_{arq.get('sequencialDocumento', 1)}")
        # Sanitizar: sin rutas ni caracteres problemáticos
        nome = Path(nome).name.replace("\x00", "")
        return re.sub(r'[<>:"/\\|?*]', "_", nome).strip() or "documento"

    def _get_con_retries(self, url: str, stream: bool = False) -> Optional[requests.Response]:
        """GET con reintentos ante 429/5xx/timeout (mismo patrón que la búsqueda)."""
        for tentativa in range(1, 4):
            try:
                resp = self.session.get(url, timeout=120, stream=stream)
                if resp.status_code == 429 or resp.status_code >= 500:
                    espera = 10 * tentativa
                    print(f"   ⏳ {resp.status_code}, aguardando {espera}s...")
                    time.sleep(espera)
                    continue
                return resp
            except requests.exceptions.RequestException as e:
                print(f"   ⏳ {type(e).__name__}, reintentando...")
                time.sleep(5)
        return None

    # =========================================================================
    # DESCOMPRESIÓN RECURSIVA
    # =========================================================================

    def _es_comprimido(self, arquivo: Path) -> bool:
        """
        Detecta zip/rar por extensión, o por firma binaria si NO tiene
        extensión (el PNCP publica comprimidos sin extensión). Los .docx/.xlsx
        también son zips por dentro, por eso no basta con mirar la firma.
        """
        ext = arquivo.suffix.lower()
        if ext in (".zip", ".rar"):
            return True
        if ext:
            return False  # .pdf, .docx, etc.: no tocar
        try:
            with open(arquivo, "rb") as f:
                firma = f.read(4)
            return firma == b"PK\x03\x04" or firma == b"Rar!"
        except OSError:
            return False

    def _extraer_recursivo(self, arquivo: Path, nivel: int = 0):
        """Extrae un comprimido a una carpeta con su nombre y repite con los anidados."""
        if nivel >= self.MAX_NIVEL_EXTRACCION:
            print(f"   ⚠️ Anidamiento máximo alcanzado en {arquivo.name}")
            return

        destino = arquivo.parent / arquivo.stem
        if destino.exists() and not destino.is_dir():
            destino = arquivo.parent / f"{arquivo.stem}_extraido"
        destino.mkdir(exist_ok=True)

        if not self._extraer(arquivo, destino):
            return
        print(f"   📂 Extraído: {arquivo.name} → {destino.name}/")

        # Buscar comprimidos anidados dentro de lo recién extraído
        for sub in sorted(destino.rglob("*")):
            if sub.is_file() and self._es_comprimido(sub):
                self._extraer_recursivo(sub, nivel + 1)

    def _extraer(self, arquivo: Path, destino: Path) -> bool:
        """Extrae zip con la stdlib y rar (u otros) con bsdtar. True si funcionó."""
        if zipfile.is_zipfile(arquivo):
            try:
                self._extraer_zip(arquivo, destino)
                return True
            except (zipfile.BadZipFile, NotImplementedError, OSError) as e:
                print(f"   ⚠️ zipfile falló con {arquivo.name} ({e}), probando bsdtar...")
        return self._extraer_bsdtar(arquivo, destino)

    def _extraer_zip(self, arquivo: Path, destino: Path):
        """
        Extracción de zip corrigiendo el mojibake típico de zips brasileños
        creados en Windows (nombres cp437 en vez de utf-8).
        """
        with zipfile.ZipFile(arquivo) as zf:
            for info in zf.infolist():
                nome = info.filename
                if not info.flag_bits & 0x800:  # sin flag utf-8: vino como cp437
                    try:
                        nome = nome.encode("cp437").decode("utf-8")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                # Protección contra path traversal (../, rutas absolutas)
                alvo = (destino / nome.replace("\\", "/").lstrip("/")).resolve()
                if not str(alvo).startswith(str(destino.resolve())):
                    print(f"   ⚠️ Ruta sospechosa ignorada: {nome}")
                    continue
                if info.is_dir():
                    alvo.mkdir(parents=True, exist_ok=True)
                    continue
                alvo.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as origem, open(alvo, "wb") as f:
                    shutil.copyfileobj(origem, f)

    def _extraer_bsdtar(self, arquivo: Path, destino: Path) -> bool:
        """Extrae rar (y otros formatos) con bsdtar, incluido en macOS."""
        resultado = subprocess.run(
            ["bsdtar", "-xf", str(arquivo), "-C", str(destino)],
            capture_output=True, text=True
        )
        if resultado.returncode != 0:
            erro = resultado.stderr.strip().splitlines()[-1] if resultado.stderr else "?"
            print(f"   ⚠️ No se pudo extraer {arquivo.name}: {erro}")
            print(f"      (si es un RAR nuevo, prueba: brew install unar)")
            return self._extraer_unar(arquivo, destino)
        return True

    def _extraer_unar(self, arquivo: Path, destino: Path) -> bool:
        """Último recurso: unar (si está instalado) maneja todos los RAR."""
        if not shutil.which("unar"):
            return False
        resultado = subprocess.run(
            ["unar", "-force-overwrite", "-o", str(destino), str(arquivo)],
            capture_output=True, text=True
        )
        return resultado.returncode == 0


# ============================================================================
# TEST
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python descargador_documentos.py PNCP-{cnpj}-{ano}-{seq}")
        sys.exit(1)

    descargador = DescargadorDocumentos("descargas")
    pasta = descargador.baixar_licitacao(sys.argv[1])
    print(f"\n{'✅ ' + str(pasta) if pasta else '❌ Falló la descarga'}")

"""
Tests del UPSERT de licitaciones, su histórico y la migración v1.

Uso:
    licitaciones_env/bin/python -m unittest discover -s tests -v
"""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apis_licitacoes import Licitacao, PNCP_API  # noqa: E402
from database import (DatabaseLicitacoes, NUEVA, ACTUALIZADA,  # noqa: E402
                      IGUAL, ESQUEMA_VERSION)
from email_notificador import gerar_html_cambios  # noqa: E402

ID = "PNCP-18307512000160-2026-76"   # Virginópolis/MG, sesión aplazada 22/09 → 02/10


def _futuro(dias: int, hora: str = "08:30:00") -> str:
    return (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d") + "T" + hora


def _lic(enc_ts=None, situacao="Divulgada no PNCP", valor=38513.64,
         fuente="consulta", **extra) -> Licitacao:
    datos = dict(
        id=ID, titulo="Cessão de uso de software de cestas de preços",
        objeto="Cessão de uso de software de cestas de preços",
        orgao="MUNICIPIO DE VIRGINOPOLIS", valor_estimado=valor,
        modalidade="Pregão - Eletrônico", uf="MG", municipio="Virginópolis",
        data_publicacao="2026-09-08",
        data_abertura="2026-09-08", data_abertura_ts="2026-09-08T08:00:00",
        data_encerramento=enc_ts[:10] if enc_ts else None,
        data_encerramento_ts=enc_ts,
        url="https://pncp.gov.br/app/editais/18307512000160/2026/76",
        fonte="PNCP", cnpj_orgao="18307512000160", situacao=situacao,
        fuente_fechas=fuente,
    )
    datos.update(extra)
    return Licitacao(**datos)


class TestUpsert(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseLicitacoes(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_nueva_e_igual(self):
        self.assertEqual(self.db.salvar_licitacao(_lic(_futuro(2))), NUEVA)
        self.assertEqual(self.db.salvar_licitacao(_lic(_futuro(2))), IGUAL)
        self.assertEqual(self.db.historico(ID), [])

    def test_cambio_de_fecha_actualiza_y_registra(self):
        viejo, nuevo = _futuro(2), _futuro(12)
        self.db.salvar_licitacao(_lic(viejo))

        self.assertEqual(self.db.salvar_licitacao(_lic(nuevo)), ACTUALIZADA)

        fila = self.db.obtener_por_id(ID)
        self.assertEqual(fila["data_encerramento"], nuevo[:10])
        self.assertEqual(fila["data_encerramento_ts"], nuevo)
        self.assertEqual(fila["fuente_encerramento"], "consulta")
        self.assertIsNotNone(fila["actualizada_em"])

        hist = self.db.historico(ID)
        self.assertEqual(len(hist), 1)
        h = hist[0]
        self.assertEqual(h["campo"], "data_encerramento")
        self.assertEqual(h["valor_anterior"], viejo)
        self.assertEqual(h["valor_nuevo"], nuevo)
        self.assertEqual(h["tipo"], "cambio")
        self.assertEqual(h["alertado"], 0)
        self.assertTrue(h["detectado_em"])

    def test_no_toca_notas_favorito_ni_notificado(self):
        self.db.salvar_licitacao(_lic(_futuro(2)))
        self.db.agregar_nota(ID, "solo ítem 1")
        self.db.marcar_favorita(ID)
        self.db.marcar_notificada(ID, "email", True)
        self.db.marcar_docs_baixados(ID)

        self.db.salvar_licitacao(_lic(_futuro(9), situacao="Suspensa", valor=40000.0))

        fila = self.db.obtener_por_id(ID)
        self.assertEqual(fila["notas"], "solo ítem 1")
        self.assertEqual(fila["favorito"], 1)
        self.assertEqual(fila["notificado"], 1)
        self.assertEqual(fila["docs_baixados"], 1)

    def test_nulos_no_sobrescriben(self):
        self.db.salvar_licitacao(_lic(_futuro(2)))
        r = self.db.salvar_licitacao(_lic(None, situacao=None, valor=None))
        self.assertEqual(r, IGUAL)
        fila = self.db.obtener_por_id(ID)
        self.assertEqual(fila["data_encerramento_ts"], _futuro(2))
        self.assertEqual(fila["valor_estimado"], 38513.64)
        self.assertEqual(fila["situacao"], "Divulgada no PNCP")

    def test_valor_cero_no_pisa(self):
        # El PNCP publica 0 en los orçamentos sigilosos
        self.db.salvar_licitacao(_lic(_futuro(2)))
        self.assertEqual(self.db.salvar_licitacao(_lic(_futuro(2), valor=0)), IGUAL)

    def test_suspension_es_cambio_avisable(self):
        self.db.salvar_licitacao(_lic(_futuro(2)))
        self.db.salvar_licitacao(_lic(_futuro(2), situacao="Suspensa"))
        h = self.db.historico(ID)[-1]
        self.assertEqual((h["campo"], h["valor_anterior"], h["valor_nuevo"], h["tipo"]),
                         ("situacao", "Divulgada no PNCP", "Suspensa", "cambio"))

    def test_hora_nueva_en_misma_fecha_es_relleno(self):
        # Filas antiguas: solo tenían la fecha. Llegar la hora no es un aplazamiento.
        ts = _futuro(3)
        self.db.salvar_licitacao(_lic(None))
        with sqlite3.connect(self.db.db_path) as c:
            c.execute("UPDATE licitacoes SET data_encerramento = ? WHERE id = ?",
                      (ts[:10], ID))
        self.assertEqual(self.db.salvar_licitacao(_lic(ts)), ACTUALIZADA)
        h = self.db.historico(ID)[-1]
        self.assertEqual(h["tipo"], "relleno")
        self.assertEqual(h["alertado"], 1)
        self.assertEqual(self.db.cambios_pendientes_aviso(), [])

    def test_misma_fecha_otra_hora_es_cambio(self):
        self.db.salvar_licitacao(_lic(_futuro(3, "09:50:00")))
        self.db.salvar_licitacao(_lic(_futuro(3, "10:00:00")))
        self.assertEqual(self.db.historico(ID)[-1]["tipo"], "cambio")

    def test_search_no_escribe_fechas_de_propuestas(self):
        lic = _lic(None, fuente="search", fonte="PNCP-search",
                   data_abertura=None, data_abertura_ts=None,
                   vigencia_inicio="2026-09-08", vigencia_fim="2027-09-08")
        self.assertEqual(self.db.salvar_licitacao(lic), NUEVA)
        fila = self.db.obtener_por_id(ID)
        self.assertIsNone(fila["data_encerramento"])
        self.assertIsNone(fila["data_abertura"])
        self.assertEqual(fila["fuente_encerramento"], "search")
        self.assertEqual(fila["vigencia_fim"], "2027-09-08")

        # Si luego llega /consulta, rellena (sin aviso: no había fecha)
        self.db.salvar_licitacao(_lic(_futuro(5)))
        fila = self.db.obtener_por_id(ID)
        self.assertEqual(fila["data_encerramento_ts"], _futuro(5))
        self.assertEqual(fila["fuente_encerramento"], "consulta")
        self.assertEqual(self.db.cambios_pendientes_aviso(), [])

    def test_search_no_pisa_fecha_de_consulta(self):
        self.db.salvar_licitacao(_lic(_futuro(5)))
        lic = _lic(None, fuente="search", data_abertura=None, data_abertura_ts=None)
        lic.data_encerramento = "2027-01-01"   # vigencia mal asignada, por si acaso
        self.db.salvar_licitacao(lic)
        self.assertEqual(self.db.obtener_por_id(ID)["data_encerramento"], _futuro(5)[:10])

    def test_fecha_manual_protegida(self):
        self.db.salvar_licitacao(_lic(_futuro(5)))
        with sqlite3.connect(self.db.db_path) as c:
            c.execute("UPDATE licitacoes SET fuente_encerramento = 'manual' WHERE id = ?", (ID,))
        self.db.salvar_licitacao(_lic(_futuro(8)))
        self.assertEqual(self.db.obtener_por_id(ID)["data_encerramento"], _futuro(5)[:10])

    def test_salvar_varias_cuenta(self):
        self.db.salvar_licitacao(_lic(_futuro(2)))
        otra = _lic(_futuro(4), id="PNCP-00000000000000-2026-1")
        r = self.db.salvar_varias([_lic(_futuro(9)), otra, otra])
        self.assertEqual((r["nuevas"], r["actualizadas"], r["iguales"], r["existentes"]),
                         (1, 1, 1, 2))


class TestAvisoCambios(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseLicitacoes(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pendientes_y_marcado(self):
        self.db.salvar_licitacao(_lic(_futuro(2)))
        self.db.salvar_licitacao(_lic(_futuro(12)))
        pend = self.db.cambios_pendientes_aviso()
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["licitacao_id"], ID)
        self.db.marcar_cambios_avisados([p["cambio_id"] for p in pend])
        self.assertEqual(self.db.cambios_pendientes_aviso(), [])

    def test_excluye_no_software(self):
        self.db.salvar_licitacao(_lic(_futuro(2)))
        self.db.guardar_triaje(ID, {"bucket": "NO_SOFTWARE", "texto_ok": False})
        self.db.salvar_licitacao(_lic(_futuro(12)))
        self.assertEqual(self.db.cambios_pendientes_aviso(), [])

    def test_excluye_cerradas_hace_dias(self):
        pasado = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT08:30:00")
        mas_pasado = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%dT08:30:00")
        self.db.salvar_licitacao(_lic(mas_pasado))
        self.db.salvar_licitacao(_lic(pasado))
        self.assertEqual(self.db.cambios_pendientes_aviso(), [])

    def test_html_muestra_antes_y_despues(self):
        # El caso real: Virginópolis 22/09 08:30 → 02/10 08:30
        cambios = [{
            "cambio_id": 1, "licitacao_id": ID, "campo": "data_encerramento",
            "valor_anterior": "2026-09-22T08:30:00",
            "valor_nuevo": "2026-10-02T08:30:00",
            "fuente": "consulta", "detectado_em": "2026-09-24 10:00:00",
            "objeto": "Cessão de uso de software", "orgao": "MUNICIPIO DE VIRGINOPOLIS",
            "municipio": "Virginópolis", "uf": "MG", "url": "https://pncp.gov.br",
            "modalidade": "Pregão - Eletrônico", "plataforma": "licitar.digital",
            "muro_economico": "NO",
        }]
        html = gerar_html_cambios(cambios)
        self.assertIn("22/09/2026 08:30", html)
        self.assertIn("02/10/2026 08:30", html)
        self.assertIn(ID, html)


class TestParse(unittest.TestCase):

    def test_parse_item_guarda_hora(self):
        item = {
            "orgaoEntidade": {"cnpj": "13016717000173", "razaoSocial": "FSPSCE"},
            "unidadeOrgao": {"ufSigla": "RS", "municipioNome": "Esteio"},
            "anoCompra": 2026, "sequencialCompra": 225,
            "objetoCompra": "SaaS chatbot",
            "dataAberturaProposta": "2026-09-21T08:00:00",
            "dataEncerramentoProposta": "2026-10-05T09:50:00",
            "situacaoCompraNome": "Divulgada no PNCP",
        }
        lic = PNCP_API().parse_item(item)
        self.assertEqual(lic.data_encerramento, "2026-10-05")
        self.assertEqual(lic.data_encerramento_ts, "2026-10-05T09:50:00")
        self.assertEqual(lic.fuente_fechas, "consulta")

    def test_parse_search_no_da_fechas_de_propuestas(self):
        item = {"orgao_cnpj": "07226794000155", "ano": "2026",
                "numero_sequencial": "140", "description": "Canal de denúncias",
                "data_inicio_vigencia": "2026-09-18T00:00:00",
                "data_fim_vigencia": "2026-10-14T14:00:00"}
        lic = PNCP_API().parse_item_search(item)
        self.assertIsNone(lic.data_encerramento)
        self.assertIsNone(lic.data_abertura)
        self.assertEqual(lic.vigencia_fim, "2026-10-14")
        self.assertEqual(lic.fuente_fechas, "search")


class TestMigracion(unittest.TestCase):

    def test_migracion_v1_idempotente_con_copia(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "vieja.db"
            # Esquema anterior (user_version 0), con una fila de cada fuente
            with sqlite3.connect(ruta) as c:
                c.execute("""CREATE TABLE licitacoes (id TEXT PRIMARY KEY,
                    titulo TEXT NOT NULL, objeto TEXT, orgao TEXT,
                    valor_estimado REAL, modalidade TEXT, uf TEXT, municipio TEXT,
                    data_publicacao TEXT, data_abertura TEXT, url TEXT, fonte TEXT,
                    cnpj_orgao TEXT, situacao TEXT,
                    data_encontrada TEXT DEFAULT CURRENT_TIMESTAMP,
                    notificado INTEGER DEFAULT 0, favorito INTEGER DEFAULT 0,
                    notas TEXT, docs_baixados INTEGER DEFAULT 0,
                    data_encerramento TEXT)""")
                c.execute("INSERT INTO licitacoes (id, titulo, fonte, data_abertura, "
                          "data_encerramento, notas) VALUES "
                          "('S', 't', 'PNCP-search', '2026-09-18', '2027-09-18', 'ojo')")
                c.execute("INSERT INTO licitacoes (id, titulo, fonte, data_abertura, "
                          "data_encerramento) VALUES "
                          "('C', 't', 'PNCP', '2026-09-08', '2026-09-22')")

            db = DatabaseLicitacoes(str(ruta))
            copias = list(Path(tmp).glob("vieja.db.bak_*_antes_migracion_v1"))
            self.assertEqual(len(copias), 1)

            s = db.obtener_por_id("S")
            self.assertIsNone(s["data_encerramento"])
            self.assertIsNone(s["data_abertura"])
            self.assertEqual(s["vigencia_fim"], "2027-09-18")
            self.assertEqual(s["fuente_encerramento"], "search")
            self.assertEqual(s["notas"], "ojo")
            self.assertEqual({h["tipo"] for h in db.historico("S")}, {"correccion"})
            self.assertEqual(db.cambios_pendientes_aviso(), [])

            cfila = db.obtener_por_id("C")
            self.assertEqual(cfila["data_encerramento"], "2026-09-22")
            self.assertEqual(cfila["fuente_encerramento"], "consulta")

            # Segunda apertura: nada que migrar, ni copia nueva
            DatabaseLicitacoes(str(ruta))
            self.assertEqual(len(list(Path(tmp).glob("vieja.db.bak_*"))), 1)
            with sqlite3.connect(ruta) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],
                                 ESQUEMA_VERSION)

    def test_base_nueva_sin_copia(self):
        with tempfile.TemporaryDirectory() as tmp:
            DatabaseLicitacoes(str(Path(tmp) / "nueva.db"))
            self.assertEqual(list(Path(tmp).glob("*.bak_*")), [])


if __name__ == "__main__":
    unittest.main()

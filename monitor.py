"""
MONITOR DE LICITAÇÕES BRASIL - VERSIÓN SIMPLIFICADA
====================================================
Ejecutar manualmente para buscar licitaciones y recibir por email.

Uso:
    python monitor.py                    # Buscar con config.json
    python monitor.py --termos software  # Buscar término específico
    python monitor.py --uf SP RJ         # Buscar en estados específicos
    python monitor.py --test-email       # Probar envío de email
"""

import argparse
import json
import os
from datetime import datetime
from typing import List, Optional

from apis_licitacoes import BuscadorLicitacoes, Licitacao
from database import DatabaseLicitacoes, NUEVA, ACTUALIZADA
from descargador_documentos import DescargadorDocumentos
from email_notificador import (criar_notificador, gerar_html_shortlist,
                               gerar_html_cambios, formatar_valor_cambio)
from triaje import TriadorLicitacoes


class MonitorLicitacoes:
    """Monitor simplificado de licitaciones"""
    
    def __init__(self, config_path: str = "config.json"):
        self.config = self._carregar_config(config_path)
        self.buscador = BuscadorLicitacoes()
        self.db = DatabaseLicitacoes(self.config.get("database", "licitacoes.db"))
        self.email = self._configurar_email()
        self.triador = TriadorLicitacoes(self.config, self.db)
    
    def _carregar_config(self, path: str) -> dict:
        """Carga o crea configuración"""
        
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # Config default
        config = {
            "database": "licitacoes.db",
            "busca": {
                "termos": ["software", "tecnologia", "informática"],
                "ufs": ["SP"],
                "dias_adelante": 7
            },
            "email": {
                "provedor": "resend",
                "api_key": "TU_API_KEY_AQUI",
                "email_from": "onboarding@resend.dev",
                "email_to": "tu.email@gmail.com"
            },
            "descargas": {
                "activado": True,
                "pasta_salida": "descargas"
            }
        }
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        print(f"📝 Archivo config.json creado. Edita las configuraciones.")
        return config
    
    def _configurar_email(self):
        """Configura el notificador de email"""
        
        email_config = self.config.get("email", {})
        
        if email_config.get("api_key") == "TU_API_KEY_AQUI":
            print("⚠️  Email no configurado. Edita config.json con tu API key.")
            return None
        
        try:
            return criar_notificador(email_config)
        except Exception as e:
            print(f"⚠️  Error configurando email: {e}")
            return None
    
    def buscar(
        self,
        termos: List[str] = None,
        ufs: List[str] = None,
        dias_adelante: int = None,
        enviar_email: bool = True
    ) -> List[Licitacao]:
        """
        Busca licitaciones y opcionalmente envía por email
        
        NOTA: La API del PNCP no soporta búsqueda por texto.
        Primero se descargan TODAS las licitaciones de los estados/fechas,
        y luego se filtran localmente por los términos.
        
        Args:
            termos: Palabras clave para filtrar (filtro LOCAL)
            ufs: Estados (usa config si None)
            dias_adelante: Días para buscar (usa config si None)
            enviar_email: Si enviar email con resultados
        
        Returns:
            Lista de licitaciones nuevas encontradas
        """
        
        # Usar config si no se especifican parámetros
        busca_config = self.config.get("busca", {})
        termos = termos or busca_config.get("termos")
        exclusoes = busca_config.get("exclusoes")
        ufs = ufs or busca_config.get("ufs")
        dias_adelante = dias_adelante or busca_config.get("dias_adelante", 7)
        
        print("\n" + "="*60)
        print(f"🔍 BUSCANDO LICITAÇÕES - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("="*60)
        print(f"📍 Estados: {ufs or 'Todos'}")
        print(f"📅 Propostas abertas (cierre en los próximos {dias_adelante} dias)")
        print(f"📝 Filtro de termos (local): {termos or 'Ninguno'}")
        print("="*60)
        print("\n⏳ Esto puede tardar unos minutos si hay muchos estados...")
        
        # Guardado incremental: cada página que a API devolve é gravada na
        # hora. Se a busca falhar a meio (API instável, 500/503), NÃO se perde
        # o que já foi descarregado.
        novas_incremental = []
        actualizadas = []

        def _guardar_pagina(lote):
            gravadas = cambiadas = 0
            for lic in lote:
                try:
                    r = self.db.salvar_licitacao(lic)
                    if r == NUEVA:
                        novas_incremental.append(lic)
                        gravadas += 1
                    elif r == ACTUALIZADA:
                        actualizadas.append(lic.id)
                        cambiadas += 1
                except Exception as e:
                    print(f"   ⚠️ erro ao gravar {lic.id}: {e}", end=" ")
            if gravadas:
                print(f"[+{gravadas} gravadas]", end=" ", flush=True)
            if cambiadas:
                print(f"[~{cambiadas} actualizadas]", end=" ", flush=True)

        # Buscar
        try:
            licitacoes = self.buscador.buscar(
                termos=termos,
                exclusoes=exclusoes,
                ufs=ufs,
                dias_adelante=dias_adelante,
                on_pagina=_guardar_pagina
            )
        except KeyboardInterrupt:
            print("\n\n⚠️  Busca interrompida pelo utilizador.")
            print(f"   ✅ {len(novas_incremental)} licitações novas já foram gravadas.")
            licitacoes = []
        except Exception as e:
            print(f"\n\n⚠️  Busca interrompida por erro: {e}")
            print(f"   ✅ {len(novas_incremental)} licitações novas já foram gravadas.")
            licitacoes = []
        
        print(f"\n📊 Total encontradas: {len(licitacoes)}")

        # ── ALERTA: API do PNCP sem retorno ──────────────────────────────
        # Se a busca devolve ZERO resultados, quase certamente a API de
        # consulta do PNCP está fora do ar (não é normal um dia útil sem
        # nenhuma licitação aberta em todo o Brasil).
        if len(licitacoes) == 0:
            aviso = (
                "A busca no PNCP retornou 0 (zero) resultados.\n\n"
                "Isso normalmente significa que a API de consulta do PNCP "
                "(https://pncp.gov.br/api/consulta/v1/contratacoes/proposta) "
                "está indisponível ou degradada — e NÃO que não existam licitações.\n\n"
                "O robô continuará rodando normalmente. Quando a API voltar, "
                "as licitações com propostas ainda abertas serão recuperadas "
                "automaticamente na próxima execução.\n\n"
                "Verificar manualmente em: https://pncp.gov.br"
            )
            print("\n" + "!"*60)
            print("🚨 ALERTA: A API DO PNCP RETORNOU ZERO RESULTADOS")
            print(aviso)
            print("!"*60)
            if enviar_email and self.email:
                try:
                    self.email.enviar_html(
                        assunto="🚨 ALERTA: API do PNCP sem retorno — robô sem licitações novas",
                        html=(
                            "<h2 style='color:#c00'>🚨 API do PNCP sem retorno</h2>"
                            "<p>A busca retornou <b>0 resultados</b>.</p>"
                            "<p>Isso indica que a <b>API de consulta do PNCP está "
                            "indisponível ou degradada</b> — não que não existam licitações.</p>"
                            "<p>O robô segue rodando. Quando a API voltar, as licitações "
                            "com propostas ainda abertas serão recuperadas automaticamente.</p>"
                            "<p>Verificar em <a href='https://pncp.gov.br'>pncp.gov.br</a></p>"
                        ),
                    )
                    print("📧 Alerta enviado por e-mail.")
                except Exception as e:
                    print(f"⚠️  Não foi possível enviar o alerta por e-mail: {e}")
        # ─────────────────────────────────────────────────────────────────
        
        # As licitações já foram gravadas incrementalmente pelo callback.
        novas = novas_incremental
        
        print(f"🆕 Novas (não vistas antes): {len(novas)}")
        print(f"🔄 Já conhecidas com dados actualizados: {len(actualizadas)}")
        
        # Mostrar resumen
        if novas:
            print("\n📋 NOVAS LICITAÇÕES:")
            print("-"*60)
            for i, lic in enumerate(novas[:10], 1):
                valor = f"R$ {lic.valor_estimado:,.2f}" if lic.valor_estimado else "N/I"
                print(f"\n{i}. {lic.titulo[:70]}...")
                print(f"   🏢 {lic.orgao[:50]}")
                print(f"   📍 {lic.municipio or 'N/A'} - {lic.uf} | 💰 {valor}")
                print(f"   🔗 {lic.url}")
            
            if len(novas) > 10:
                print(f"\n   ... e mais {len(novas) - 10} licitações")

            # Descargar documentos de las nuevas
            descargas_config = self.config.get("descargas", {})
            if descargas_config.get("activado", True):
                # Solo lo vigente, y primero lo que cierra antes. Añadido el
                # 21/09/2026: se descargaba en el orden en que llegaban de la
                # búsqueda, y con el buscador de emergencia eso era relevancia
                # de texto — pliegos de 2023 antes que los que cierran esta
                # semana. Es el mismo orden que obtener_pendientes_descarga().
                hoy = datetime.now().strftime("%Y-%m-%d")
                vigentes = [l for l in novas
                            if l.data_encerramento is None or l.data_encerramento >= hoy]
                vigentes.sort(key=lambda l: (l.data_encerramento is None,
                                             l.data_encerramento or ""))
                if len(vigentes) < len(novas):
                    print(f"\n⏭️  {len(novas) - len(vigentes)} novas ya cerradas: no se descargan")
                self.descargar_documentos([lic.id for lic in vigentes])
        else:
            print("\n✅ Nenhuma licitação nova desde a última busca.")

        # Triaje por reglas de lo descargado pendiente + shortlist
        self.triar(enviar_email=enviar_email)

        # Aplazamientos, suspensiones y cambios de valor detectados
        self.avisar_cambios(enviar_email=enviar_email)

        return novas

    def refrescar(self, ids: List[str], enviar_email: bool = True):
        """
        Relee del PNCP (detalle de la compra) las licitaciones indicadas y
        aplica el UPSERT. Sirve para las que ya no salen en /proposta —
        una sesión aplazada fuera del horizonte, una suspendida— y para
        comprobar a mano una concreta. Termina con el aviso de cambios.
        """
        api = self.buscador.api
        print(f"\n🔄 Refrescando {len(ids)} licitação(ões) desde o PNCP...")
        for lic_id in ids:
            try:
                _, cnpj, ano, seq = lic_id.split("-")
            except ValueError:
                print(f"   ⚠️ {lic_id}: id fuera del formato PNCP-cnpj-ano-seq")
                continue
            dados = api.consultar_compra(cnpj, ano, seq)
            if not dados:
                print(f"   ⚠️ {lic_id}: el PNCP no devolvió el detalle")
                continue
            r = self.db.salvar_licitacao(api.parse_item(dados))
            print(f"   {lic_id}: {r}")
        self.avisar_cambios(enviar_email=enviar_email)

    def avisar_cambios(self, enviar_email: bool = True) -> int:
        """
        Avisa de los cambios (fecha, situación, valor) aún no avisados.
        Solo los marca como avisados si el email salió: sin email, se
        vuelven a mostrar en la próxima pasada.
        """
        cambios = self.db.cambios_pendientes_aviso()
        if not cambios:
            print("📭 Sin cambios de fecha/situación pendientes de aviso.")
            return 0

        n_lic = len({c["licitacao_id"] for c in cambios})
        print(f"\n📅 {n_lic} licitação(ões) com mudanças:")
        for c in cambios:
            print(f"   • {c['licitacao_id']} {c['campo']}: "
                  f"{formatar_valor_cambio(c['campo'], c['valor_anterior'])} → "
                  f"{formatar_valor_cambio(c['campo'], c['valor_nuevo'])}")

        if not (enviar_email and self.email):
            print("   (sin email: quedan pendientes para la próxima pasada)")
            return n_lic

        assunto = f"📅 {n_lic} licitação(ões) mudaram de data ou situação"
        if self.email.enviar_html(assunto, gerar_html_cambios(cambios)):
            self.db.marcar_cambios_avisados([c["cambio_id"] for c in cambios])
        return n_lic

    def triar(self, enviar_email: bool = True):
        """
        Tría lo descargado pendiente, regenera candidatas.csv y avisa por
        email SOLO de las candidatas sin muros activos próximas a vencer.
        """

        print("\n" + "="*60)
        print("🧮 TRIAJE POR REGLAS")
        print("="*60)

        conteo = self.triador.triar_pendientes()
        if conteo:
            print(f"\n📊 Triaje: {conteo}")

        self.triador.generar_csv()

        alertas = self.triador.candidatas_alerta()
        if not alertas:
            print("📭 Sin candidatas nuevas sin muros próximas a vencer.")
            return

        dias = self.triador.dias_alerta
        print(f"\n🎯 {len(alertas)} candidata(s) SIN muros cierran en ≤{dias} días:")
        for c in alertas:
            print(f"   • {c['data_encerramento']} | {(c.get('objeto') or '')[:70]}")

        if enviar_email and self.email:
            print(f"\n📧 Enviando shortlist...")
            assunto = f"🎯 {len(alertas)} candidata(s) sem muros fecham em ≤{dias} dias"
            ok = self.email.enviar_html(assunto, gerar_html_shortlist(alertas, dias))
            if ok:
                for c in alertas:
                    self.db.marcar_notificada(c["id"], "email", True)

    def descargar_documentos(self, ids: List[str] = None, limite: int = None):
        """
        Descarga los documentos (pestaña 'Arquivos' del PNCP) de las
        licitaciones indicadas, o de las pendientes en la base si ids=None.
        Los zip/rar se descomprimen recursivamente en la carpeta de salida.
        """

        descargas_config = self.config.get("descargas", {})
        pasta = descargas_config.get("pasta_salida", "descargas")
        descargador = DescargadorDocumentos(pasta)

        if ids is None:
            pendientes = self.db.obtener_pendientes_descarga(limite)
            ids = [p["id"] for p in pendientes]

        if not ids:
            print("\n✅ No hay documentos pendientes de descarga.")
            return

        print(f"\n📥 DESCARGANDO DOCUMENTOS de {len(ids)} licitações → {pasta}/")
        print("-"*60)

        ok, errores, saltadas = 0, 0, 0
        for i, lic_id in enumerate(ids, 1):
            print(f"\n[{i}/{len(ids)}] {lic_id}")

            # Pre-clasificación por objeto: si ya es NO_SOFTWARE de alta
            # confianza, se registra en triaje y no se descarga nada
            objeto = (self.db.obtener_por_id(lic_id) or {}).get("objeto") or ""
            if objeto and self.triador.clasificar(objeto) == "NO_SOFTWARE":
                self.db.guardar_triaje(lic_id, {"bucket": "NO_SOFTWARE", "texto_ok": False})
                print("   🚫 NO_SOFTWARE por objeto: documentos no descargados")
                saltadas += 1
                continue

            try:
                resultado = descargador.baixar_licitacao(lic_id)
            except Exception as e:
                # Un fallo en una licitación no debe abortar toda la tanda
                print(f"   ⚠️ Error inesperado ({type(e).__name__}): {e}")
                resultado = None
            if resultado:
                self.db.marcar_docs_baixados(lic_id)
                ok += 1
            else:
                errores += 1  # queda pendiente para reintentar en la próxima

        print(f"\n📊 Descargas: {ok} ok, {errores} con error (se reintentarán), "
              f"{saltadas} NO_SOFTWARE sin descargar")
    
    def testar_email(self) -> bool:
        """Prueba el envío de email"""
        
        if not self.email:
            print("❌ Email no configurado. Edita config.json")
            return False
        
        print("🧪 Testando conexão de email...")
        
        if self.email.testar_conexao():
            print("✅ Email funcionando correctamente!")
            return True
        else:
            print("❌ Error en la conexión de email")
            return False
    
    def estatisticas(self):
        """Muestra estadísticas"""
        
        stats = self.db.estatisticas()
        
        print("\n📊 ESTATÍSTICAS DO BANCO")
        print("="*40)
        print(f"Total de licitações: {stats['total']}")
        print(f"Valor total: R$ {stats['valor_total']:,.2f}")
        
        if stats['por_uf']:
            print("\nPor estado:")
            for uf, total in list(stats['por_uf'].items())[:5]:
                print(f"  {uf}: {total}")
    
    def exportar(self, arquivo: str = "licitacoes_export.csv"):
        """Exporta licitaciones a CSV"""
        
        total = self.db.exportar_csv(arquivo)
        print(f"✅ Exportadas {total} licitações para: {arquivo}")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor de Licitações Brasil 🇧🇷",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python monitor.py                        # Buscar con config.json
  python monitor.py --termos software ti   # Buscar términos específicos
  python monitor.py --uf SP RJ MG          # Buscar en estados
  python monitor.py --dias 14              # Últimos 14 días
  python monitor.py --sem-email            # Sin enviar email
  python monitor.py --test-email           # Probar email
  python monitor.py --stats                # Ver estadísticas
  python monitor.py --exportar             # Exportar a CSV
  python monitor.py --descargar            # Descargar docs pendientes
  python monitor.py --descargar --limite 5 # Solo las 5 más recientes
  python monitor.py --triar                # Triar pendientes + regenerar CSV
  python monitor.py --refrescar PNCP-…     # Releer del PNCP y avisar de cambios
  python monitor.py --avisar-cambios       # Enviar solo el aviso de cambios
        """
    )

    parser.add_argument("--termos", "-t", nargs="+", help="Palabras clave para buscar")
    parser.add_argument("--uf", "-u", nargs="+", help="Estados (SP, RJ, MG, etc.)")
    parser.add_argument("--dias", "-d", type=int, help="Días para buscar (default: 7)")
    parser.add_argument("--sem-email", action="store_true", help="No enviar email")
    parser.add_argument("--test-email", action="store_true", help="Probar conexión de email")
    parser.add_argument("--stats", action="store_true", help="Mostrar estadísticas")
    parser.add_argument("--exportar", "-e", action="store_true", help="Exportar a CSV")
    parser.add_argument("--descargar", action="store_true", help="Descargar documentos pendientes")
    parser.add_argument("--triar", action="store_true", help="Triar pendientes y regenerar candidatas.csv")
    parser.add_argument("--limite", type=int, help="Límite de licitaciones para --descargar")
    parser.add_argument("--refrescar", nargs="+", metavar="ID",
                        help="Releer estas licitaciones del PNCP y avisar de cambios")
    parser.add_argument("--avisar-cambios", action="store_true",
                        help="Enviar el aviso de cambios pendientes")
    parser.add_argument("--config", "-c", default="config.json", help="Archivo de config")

    args = parser.parse_args()

    # Crear monitor
    monitor = MonitorLicitacoes(args.config)

    # Ejecutar comando
    if args.test_email:
        monitor.testar_email()

    elif args.stats:
        monitor.estatisticas()

    elif args.exportar:
        monitor.exportar()

    elif args.descargar:
        monitor.descargar_documentos(limite=args.limite)

    elif args.triar:
        monitor.triar(enviar_email=not args.sem_email)

    elif args.refrescar:
        monitor.refrescar(args.refrescar, enviar_email=not args.sem_email)

    elif args.avisar_cambios:
        monitor.avisar_cambios(enviar_email=not args.sem_email)

    else:
        # Buscar licitaciones
        monitor.buscar(
            termos=args.termos,
            ufs=args.uf,
            dias_adelante=args.dias,
            enviar_email=not args.sem_email
        )


if __name__ == "__main__":
    main()
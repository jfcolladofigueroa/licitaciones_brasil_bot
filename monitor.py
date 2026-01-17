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
from database import DatabaseLicitacoes
from email_notificador import criar_notificador, EmailResend


class MonitorLicitacoes:
    """Monitor simplificado de licitaciones"""
    
    def __init__(self, config_path: str = "config.json"):
        self.config = self._carregar_config(config_path)
        self.buscador = BuscadorLicitacoes()
        self.db = DatabaseLicitacoes(self.config.get("database", "licitacoes.db"))
        self.email = self._configurar_email()
    
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
                "dias_atras": 7
            },
            "email": {
                "provedor": "resend",
                "api_key": "TU_API_KEY_AQUI",
                "email_from": "onboarding@resend.dev",
                "email_to": "tu.email@gmail.com"
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
        dias_atras: int = None,
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
            dias_atras: Días para buscar (usa config si None)
            enviar_email: Si enviar email con resultados
        
        Returns:
            Lista de licitaciones nuevas encontradas
        """
        
        # Usar config si no se especifican parámetros
        busca_config = self.config.get("busca", {})
        termos = termos or busca_config.get("termos")
        ufs = ufs or busca_config.get("ufs")
        dias_atras = dias_atras or busca_config.get("dias_atras", 7)
        
        print("\n" + "="*60)
        print(f"🔍 BUSCANDO LICITAÇÕES - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("="*60)
        print(f"📍 Estados: {ufs or 'Todos'}")
        print(f"📅 Últimos {dias_atras} dias")
        print(f"📝 Filtro de termos (local): {termos or 'Ninguno'}")
        print("="*60)
        print("\n⏳ Esto puede tardar unos minutos si hay muchos estados...")
        
        # Buscar
        licitacoes = self.buscador.buscar(
            termos=termos,
            ufs=ufs,
            dias_atras=dias_atras
        )
        
        print(f"\n📊 Total encontradas: {len(licitacoes)}")
        
        # Filtrar nuevas (no están en el banco)
        novas = []
        for lic in licitacoes:
            if self.db.salvar_licitacao(lic):
                novas.append(lic)
        
        print(f"🆕 Novas (não vistas antes): {len(novas)}")
        
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
            
            # Enviar email
            if enviar_email and self.email and novas:
                print(f"\n📧 Enviando email...")
                self.email.enviar(novas)
        else:
            print("\n✅ Nenhuma licitação nova desde a última busca.")
        
        return novas
    
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
        """
    )
    
    parser.add_argument("--termos", "-t", nargs="+", help="Palabras clave para buscar")
    parser.add_argument("--uf", "-u", nargs="+", help="Estados (SP, RJ, MG, etc.)")
    parser.add_argument("--dias", "-d", type=int, help="Días para buscar (default: 7)")
    parser.add_argument("--sem-email", action="store_true", help="No enviar email")
    parser.add_argument("--test-email", action="store_true", help="Probar conexión de email")
    parser.add_argument("--stats", action="store_true", help="Mostrar estadísticas")
    parser.add_argument("--exportar", "-e", action="store_true", help="Exportar a CSV")
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
    
    else:
        # Buscar licitaciones
        monitor.buscar(
            termos=args.termos,
            ufs=args.uf,
            dias_atras=args.dias,
            enviar_email=not args.sem_email
        )


if __name__ == "__main__":
    main()
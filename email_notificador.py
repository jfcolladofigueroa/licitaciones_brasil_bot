"""
NOTIFICACIONES POR EMAIL - SIMPLIFICADO
========================================
Envía alertas de licitaciones por email usando APIs gratuitas.

Opciones soportadas:
- Resend (RECOMENDADO) - 100 emails/día gratis
- SendGrid - 100 emails/día gratis  
- SMTP directo (Gmail, Outlook, etc.)
"""

import requests
from typing import List, Optional
from dataclasses import dataclass

# Importamos Licitacao del módulo de APIs
try:
    from apis_licitacoes import Licitacao
except ImportError:
    # Para testing standalone
    @dataclass
    class Licitacao:
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
        url: Optional[str]
        fonte: str


# ============================================================================
# OPCIÓN 1: RESEND (RECOMENDADO - MÁS FÁCIL)
# ============================================================================

class EmailResend:
    """
    Envía emails usando Resend API
    
    Configuración (5 minutos):
        1. Crear cuenta en https://resend.com
        2. Obtener API Key en Dashboard
        3. Verificar un dominio O usar el email de testing
        
    Tier gratuito: 100 emails/día, 3000/mes
    """
    
    def __init__(self, api_key: str, email_from: str, email_to: str):
        """
        Args:
            api_key: Tu API key de Resend
            email_from: Email remitente (debe ser de dominio verificado o onboarding@resend.dev para testing)
            email_to: Email destinatario
        """
        self.api_key = api_key
        self.email_from = email_from
        self.email_to = email_to
        self.api_url = "https://api.resend.com/emails"
    
    def testar_conexao(self) -> bool:
        """Prueba la conexión enviando un email de test"""
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": self.email_from,
                    "to": [self.email_to],
                    "subject": "🧪 Test - Monitor Licitações",
                    "html": "<p>Conexão funcionando! ✅</p>"
                },
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Error: {e}")
            return False
    
    def enviar(self, licitacoes: List[Licitacao], assunto: str = None) -> bool:
        """Envía email con las licitaciones encontradas"""
        
        if not licitacoes:
            return True
        
        if not assunto:
            assunto = f"🔔 {len(licitacoes)} Nova(s) Licitação(ões) Encontrada(s)"
        
        html = self._gerar_html(licitacoes)
        
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": self.email_from,
                    "to": [self.email_to],
                    "subject": assunto,
                    "html": html
                },
                timeout=30
            )
            
            if response.status_code == 200:
                print(f"✅ Email enviado para {self.email_to}")
                return True
            else:
                print(f"❌ Error {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error al enviar: {e}")
            return False
    
    def _gerar_html(self, licitacoes: List[Licitacao]) -> str:
        """Genera HTML bonito para el email"""
        
        cards = ""
        for lic in licitacoes:
            valor = f"R$ {lic.valor_estimado:,.2f}" if lic.valor_estimado else "Não informado"
            cards += f"""
            <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin: 15px 0; background: #fafafa;">
                <h3 style="color: #1a365d; margin: 0 0 15px 0; font-size: 16px; line-height: 1.4;">{lic.titulo[:150]}</h3>
                <table style="width: 100%; font-size: 14px; color: #444;">
                    <tr><td style="padding: 5px 0; width: 120px;"><strong>🏢 Órgão:</strong></td><td>{lic.orgao}</td></tr>
                    <tr><td style="padding: 5px 0;"><strong>📍 Local:</strong></td><td>{lic.municipio or 'N/A'} - {lic.uf}</td></tr>
                    <tr><td style="padding: 5px 0;"><strong>💰 Valor:</strong></td><td style="color: #2d7a2d; font-weight: bold;">{valor}</td></tr>
                    <tr><td style="padding: 5px 0;"><strong>📑 Modalidade:</strong></td><td>{lic.modalidade}</td></tr>
                    <tr><td style="padding: 5px 0;"><strong>📅 Publicação:</strong></td><td>{lic.data_publicacao}</td></tr>
                    <tr><td style="padding: 5px 0;"><strong>📅 Abertura:</strong></td><td>{lic.data_abertura or 'Não informada'}</td></tr>
                </table>
                <a href="{lic.url or '#'}" style="display: inline-block; margin-top: 15px; padding: 10px 20px; background: #2563eb; color: white; text-decoration: none; border-radius: 5px; font-size: 14px;">Ver no Portal</a>
            </div>
            """
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 650px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
            <div style="background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h1 style="color: #1a365d; border-bottom: 3px solid #2563eb; padding-bottom: 15px; margin-top: 0;">
                    🔔 Novas Licitações
                </h1>
                <p style="color: #666; font-size: 15px;">
                    Encontramos <strong style="color: #2563eb;">{len(licitacoes)}</strong> nova(s) licitação(ões) que correspondem aos seus critérios.
                </p>
                {cards}
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0 20px 0;">
                <p style="color: #999; font-size: 12px; text-align: center; margin: 0;">
                    Monitor de Licitações Brasil 🇧🇷
                </p>
            </div>
        </body>
        </html>
        """


# ============================================================================
# OPCIÓN 2: SENDGRID
# ============================================================================

class EmailSendGrid:
    """
    Envía emails usando SendGrid API
    
    Configuración:
        1. Crear cuenta en https://sendgrid.com
        2. Settings > API Keys > Create API Key
        3. Verificar email remitente (Sender Authentication)
        
    Tier gratuito: 100 emails/día
    """
    
    def __init__(self, api_key: str, email_from: str, email_to: str):
        self.api_key = api_key
        self.email_from = email_from
        self.email_to = email_to
        self.api_url = "https://api.sendgrid.com/v3/mail/send"
    
    def testar_conexao(self) -> bool:
        """Prueba enviando un email de test"""
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "personalizations": [{"to": [{"email": self.email_to}]}],
                    "from": {"email": self.email_from},
                    "subject": "🧪 Test - Monitor Licitações",
                    "content": [{"type": "text/html", "value": "<p>Conexão funcionando! ✅</p>"}]
                },
                timeout=10
            )
            return response.status_code in [200, 202]
        except Exception as e:
            print(f"❌ Error: {e}")
            return False
    
    def enviar(self, licitacoes: List[Licitacao], assunto: str = None) -> bool:
        """Envía email con las licitaciones"""
        
        if not licitacoes:
            return True
        
        if not assunto:
            assunto = f"🔔 {len(licitacoes)} Nova(s) Licitação(ões)"
        
        # Reutilizamos el generador de HTML de Resend
        html = EmailResend._gerar_html(self, licitacoes)
        
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "personalizations": [{"to": [{"email": self.email_to}]}],
                    "from": {"email": self.email_from},
                    "subject": assunto,
                    "content": [{"type": "text/html", "value": html}]
                },
                timeout=30
            )
            
            if response.status_code in [200, 202]:
                print(f"✅ Email enviado para {self.email_to}")
                return True
            else:
                print(f"❌ Error {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error: {e}")
            return False


# ============================================================================
# OPCIÓN 3: SMTP DIRECTO (Gmail, Outlook, etc.)
# ============================================================================

class EmailSMTP:
    """
    Envía emails usando SMTP directo (Gmail, Outlook, etc.)
    
    Para Gmail:
        1. Activar 2FA en tu cuenta Google
        2. Crear App Password: myaccount.google.com > Security > App passwords
        3. Usar esa contraseña (no tu contraseña normal)
        
    Para Outlook:
        smtp_servidor: smtp.office365.com
        smtp_porta: 587
    """
    
    def __init__(
        self, 
        email_from: str, 
        senha: str, 
        email_to: str,
        smtp_servidor: str = "smtp.gmail.com",
        smtp_porta: int = 587
    ):
        self.email_from = email_from
        self.senha = senha
        self.email_to = email_to
        self.smtp_servidor = smtp_servidor
        self.smtp_porta = smtp_porta
    
    def testar_conexao(self) -> bool:
        """Prueba la conexión SMTP"""
        import smtplib
        try:
            with smtplib.SMTP(self.smtp_servidor, self.smtp_porta) as server:
                server.starttls()
                server.login(self.email_from, self.senha)
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False
    
    def enviar(self, licitacoes: List[Licitacao], assunto: str = None) -> bool:
        """Envía email con las licitaciones"""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        if not licitacoes:
            return True
        
        if not assunto:
            assunto = f"🔔 {len(licitacoes)} Nova(s) Licitação(ões)"
        
        html = EmailResend._gerar_html(self, licitacoes)
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = self.email_from
        msg["To"] = self.email_to
        msg.attach(MIMEText(html, "html", "utf-8"))
        
        try:
            with smtplib.SMTP(self.smtp_servidor, self.smtp_porta) as server:
                server.starttls()
                server.login(self.email_from, self.senha)
                server.send_message(msg)
            print(f"✅ Email enviado para {self.email_to}")
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False


# ============================================================================
# FUNCIÓN HELPER PARA CREAR EL NOTIFICADOR
# ============================================================================

def criar_notificador(config: dict):
    """
    Crea el notificador según la configuración
    
    Args:
        config: Diccionario con la configuración de email
        
    Ejemplo config:
        {
            "provedor": "resend",  # o "sendgrid" o "smtp"
            "api_key": "re_xxxxx",
            "email_from": "onboarding@resend.dev",
            "email_to": "tu@email.com"
        }
    """
    
    provedor = config.get("provedor", "resend").lower()
    
    if provedor == "resend":
        return EmailResend(
            api_key=config["api_key"],
            email_from=config.get("email_from", "onboarding@resend.dev"),
            email_to=config["email_to"]
        )
    
    elif provedor == "sendgrid":
        return EmailSendGrid(
            api_key=config["api_key"],
            email_from=config["email_from"],
            email_to=config["email_to"]
        )
    
    elif provedor == "smtp":
        return EmailSMTP(
            email_from=config["email_from"],
            senha=config["senha"],
            email_to=config["email_to"],
            smtp_servidor=config.get("smtp_servidor", "smtp.gmail.com"),
            smtp_porta=config.get("smtp_porta", 587)
        )
    
    else:
        raise ValueError(f"Provedor no soportado: {provedor}")


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Licitación de ejemplo para pruebas
    licitacao_teste = Licitacao(
        id="TESTE-001",
        titulo="Aquisição de equipamentos de informática para modernização do datacenter municipal",
        objeto="Aquisição de servidores, storage e equipamentos de rede",
        orgao="Prefeitura Municipal de São Paulo",
        valor_estimado=1500000.00,
        modalidade="Pregão Eletrônico",
        uf="SP",
        municipio="São Paulo",
        data_publicacao="2024-01-15",
        data_abertura="2024-01-30",
        url="https://pncp.gov.br/app/editais/exemplo",
        fonte="PNCP"
    )
    
    print("="*60)
    print("EJEMPLO DE CONFIGURACIÓN")
    print("="*60)
    
    print("""
Para usar Resend (recomendado):

1. Crear cuenta en https://resend.com (gratis)
2. Obtener API Key
3. Configurar así:

    from email_notificador import EmailResend
    
    email = EmailResend(
        api_key="re_xxxxxxxx",
        email_from="onboarding@resend.dev",  # Para testing
        email_to="tu.email@gmail.com"
    )
    
    # Testar conexión
    if email.testar_conexao():
        print("✅ Conectado!")
    
    # Enviar licitaciones
    email.enviar([licitacao1, licitacao2])
""")

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
# HTML DEL SHORTLIST DE CANDIDATAS (triaje por reglas)
# ============================================================================

def gerar_html_shortlist(candidatas: list, dias_horizonte: int, max_cards: int = 30) -> str:
    """HTML para el email de candidatas SIN muros próximas a vencer.

    Muestra como máximo max_cards (las más urgentes); el resto queda
    referenciado en candidatas.csv para no generar un email inmanejable.
    """

    restantes = max(0, len(candidatas) - max_cards)
    cards = ""
    for c in candidatas[:max_cards]:
        valor = f"R$ {c['valor']:,.2f}" if c.get("valor") else "Não informado"
        piso = f"R$ {c['piso_50']:,.2f}" if c.get("piso_50") else "-"
        flags = []
        if c.get("software_publico") == "SI":
            flags.append("🟢 software público")
        if c.get("poc") == "SI":
            flags.append("PoC/amostra")
        if c.get("fabrica_pf") == "SI":
            flags.append("fábrica PF")
        if c.get("spec_pesada") == "SI":
            flags.append("spec pesada")
        flags_txt = " | ".join(flags) if flags else "sem alertas"
        cards += f"""
        <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin: 15px 0; background: #fafafa;">
            <h3 style="color: #1a365d; margin: 0 0 15px 0; font-size: 16px; line-height: 1.4;">{(c.get('objeto') or '')[:200]}</h3>
            <table style="width: 100%; font-size: 14px; color: #444;">
                <tr><td style="padding: 4px 0; width: 140px;"><strong>🏢 Órgão:</strong></td><td>{c.get('orgao') or ''}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>📍 Local:</strong></td><td>{c.get('municipio') or 'N/A'} - {c.get('uf') or ''}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>⏰ Encerramento:</strong></td><td style="color: #b45309; font-weight: bold;">{c.get('data_encerramento') or '?'}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>💰 Valor:</strong></td><td style="color: #2d7a2d; font-weight: bold;">{valor} <span style="color:#999; font-weight:normal;">(piso 50%: {piso})</span></td></tr>
                <tr><td style="padding: 4px 0;"><strong>🖥 Plataforma:</strong></td><td>{c.get('plataforma') or '?'}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>🏷 ME/EPP:</strong></td><td>{c.get('me_epp') or '?'}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>🚩 Flags:</strong></td><td>{flags_txt}</td></tr>
            </table>
            <a href="{c.get('url') or '#'}" style="display: inline-block; margin-top: 12px; padding: 10px 20px; background: #2563eb; color: white; text-decoration: none; border-radius: 5px; font-size: 14px;">Ver no PNCP</a>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 650px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
        <div style="background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h1 style="color: #1a365d; border-bottom: 3px solid #16a34a; padding-bottom: 15px; margin-top: 0;">
                🎯 Candidatas sem muros
            </h1>
            <p style="color: #666; font-size: 15px;">
                <strong style="color: #16a34a;">{len(candidatas)}</strong> candidata(s) SEM muro econômico
                com encerramento nos próximos <strong>{dias_horizonte}</strong> dias.
                Shortlist completo em <code>candidatas.csv</code>.
            </p>
            {cards}
            {f'<p style="color: #666; font-size: 14px;">… e mais <strong>{restantes}</strong> candidata(s) no <code>candidatas.csv</code>.</p>' if restantes else ''}
            <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0 20px 0;">
            <p style="color: #999; font-size: 12px; text-align: center; margin: 0;">
                Monitor de Licitações Brasil 🇧🇷 — triagem por regras
            </p>
        </div>
    </body>
    </html>
    """


# ============================================================================
# HTML DEL AVISO DE CAMBIOS (aplazamientos, suspensiones, valor)
# ============================================================================

_ETIQUETAS_CAMPO = {
    "data_encerramento": "⏰ Encerramento das propostas",
    "data_abertura": "📬 Abertura das propostas",
    "situacao": "🚦 Situação",
    "valor_estimado": "💰 Valor estimado",
}


def formatar_valor_cambio(campo: str, valor) -> str:
    """'2026-10-02T08:30:00' → '02/10/2026 08:30'; '2026-09-22' → '22/09/2026'."""
    if valor is None or valor == "":
        return "—"
    if campo.startswith("data_"):
        s = str(valor)
        fecha = f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
        return f"{fecha} {s[11:16]}" if len(s) >= 16 else fecha
    if campo == "valor_estimado":
        try:
            return f"R$ {float(valor):,.2f}"
        except ValueError:
            return str(valor)
    return str(valor)


def gerar_html_cambios(cambios: list) -> str:
    """HTML del aviso de cambios. `cambios` sale de
    DatabaseLicitacoes.cambios_pendientes_aviso(): una fila por campo
    cambiado; aquí se agrupan por licitación."""

    por_lic = {}
    for c in cambios:
        por_lic.setdefault(c["licitacao_id"], []).append(c)

    cards = ""
    for lic_id, lista in por_lic.items():
        c0 = lista[0]
        filas = ""
        for c in lista:
            antes = formatar_valor_cambio(c["campo"], c["valor_anterior"])
            despues = formatar_valor_cambio(c["campo"], c["valor_nuevo"])
            color = "#b91c1c" if c["campo"] == "situacao" else "#b45309"
            filas += f"""
                <tr><td style="padding: 4px 0; width: 210px;"><strong>{_ETIQUETAS_CAMPO.get(c['campo'], c['campo'])}:</strong></td>
                    <td><span style="text-decoration: line-through; color: #999;">{antes}</span>
                        → <span style="color: {color}; font-weight: bold;">{despues}</span></td></tr>"""
        muro = " · ⚠️ muro econômico" if c0.get("muro_economico") == "SI" else ""
        cards += f"""
        <div style="border: 1px solid #fcd34d; border-radius: 8px; padding: 20px; margin: 15px 0; background: #fffbeb;">
            <h3 style="color: #1a365d; margin: 0 0 6px 0; font-size: 16px; line-height: 1.4;">{(c0.get('objeto') or '')[:200]}</h3>
            <p style="margin: 0 0 12px 0; color: #666; font-size: 13px;">{c0.get('orgao') or ''} — {c0.get('municipio') or 'N/A'}/{c0.get('uf') or ''}<br>
               <code>{lic_id}</code> · {c0.get('modalidade') or ''} · plataforma: {c0.get('plataforma') or '?'}{muro}</p>
            <table style="width: 100%; font-size: 14px; color: #444;">{filas}
            </table>
            <p style="margin: 10px 0 0 0; font-size: 12px; color: #999;">Detectado em {c0.get('detectado_em') or ''} · fonte: {c0.get('fuente') or ''}</p>
            <a href="{c0.get('url') or '#'}" style="display: inline-block; margin-top: 12px; padding: 10px 20px; background: #2563eb; color: white; text-decoration: none; border-radius: 5px; font-size: 14px;">Ver no PNCP</a>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 650px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
        <div style="background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h1 style="color: #1a365d; border-bottom: 3px solid #f59e0b; padding-bottom: 15px; margin-top: 0;">
                📅 Mudanças em licitações acompanhadas
            </h1>
            <p style="color: #666; font-size: 15px;">
                O PNCP mudou <strong style="color: #b45309;">{len(por_lic)}</strong> licitação(ões)
                desde a última passada. Confirmar a nova data na plataforma antes de cadastrar a proposta.
            </p>
            {cards}
            <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0 20px 0;">
            <p style="color: #999; font-size: 12px; text-align: center; margin: 0;">
                Monitor de Licitações Brasil 🇧🇷 — histórico em <code>historico_cambios</code>
            </p>
        </div>
    </body>
    </html>
    """


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
        
        return self.enviar_html(assunto, self._gerar_html(licitacoes))

    def enviar_html(self, assunto: str, html: str) -> bool:
        """Envía un email con HTML arbitrario (p.ej. el shortlist del triaje)"""

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
        return self.enviar_html(assunto, EmailResend._gerar_html(self, licitacoes))

    def enviar_html(self, assunto: str, html: str) -> bool:
        """Envía un email con HTML arbitrario (p.ej. el shortlist del triaje)"""

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
        
        return self.enviar_html(assunto, EmailResend._gerar_html(self, licitacoes))

    def enviar_html(self, assunto: str, html: str) -> bool:
        """Envía un email con HTML arbitrario (p.ej. el shortlist del triaje)"""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

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

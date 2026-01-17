# 🇧🇷 Monitor de Licitações Brasil

Monitor simple para buscar licitaciones públicas en Brasil y recibir alertas por email.

## USO

1. cd /ruta/al/proyecto
2. source licitaciones_venv/bin/activate
3. python monitor.py

## 🚀 Instalación (2 minutos)

```bash
# 1. Instalar dependencias
pip install requests

# 2. Configurar email (ver abajo)
nano config.json

# 3. Ejecutar
python monitor.py
```

## 📧 Configurar Email con Resend (GRATIS)

**Resend** es la opción más fácil - 100 emails gratis por día.

### Paso 1: Crear cuenta

1. Ve a [resend.com](https://resend.com) y crea cuenta (gratis)
2. En el Dashboard, copia tu **API Key**

### Paso 2: Configurar

Edita `config.json`:

```json
{
  "busca": {
    "termos": ["software", "tecnologia"],
    "ufs": ["SP", "RJ"],
    "dias_atras": 7
  },

  "email": {
    "provedor": "resend",
    "api_key": "re_xxxxxxxxx",           <-- Tu API Key
    "email_from": "onboarding@resend.dev",
    "email_to": "tu.email@gmail.com"      <-- Tu email
  }
}
```

### Paso 3: Probar

```bash
python monitor.py --test-email
```

## 📋 Uso

```bash
# Buscar con la configuración de config.json
python monitor.py

# Buscar términos específicos
python monitor.py --termos software cloud

# Buscar en estados específicos
python monitor.py --uf SP RJ MG

# Buscar últimos 14 días
python monitor.py --dias 14

# Buscar sin enviar email
python monitor.py --sem-email

# Probar email
python monitor.py --test-email

# Ver estadísticas
python monitor.py --stats

# Exportar a CSV
python monitor.py --exportar
```

## 📁 Archivos

```
monitor-licitacoes/
├── monitor.py           # Script principal (ejecutar este)
├── config.json          # Tu configuración
├── apis_licitacoes.py   # Conexión con APIs del gobierno
├── database.py          # Base de datos local
├── email_notificador.py # Envío de emails
└── licitacoes.db        # Base de datos (se crea automáticamente)
```

## 🔄 Automatizar (Opcional)

### Linux/Mac (cron)

```bash
# Ejecutar todos los días a las 9am
crontab -e
0 9 * * * cd /ruta/monitor-licitacoes && python monitor.py
```

### Windows (Task Scheduler)

1. Abrir "Programador de tareas"
2. Crear tarea básica
3. Configurar para ejecutar `python monitor.py`

## 📊 Fuente de Datos

Los datos vienen del **PNCP** (Portal Nacional de Contratações Públicas), que es la fuente oficial del gobierno brasileño que unifica licitaciones de:

- Gobierno Federal
- Estados
- Municipios

## ❓ Alternativas de Email

Si no quieres usar Resend, puedes usar:

### SendGrid

```json
"email": {
  "provedor": "sendgrid",
  "api_key": "SG.xxxxx",
  "email_from": "tu@dominio.com",
  "email_to": "destino@email.com"
}
```

### Gmail SMTP

```json
"email": {
  "provedor": "smtp",
  "email_from": "tu.email@gmail.com",
  "senha": "tu_app_password",
  "email_to": "destino@email.com",
  "smtp_servidor": "smtp.gmail.com",
  "smtp_porta": 587
}
```

Para Gmail necesitas crear una "App Password" en tu cuenta Google.

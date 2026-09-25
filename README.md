# 🇧🇷 Monitor de Licitações Brasil

Monitor simple para buscar licitaciones públicas en Brasil y recibir alertas por email.

## USO

1. cd /ruta/al/proyecto
2. source licitaciones_env/bin/activate
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
    "dias_adelante": 7
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

# Descargar documentos pendientes (pestaña "Arquivos" del PNCP)
python monitor.py --descargar

# Descargar solo las 5 licitaciones más recientes
python monitor.py --descargar --limite 5

# Triar pendientes y regenerar candidatas.csv (también corre solo tras cada búsqueda)
python monitor.py --triar
```

## 🧮 Triaje por reglas (sin API de IA)

Tras descargar, cada carpeta pasa por un triaje 100% local:

1. **Extracción de texto** (`extractor_texto.py`): `pdftotext` para PDFs con
   texto, **OCR con tesseract** (2 primeras páginas) para escaneados,
   `zipfile` para docx/odt y `textutil` para doc/rtf/html. El texto se cachea
   en `_texto_extraido.txt` dentro de cada carpeta (no se reprocesa).
2. **Clasificación recall-first** (`triaje.py`): prioriza no perder candidatas.
   Solo se aparta lo confirmado NO_SOFTWARE (se **mueve** a
   `descargas/NO_SOFTWARE/`, nada se borra); todo lo que tenga señal de
   software o sea ambiguo queda como CANDIDATA. La lista `falsos_amigos` de
   `config.json` evita que señales genéricas (`sistema`, `plataforma`,
   `desenvolvimento`) marquen como software cosas como "sistema de esgoto" o
   "plataforma elevatória".
3. **Detección de muros** — 🔑 **REGLA DE ORO: el ÚNICO muro que descarta es
   el económico** (`muro_economico=SI`: balanço + índices/patrimônio líquido,
   con `PL_positivo=false`). Todo lo demás **solo se etiqueta** para que lo
   revises a mano, nunca descarta ni penaliza el orden:
   - `poc` (SI/NO): prova de conceito/amostra/demonstração. Se extrae además
     `poc_roteiro` (bloque del roteiro) para juzgar el alcance del producto.
   - `atestado_exigido` (texto): el requisito de atestado, tal cual.
   - `fabrica_pf` (SI/NO): pontos de função.
   - `spec_pesada` (SI/NO): gov.br login/ICP-Brasil/app nativo/migração massiva.
   - `software_publico` (SI/NO): i-Educar/e-Cidade/software livre/código
     aberto → categoría **accesible**, sube al tope del shortlist.
4. **Shortlist `candidatas.csv`** (separador `;`): ordenado abiertas primero,
   luego software público arriba, las descartadas por muro económico al fondo
   (`descarte=SI`, siguen visibles para auditar), y por fecha de cierre. Se
   regenera completo en cada corrida.
5. **Email**: solo candidatas sin muro económico con cierre dentro de
   `triaje.dias_alerta_vencimiento` días (default 7).

### Flag del perfil (config.json)

```json
"perfil": {
  "PL_positivo": false   // false => las muro_economico=SI se descartan (van al fondo)
}
```

Cuando tu situación cambie (capitalización), pon `PL_positivo` en `true` y
regenera el CSV (`python triaje.py --solo-csv`): esas candidatas dejan de
descartarse y **suben solas** al shortlist.

### Comandos del triaje

```bash
python triaje.py            # tría lo pendiente y regenera el CSV
python triaje.py --todo     # (re)tría TODAS las carpetas (backlog)
python triaje.py --carpeta PNCP-{cnpj}-{año}-{seq}   # una sola
python triaje.py --solo-csv # solo regenerar candidatas.csv
python triaje.py --forzar   # ignora la caché de texto extraído
```

### Backfill de fechas de cierre

Las licitaciones guardadas antes de la migración no tienen `data_encerramento`
(necesaria para las alertas de "próximas a vencer"). Se rellena consultando el
PNCP; es relanzable (solo procesa las que faltan):

```bash
python backfill_encerramento.py
```

### OCR en portugués

El proyecto usa `tessdata/por.traineddata` (tessdata_fast). Si falta, se
descarga así:

```bash
mkdir -p tessdata && curl -sL -o tessdata/por.traineddata \
  https://github.com/tesseract-ocr/tessdata_fast/raw/main/por.traineddata
```

## 📥 Descarga de documentos

Tras cada búsqueda (después de enviar el email) se descargan automáticamente
los documentos de las licitaciones nuevas usando la API pública del PNCP.
Los `.zip`/`.rar` se descomprimen recursivamente (incluso comprimidos dentro
de comprimidos) en una carpeta por licitación:

```
descargas/
└── PNCP-{cnpj}-{año}-{seq}/
    ├── edital.pdf
    ├── anexos.zip
    └── anexos/          # contenido extraído
```

Se controla en `config.json`:

```json
"descargas": {
  "activado": true,
  "pasta_salida": "descargas"
}
```

Los RAR se extraen con `bsdtar` (incluido en macOS). Si algún RAR muy nuevo
falla, instalar `unar` como respaldo: `brew install unar`.

### Registro anti re-descarga (no volver a bajar lo descartado)

El "registro de vistas" es la propia base de datos (tabla `triaje`): no hay un
`vistas.json` aparte que mantener sincronizado. Dos capas evitan re-bajar lo
ya clasificado NO_SOFTWARE:

- **Cola del monitor** (`obtener_pendientes_descarga`): excluye del `SELECT`
  todo lo que sea `bucket='NO_SOFTWARE'`, aunque su carpeta desaparezca.
- **Pre-clasificación por objeto**: antes de tocar la red, si el objeto es
  NO_SOFTWARE de alta confianza se registra en `triaje` y **no se descarga
  ningún archivo** (se ahorra la descarga desde la primera vez).

## 📁 Archivos

```
monitor-licitacoes/
├── monitor.py                  # Script principal (ejecutar este)
├── config.json                 # Tu configuración (perfil, triaje, email)
├── apis_licitacoes.py          # Conexión con APIs del gobierno
├── descargador_documentos.py   # Descarga y descompresión de documentos
├── extractor_texto.py          # Extracción de texto (PDF/OCR/docx/rtf/odt)
├── triaje.py                   # Triaje por reglas + candidatas.csv
├── database.py                 # Base de datos local
├── email_notificador.py        # Envío de emails
├── licitacoes.db               # Base de datos (se crea automáticamente)
├── candidatas.csv              # Shortlist ordenado (salida del triaje)
├── tessdata/                   # Idioma portugués para el OCR
└── descargas/                  # Documentos descargados por licitación
    └── NO_SOFTWARE/            # Carpetas apartadas por el triaje
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

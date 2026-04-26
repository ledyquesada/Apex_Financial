# ⚡ APEX — Financial Intelligence

Superagente financiero completo con datos en tiempo real, análisis IA, alertas automáticas y gestión de portafolio.

## Características
- 📊 Precios en tiempo real (Alpha Vantage)
- 🔍 Buscar cualquier acción del mundo
- 💼 Agregar/quitar inversiones desde la app
- 🚀 Oportunidades a corto y largo plazo
- ₿ Cripto en tiempo real (CoinGecko)
- 📰 Noticias con análisis de sentimiento
- 📧 Alertas automáticas cada 4 horas a 2 correos
- 🚨 Alertas urgentes de señales de venta
- 🤖 Análisis experto con Claude (Anthropic)
- 📱 Responsive — celular y PC

---

## Deploy en Railway (5-10 minutos)

### 1. Sube a GitHub
Crea repositorio `apex-financial` en github.com y sube todos estos archivos.

### 2. Deploy
1. Ve a **railway.app** → login con GitHub
2. **New Project** → **Deploy from GitHub repo** → selecciona `apex-financial`

### 3. Variables de entorno (obligatorias)
En Railway → tu proyecto → **Variables**:

| Variable | Valor | Dónde obtener |
|----------|-------|---------------|
| `ANTHROPIC_API_KEY` | sk-ant-... | console.anthropic.com |
| `ALPHA_VANTAGE_KEY` | tu-key | alphavantage.co/support/#api-key |
| `NEWS_API_KEY` | tu-key | newsapi.org → Get API Key (gratis) |
| `EMAIL_FROM` | tu-gmail@gmail.com | Tu cuenta Gmail |
| `EMAIL_PASS` | xxxx xxxx xxxx xxxx | Gmail → Seguridad → Contraseñas de app |
| `EMAIL_1` | tu-correo@gmail.com | Tu correo para alertas |
| `EMAIL_2` | yorguin@gmail.com | Correo de tu esposo |

### Cómo crear contraseña de app Gmail
1. Entra a myaccount.google.com
2. Seguridad → Verificación en 2 pasos (debe estar activada)
3. Seguridad → Contraseñas de aplicaciones
4. Crea una para "Correo / Windows"
5. Copia los 16 caracteres → eso es EMAIL_PASS

### 4. Tu URL pública
Railway genera automáticamente:
`https://apex-financial-production.up.railway.app`

¡Ábrela desde celular, PC, donde quieras!

---

## Variables opcionales
Si no configuras EMAIL_*, la app funciona igual pero sin notificaciones por correo.

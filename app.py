import os, json, requests, smtplib, threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
CORS(app)

# ── ENV VARS ──────────────────────────────────────────────
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AV_KEY        = os.environ.get("ALPHA_VANTAGE_KEY", "")
NEWS_API_KEY  = os.environ.get("NEWS_API_KEY", "")     # newsapi.org - gratis
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "")       # tu gmail
EMAIL_PASS    = os.environ.get("EMAIL_PASS", "")       # app password de gmail
EMAIL_1       = os.environ.get("EMAIL_1", "")          # tu correo
EMAIL_2       = os.environ.get("EMAIL_2", "")          # correo de Yorguin

# ── PORTFOLIO STORAGE (JSON file) ────────────────────────
PORTFOLIO_FILE = "portfolio.json"

DEFAULT_PORTFOLIO = [
    {"symbol": "QQQM",  "name": "Invesco NASDAQ 100 ETF",  "shares": 0, "avg_price": 0, "amount": 52.5,  "allocation": 15},
    {"symbol": "VT",    "name": "Vanguard Total World ETF", "shares": 0, "avg_price": 0, "amount": 157.5, "allocation": 45},
    {"symbol": "GOOGL", "name": "Alphabet (Google)",        "shares": 0, "avg_price": 0, "amount": 52.5,  "allocation": 15},
    {"symbol": "CVX",   "name": "Chevron Corporation",      "shares": 0, "avg_price": 0, "amount": 52.5,  "allocation": 15},
    {"symbol": "IAU",   "name": "iShares Gold Trust ETF",   "shares": 0, "avg_price": 0, "amount": 35.0,  "allocation": 10},
]

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return DEFAULT_PORTFOLIO.copy()

def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

# ── ALPHA VANTAGE HELPERS ─────────────────────────────────
def get_quote(symbol):
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        q = r.json().get("Global Quote", {})
        if not q or not q.get("05. price"):
            return None
        return {
            "symbol": symbol,
            "price":      float(q.get("05. price", 0)),
            "change":     float(q.get("09. change", 0)),
            "change_pct": q.get("10. change percent", "0%").replace("%",""),
            "volume":     q.get("06. volume", "N/A"),
            "prev_close": float(q.get("08. previous close", 0)),
            "high":       float(q.get("03. high", 0)),
            "low":        float(q.get("04. low", 0)),
            "open":       float(q.get("02. open", 0)),
        }
    except:
        return None

def get_rsi(symbol):
    try:
        url = f"https://www.alphavantage.co/query?function=RSI&symbol={symbol}&interval=daily&time_period=14&series_type=close&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        vals = r.json().get("Technical Analysis: RSI", {})
        if not vals: return None
        latest = sorted(vals.keys())[-1]
        return round(float(vals[latest]["RSI"]), 2)
    except:
        return None

def get_macd(symbol):
    try:
        url = f"https://www.alphavantage.co/query?function=MACD&symbol={symbol}&interval=daily&series_type=close&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        vals = r.json().get("Technical Analysis: MACD", {})
        if not vals: return None
        latest = sorted(vals.keys())[-1]
        d = vals[latest]
        return {
            "macd":   round(float(d.get("MACD", 0)), 4),
            "signal": round(float(d.get("MACD_Signal", 0)), 4),
            "hist":   round(float(d.get("MACD_Hist", 0)), 4),
        }
    except:
        return None

def get_crypto():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,cardano,polkadot&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
        r = requests.get(url, timeout=10)
        return r.json()
    except:
        return {}

def get_news(tickers="AAPL,GOOGL,SPY"):
    """Noticias financieras con sentimiento desde Alpha Vantage"""
    try:
        url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={tickers}&sort=LATEST&limit=15&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        feed = r.json().get("feed", [])
        return [{"title": n.get("title",""), "summary": n.get("summary","")[:250],
                 "source": n.get("source",""), "sentiment": n.get("overall_sentiment_label","Neutral"),
                 "time": n.get("time_published","")[:8], "type": "financial"} for n in feed[:10]]
    except:
        return []

def get_global_news():
    """Noticias mundiales generales desde NewsAPI que pueden impactar mercados"""
    if not NEWS_API_KEY:
        return []
    try:
        # Temas clave que mueven mercados
        queries = [
            "Federal Reserve interest rates economy",
            "geopolitical conflict oil markets",
            "inflation GDP economic data",
            "stock market crash rally",
            "China US trade war sanctions",
        ]
        all_news = []
        for q in queries[:3]:  # 3 queries para no gastar cuota
            url = (f"https://newsapi.org/v2/everything?q={requests.utils.quote(q)}"
                   f"&language=en&sortBy=publishedAt&pageSize=3&apiKey={NEWS_API_KEY}")
            r = requests.get(url, timeout=10)
            articles = r.json().get("articles", [])
            for a in articles:
                if a.get("title") and "[Removed]" not in a.get("title",""):
                    all_news.append({
                        "title":     a.get("title",""),
                        "summary":   (a.get("description") or "")[:250],
                        "source":    a.get("source",{}).get("name",""),
                        "sentiment": "Global",
                        "time":      (a.get("publishedAt","")[:10]),
                        "type":      "global",
                        "url":       a.get("url",""),
                    })
        # Deduplicar por título
        seen = set()
        unique = []
        for n in all_news:
            if n["title"] not in seen:
                seen.add(n["title"])
                unique.append(n)
        return unique[:12]
    except Exception as e:
        print(f"NewsAPI error: {e}")
        return []

def get_fear_greed():
    """Índice Fear & Greed del mercado (CNN / alternative.me)"""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json().get("data", [{}])[0]
        return {
            "value":       int(d.get("value", 50)),
            "label":       d.get("value_classification", "Neutral"),
            "timestamp":   d.get("timestamp", ""),
        }
    except:
        return None

def search_symbol(query):
    try:
        url = f"https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords={query}&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        matches = r.json().get("bestMatches", [])
        return [{"symbol": m.get("1. symbol",""), "name": m.get("2. name",""),
                 "type": m.get("3. type",""), "region": m.get("4. region","")} for m in matches[:8]]
    except:
        return []

# ── CLAUDE API ────────────────────────────────────────────
SYSTEM_PROMPT = """Eres APEX — superagente financiero de élite, especialista en bolsa de valores con visión global. Combinas análisis técnico, fundamental, macroeconómico y geopolítico para dar recomendaciones con el mínimo margen de error posible.

## FILOSOFÍA DE ANÁLISIS
No haces análisis superficiales. Cada respuesta cruza TODAS las variables que pueden afectar el precio de una acción:

### Variables que siempre consideras:
**Técnicas**: RSI, MACD, medias móviles (50/200), volumen, soportes, resistencias, patrones de velas, divergencias
**Fundamentales**: P/E ratio, earnings, revenue growth, deuda, márgenes, guidance
**Macroeconómicas**: tasas de interés Fed, inflación (CPI/PPI), empleo (NFP), PIB, ciclo económico
**Geopolíticas**: conflictos armados, sanciones, relaciones comerciales, estabilidad política
**Sectoriales**: precio del petróleo (energía), semiconductores (tech), tasas (financieras), dólar (exportadoras)
**Sentimiento**: Fear & Greed Index, flujos institucionales, short interest, opciones
**Noticias globales**: cualquier evento mundial que pueda crear volatilidad o cambio de tendencia
**Correlaciones**: cómo se mueven en conjunto diferentes activos (oro vs dólar, cripto vs riesgo, etc.)

## SUB-AGENTES INTERNOS
**📈 Agente Técnico**: RSI, MACD, medias móviles, soportes/resistencias, patrones
**📰 Agente de Noticias Financieras**: earnings, Fed, sector, empresa específica
**🌍 Agente de Noticias Globales**: geopolítica, macro, eventos mundiales con impacto en mercados
**💼 Agente de Portafolio**: diversificación, correlación, optimización, riesgo total
**🔍 Agente de Oportunidades**: escaneo global de activos con potencial real
**⚠️ Agente de Riesgo**: escenarios adversos, stop loss, hedging, volatilidad implícita

## FORMATO DE RESPUESTA
**🧠 Agentes Activados**: [lista]
**📊 Datos en Tiempo Real**: precios, RSI, MACD actuales
**🌍 Contexto Mundial**: noticias globales relevantes y su impacto
**📰 Contexto Financiero**: noticias del sector/empresa
**📈 Análisis Técnico**: indicadores con números reales
**🏦 Análisis Fundamental**: métricas clave si aplica
**😱 Sentimiento**: Fear & Greed, flujos, posicionamiento
**🎯 Señal**: COMPRAR 🟢 / VENDER 🔴 / MANTENER 🟡 / ESPERAR ⏳
**💰 Acción Recomendada**: qué hacer exactamente, con cuánto dinero
**📅 Horizonte**: cuándo ejecutar y por qué ese momento
**⚠️ Stop Loss**: precio exacto de salida y por qué ese nivel
**🔮 Proyección**: escenarios optimista/base/pesimista a 30/90/180 días con precios objetivo

## REGLAS DE ORO
- Nunca emitas señal sin cruzar al menos 3 variables diferentes
- Si hay contradicción entre técnico y fundamental, explícala y da peso a cada una
- Un evento geopolítico mayor puede invalidar cualquier análisis técnico — siempre contextualizas
- El Fear & Greed extremo (>80 o <20) es señal contraria — explica por qué
- Para acciones individuales, los earnings son el evento más importante — siempre lo mencionas
- Siempre menciona el catalizador más probable que haría moverse el precio

Idioma: siempre español. Sé específico con números reales. Actúa como el mejor analista financiero del mundo."""

def claude_chat(messages, market_context=""):
    if not ANTHROPIC_KEY:
        return "Error: ANTHROPIC_API_KEY no configurada."
    system = SYSTEM_PROMPT
    if market_context:
        system += f"\n\n## DATOS DE MERCADO EN TIEMPO REAL\n{market_context}"
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
            json={"model":"claude-opus-4-5","max_tokens":2000,"system":system,"messages":messages},
            timeout=90,
        )
        data = r.json()
        if "error" in data:
            return f"Error: {data['error']['message']}"
        return "".join(b["text"] for b in data.get("content",[]) if b["type"]=="text")
    except Exception as e:
        return f"Error de conexión: {str(e)}"

# ── EMAIL ─────────────────────────────────────────────────
def send_email(subject, html_body):
    if not EMAIL_FROM or not EMAIL_PASS:
        print("EMAIL no configurado")
        return False
    recipients = [e for e in [EMAIL_1, EMAIL_2] if e]
    if not recipients:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"APEX Financial ⚡ <{EMAIL_FROM}>"
        msg["To"]      = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, recipients, msg.as_string())
        print(f"Email enviado a {recipients}")
        return True
    except Exception as e:
        print(f"Error email: {e}")
        return False

def make_email_html(subject, analysis, urgent=False):
    color = "#ff4444" if urgent else "#00d4aa"
    icon  = "🚨" if urgent else "⚡"
    return f"""
<!DOCTYPE html><html><body style="background:#020b18;color:#ddf0f8;font-family:'Segoe UI',sans-serif;padding:24px;margin:0;">
<div style="max-width:600px;margin:0 auto;">
  <div style="background:linear-gradient(135deg,{color}22,#041428);border:1px solid {color}44;border-radius:16px;padding:24px;margin-bottom:20px;">
    <h1 style="color:{color};margin:0 0 4px;font-size:24px;">{icon} APEX Financial</h1>
    <p style="color:#4a9ebb;margin:0;font-size:12px;letter-spacing:2px;">FINANCIAL INTELLIGENCE · {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
  </div>
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(0,212,170,.15);border-radius:12px;padding:20px;white-space:pre-wrap;font-size:14px;line-height:1.7;">
{analysis}
  </div>
  <p style="color:#2d5a6e;font-size:11px;text-align:center;margin-top:16px;">⚠️ Análisis orientativo — Las decisiones de inversión son tuyas</p>
</div>
</body></html>"""

# ── SCHEDULED JOB (cada 4 horas) ─────────────────────────
def scheduled_analysis():
    print(f"[{datetime.now()}] Ejecutando análisis programado...")
    portfolio = load_portfolio()
    symbols = [p["symbol"] for p in portfolio]

    # Obtener datos
    quotes = []
    urgent_sells = []
    for p in portfolio:
        q = get_quote(p["symbol"])
        rsi = get_rsi(p["symbol"])
        if q:
            q["rsi"] = rsi
            q["amount"] = p.get("amount", 0)
            quotes.append(q)
            pct = float(q.get("change_pct", 0))
            if (rsi and rsi >= 72) or pct <= -3:
                urgent_sells.append({"symbol": p["symbol"], "rsi": rsi, "change_pct": pct, "price": q["price"]})

    crypto      = get_crypto()
    news        = get_news(",".join(symbols))
    global_news = get_global_news()
    fear_greed  = get_fear_greed()

    # Construir contexto enriquecido
    ctx = "PRECIOS ACTUALES:\n"
    for q in quotes:
        ctx += f"- {q['symbol']}: ${q['price']:.2f} ({float(q['change_pct']):+.2f}% hoy)"
        if q.get("rsi"): ctx += f" | RSI: {q['rsi']}"
        ctx += "\n"
    if crypto:
        ctx += "\nCRIPTO:\n"
        for k, v in list(crypto.items())[:3]:
            ctx += f"- {k}: ${v['usd']:,.2f} ({v.get('usd_24h_change',0):+.2f}% 24h)\n"
    if fear_greed:
        ctx += f"\nFEAR & GREED INDEX: {fear_greed['value']}/100 — {fear_greed['label']}\n"
    if news:
        ctx += "\nNOTICIAS FINANCIERAS:\n"
        for n in news[:5]:
            ctx += f"- [{n['sentiment']}] {n['title']}\n"
    if global_news:
        ctx += "\nNOTICIAS MUNDIALES (impacto en mercados):\n"
        for n in global_news[:6]:
            ctx += f"- [{n['source']}] {n['title']}\n"

    # Si hay señales urgentes de venta → email urgente
    if urgent_sells:
        analysis = claude_chat(
            [{"role":"user","content":f"ALERTA URGENTE: Los siguientes activos del portafolio muestran señales de venta: {urgent_sells}. Analiza con los datos de mercado y da recomendación inmediata de qué hacer."}],
            ctx
        )
        send_email("🚨 APEX ALERTA — Señal de venta urgente", make_email_html("Alerta Urgente", analysis, urgent=True))

    # Resumen completo cada 4 horas
    total_invested = sum(p.get("amount", 0) for p in portfolio)
    analysis_full = claude_chat(
        [{"role":"user","content":f"Genera el resumen financiero completo del portafolio de ${total_invested:.0f} USD. Incluye: estado de cada posición, señales técnicas, impacto de noticias, oportunidades de compra detectadas y 2-3 acciones con potencial que valdría la pena investigar para invertir."}],
        ctx
    )
    send_email("⚡ APEX — Análisis cada 4 horas", make_email_html("Resumen de Mercado", analysis_full))
    print("Análisis programado completado.")

# ── ROUTES ────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/portfolio", methods=["GET"])
def get_portfolio_route():
    return jsonify(load_portfolio())

@app.route("/api/portfolio/add", methods=["POST"])
def add_investment():
    data = request.json
    symbol    = data.get("symbol","").upper().strip()
    name      = data.get("name", symbol)
    amount    = float(data.get("amount", 0))
    avg_price = float(data.get("avg_price", 0))

    if not symbol or amount <= 0:
        return jsonify({"error": "Símbolo y monto requeridos"}), 400

    portfolio = load_portfolio()
    # Si ya existe, actualizar
    for p in portfolio:
        if p["symbol"] == symbol:
            p["amount"]    = round(p["amount"] + amount, 2)
            p["avg_price"] = avg_price if avg_price > 0 else p["avg_price"]
            save_portfolio(portfolio)
            return jsonify({"ok": True, "message": f"{symbol} actualizado"})

    # Si es nuevo, agregar
    portfolio.append({"symbol": symbol, "name": name, "shares": 0,
                      "avg_price": avg_price, "amount": amount, "allocation": 0})
    # Recalcular allocations
    total = sum(p["amount"] for p in portfolio)
    for p in portfolio:
        p["allocation"] = round((p["amount"] / total) * 100, 1)
    save_portfolio(portfolio)
    return jsonify({"ok": True, "message": f"{symbol} agregado al portafolio"})

@app.route("/api/portfolio/remove", methods=["POST"])
def remove_investment():
    symbol = request.json.get("symbol","").upper()
    portfolio = load_portfolio()
    portfolio = [p for p in portfolio if p["symbol"] != symbol]
    total = sum(p["amount"] for p in portfolio)
    for p in portfolio:
        p["allocation"] = round((p["amount"] / total) * 100, 1) if total > 0 else 0
    save_portfolio(portfolio)
    return jsonify({"ok": True})

@app.route("/api/market-data")
def market_data():
    portfolio = load_portfolio()
    quotes = []
    for p in portfolio:
        q = get_quote(p["symbol"])
        if q:
            q["rsi"]    = get_rsi(p["symbol"])
            q["amount"] = p.get("amount", 0)
            q["allocation"] = p.get("allocation", 0)
            q["avg_price"]  = p.get("avg_price", 0)
        else:
            q = {"symbol": p["symbol"], "error": True, "amount": p.get("amount",0)}
        quotes.append(q)

    tickers = ",".join(p["symbol"] for p in portfolio)
    return jsonify({
        "quotes":       quotes,
        "crypto":       get_crypto(),
        "news":         get_news(tickers),
        "global_news":  get_global_news(),
        "fear_greed":   get_fear_greed(),
        "status":       "ok",
        "updated":      datetime.now().strftime("%H:%M:%S"),
    })

@app.route("/api/search")
def search():
    q = request.args.get("q","")
    if not q:
        return jsonify([])
    results = search_symbol(q)
    # Enrich with quote
    for r in results[:3]:
        q_data = get_quote(r["symbol"])
        if q_data:
            r["price"]      = q_data["price"]
            r["change_pct"] = q_data["change_pct"]
    return jsonify(results)

@app.route("/api/chat", methods=["POST"])
def chat():
    body    = request.json
    msgs    = body.get("messages", [])
    context = body.get("market_context", "")
    response = claude_chat(msgs, context)
    return jsonify({"response": response})

@app.route("/api/opportunities", methods=["POST"])
def opportunities():
    body     = request.json
    horizon  = body.get("horizon", "both")   # short, long, both
    risk     = body.get("risk", "medium")     # low, medium, high
    amount   = body.get("amount", 100)
    context  = body.get("market_context", "")

    horizon_text = {"short":"corto plazo (1-3 meses)","long":"largo plazo (6-24 meses)","both":"corto Y largo plazo"}.get(horizon,"ambos")
    risk_text    = {"low":"conservador (bajo riesgo)","medium":"moderado","high":"agresivo (alto riesgo/alta recompensa)"}.get(risk,"moderado")

    prompt = f"""Analiza el mercado actual completo y dame las MEJORES 5-8 oportunidades de inversión para {horizon_text} con perfil {risk_text} y disponibilidad de ${amount} USD.

Para cada oportunidad incluye:
- Símbolo y nombre
- Por qué es una buena oportunidad AHORA
- Precio de entrada ideal
- Take profit objetivo
- Stop loss
- % de ganancia esperado
- Nivel de riesgo (1-5)
- Horizonte recomendado

Clasifica entre: Acciones individuales, ETFs, Cripto, Materias primas (oro, plata, petróleo)."""

    response = claude_chat([{"role":"user","content":prompt}], context)
    return jsonify({"response": response})

@app.route("/api/trigger-analysis", methods=["POST"])
def trigger_analysis():
    """Ejecuta análisis manual inmediato"""
    threading.Thread(target=scheduled_analysis).start()
    return jsonify({"ok": True, "message": "Análisis iniciado, recibirás el email en unos minutos."})

# ── SCHEDULER ─────────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_analysis, "interval", hours=4, id="apex_analysis")
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

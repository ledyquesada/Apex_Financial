import os, json, requests, smtplib, threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_DB = True
except ImportError:
    HAS_DB = False

app = Flask(__name__)
CORS(app)

# ── ENV VARS ──────────────────────────────────────────────
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AV_KEY        = os.environ.get("ALPHA_VANTAGE_KEY", "")
NEWS_API_KEY  = os.environ.get("NEWS_API_KEY", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "")
EMAIL_PASS    = os.environ.get("EMAIL_PASS", "")
EMAIL_1       = os.environ.get("EMAIL_1", "")
EMAIL_2       = os.environ.get("EMAIL_2", "")
DATABASE_URL  = os.environ.get("DATABASE_URL", "")

# ── DATABASE ──────────────────────────────────────────────
def get_db():
    if not HAS_DB or not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        return conn
    except Exception as e:
        print(f"DB connection error: {e}")
        return None

def init_db():
    conn = get_db()
    if not conn:
        print("No DB — chat history will not persist.")
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                role VARCHAR(16) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_session ON chat_sessions(session_id, created_at);
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("DB initialized OK")
    except Exception as e:
        print(f"DB init error: {e}")

def db_save_message(session_id, role, content):
    conn = get_db()
    if not conn: return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_sessions (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB save error: {e}")

def db_get_history(session_id, limit=50):
    conn = get_db()
    if not conn: return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT role, content, created_at FROM chat_sessions WHERE session_id=%s ORDER BY created_at DESC LIMIT %s",
            (session_id, limit)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        # Reverse so oldest first
        rows.reverse()
        return [{"role": r["role"], "content": r["content"], "created_at": r["created_at"].strftime("%H:%M")} for r in rows]
    except Exception as e:
        print(f"DB get error: {e}")
        return []

def db_get_sessions():
    """Lista sesiones únicas con su primer mensaje"""
    conn = get_db()
    if not conn: return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT ON (session_id)
                session_id,
                content as first_msg,
                created_at
            FROM chat_sessions
            WHERE role = 'user'
            ORDER BY session_id, created_at ASC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"session_id": r["session_id"],
                 "preview": r["first_msg"][:60] + "..." if len(r["first_msg"]) > 60 else r["first_msg"],
                 "date": r["created_at"].strftime("%d/%m %H:%M")} for r in rows]
    except Exception as e:
        print(f"DB sessions error: {e}")
        return []

# ── PORTFOLIO STORAGE ─────────────────────────────────────
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

# ── ALPHA VANTAGE ─────────────────────────────────────────
def get_quote(symbol):
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        q = r.json().get("Global Quote", {})
        if not q or not q.get("05. price"): return None
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
    except: return None

def get_rsi(symbol):
    try:
        url = f"https://www.alphavantage.co/query?function=RSI&symbol={symbol}&interval=daily&time_period=14&series_type=close&apikey={AV_KEY}"
        r = requests.get(url, timeout=10)
        vals = r.json().get("Technical Analysis: RSI", {})
        if not vals: return None
        latest = sorted(vals.keys())[-1]
        return round(float(vals[latest]["RSI"]), 2)
    except: return None

def get_crypto():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,cardano,polkadot&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
        return requests.get(url, timeout=10).json()
    except: return {}

def get_news(tickers="AAPL,GOOGL,SPY"):
    try:
        url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={tickers}&sort=LATEST&limit=15&apikey={AV_KEY}"
        feed = requests.get(url, timeout=10).json().get("feed", [])
        return [{"title": n.get("title",""), "summary": n.get("summary","")[:250],
                 "source": n.get("source",""), "sentiment": n.get("overall_sentiment_label","Neutral"),
                 "time": n.get("time_published","")[:8], "type": "financial"} for n in feed[:10]]
    except: return []

def get_global_news():
    if not NEWS_API_KEY: return []
    try:
        queries = ["Federal Reserve interest rates economy", "geopolitical conflict oil markets", "inflation GDP economic data"]
        all_news = []
        for q in queries:
            url = (f"https://newsapi.org/v2/everything?q={requests.utils.quote(q)}"
                   f"&language=en&sortBy=publishedAt&pageSize=3&apiKey={NEWS_API_KEY}")
            articles = requests.get(url, timeout=10).json().get("articles", [])
            for a in articles:
                if a.get("title") and "[Removed]" not in a.get("title",""):
                    all_news.append({"title": a.get("title",""), "summary": (a.get("description") or "")[:250],
                                     "source": a.get("source",{}).get("name",""), "sentiment": "Global",
                                     "time": a.get("publishedAt","")[:10], "type": "global"})
        seen = set()
        return [n for n in all_news if n["title"] not in seen and not seen.add(n["title"])][:12]
    except Exception as e:
        print(f"NewsAPI error: {e}"); return []

def get_fear_greed():
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8).json().get("data", [{}])[0]
        return {"value": int(d.get("value", 50)), "label": d.get("value_classification", "Neutral")}
    except: return None

def search_symbol(query):
    try:
        url = f"https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords={query}&apikey={AV_KEY}"
        matches = requests.get(url, timeout=10).json().get("bestMatches", [])
        return [{"symbol": m.get("1. symbol",""), "name": m.get("2. name",""),
                 "type": m.get("3. type",""), "region": m.get("4. region","")} for m in matches[:8]]
    except: return []

# ── CLAUDE ────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres APEX — superagente financiero de élite, especialista en bolsa de valores con visión global. Combinas análisis técnico, fundamental, macroeconómico y geopolítico para dar recomendaciones con el mínimo margen de error posible.

## FILOSOFÍA DE ANÁLISIS
Cada respuesta cruza TODAS las variables que pueden afectar el precio de una acción:
**Técnicas**: RSI, MACD, medias móviles (50/200), volumen, soportes, resistencias, patrones de velas
**Fundamentales**: P/E ratio, earnings, revenue growth, deuda, márgenes, guidance
**Macroeconómicas**: tasas de interés Fed, inflación (CPI/PPI), empleo (NFP), PIB, ciclo económico
**Geopolíticas**: conflictos armados, sanciones, relaciones comerciales, estabilidad política
**Sectoriales**: precio del petróleo (energía), semiconductores (tech), tasas (financieras), dólar
**Sentimiento**: Fear & Greed Index, flujos institucionales, short interest
**Noticias globales**: cualquier evento mundial que pueda crear volatilidad

## SUB-AGENTES
**📈 Técnico** | **📰 Noticias Financieras** | **🌍 Noticias Globales** | **💼 Portafolio** | **🔍 Oportunidades** | **⚠️ Riesgo**

## PORTAFOLIO DE LEDY ($350 USD en Hapi)
QQQM 15% | VT 45% | GOOGL 15% | CVX 15% | IAU 10%

## FORMATO
**🧠 Agentes Activados** | **📊 Datos Tiempo Real** | **🌍 Contexto Mundial** | **📰 Contexto Financiero**
**📈 Análisis Técnico** | **🎯 Señal**: COMPRAR🟢/VENDER🔴/MANTENER🟡/ESPERAR⏳
**💰 Acción Recomendada** (monto exacto) | **📅 Horizonte** | **⚠️ Stop Loss** | **🔮 Proyección 30/90/180 días**

Idioma: siempre español. Sé específico con números reales. Actúa como el mejor analista financiero del mundo."""

def claude_chat(messages, market_context=""):
    if not ANTHROPIC_KEY:
        return "Error: ANTHROPIC_API_KEY no configurada."
    system = SYSTEM_PROMPT
    if market_context:
        system += f"\n\n## DATOS EN TIEMPO REAL\n{market_context}"
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
    if not EMAIL_FROM or not EMAIL_PASS: return False
    recipients = [e for e in [EMAIL_1, EMAIL_2] if e]
    if not recipients: return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"APEX Financial ⚡ <{EMAIL_FROM}>"
        msg["To"]      = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}"); return False

def make_email_html(subject, analysis, urgent=False):
    color = "#ff4444" if urgent else "#00d4aa"
    icon  = "🚨" if urgent else "⚡"
    return f"""<!DOCTYPE html><html><body style="background:#020b18;color:#ddf0f8;font-family:'Segoe UI',sans-serif;padding:24px;">
<div style="max-width:600px;margin:0 auto;">
  <div style="background:linear-gradient(135deg,{color}22,#041428);border:1px solid {color}44;border-radius:16px;padding:24px;margin-bottom:20px;">
    <h1 style="color:{color};margin:0 0 4px;">{icon} APEX Financial</h1>
    <p style="color:#4a9ebb;margin:0;font-size:12px;">{datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
  </div>
  <div style="background:rgba(255,255,255,.04);border:1px solid rgba(0,212,170,.15);border-radius:12px;padding:20px;white-space:pre-wrap;font-size:14px;line-height:1.7;">{analysis}</div>
  <p style="color:#2d5a6e;font-size:11px;text-align:center;margin-top:16px;">⚠️ Análisis orientativo — Las decisiones de inversión son tuyas</p>
</div></body></html>"""

# ── SCHEDULED JOB ─────────────────────────────────────────
def scheduled_analysis():
    print(f"[{datetime.now()}] Análisis programado iniciado...")
    portfolio = load_portfolio()
    symbols = [p["symbol"] for p in portfolio]
    quotes = []
    urgent_sells = []
    for p in portfolio:
        q = get_quote(p["symbol"])
        rsi = get_rsi(p["symbol"])
        if q:
            q["rsi"] = rsi; q["amount"] = p.get("amount",0)
            quotes.append(q)
            pct = float(q.get("change_pct",0))
            if (rsi and rsi >= 72) or pct <= -3:
                urgent_sells.append({"symbol":p["symbol"],"rsi":rsi,"change_pct":pct,"price":q["price"]})

    crypto      = get_crypto()
    news        = get_news(",".join(symbols))
    global_news = get_global_news()
    fear_greed  = get_fear_greed()

    ctx = "PRECIOS ACTUALES:\n"
    for q in quotes:
        ctx += f"- {q['symbol']}: ${q['price']:.2f} ({float(q['change_pct']):+.2f}% hoy)"
        if q.get("rsi"): ctx += f" | RSI:{q['rsi']}"
        ctx += "\n"
    if fear_greed:
        ctx += f"\nFEAR & GREED: {fear_greed['value']}/100 — {fear_greed['label']}\n"
    if news:
        ctx += "\nNOTICIAS FINANCIERAS:\n" + "".join(f"- [{n['sentiment']}] {n['title']}\n" for n in news[:5])
    if global_news:
        ctx += "\nNOTICIAS MUNDIALES:\n" + "".join(f"- [{n['source']}] {n['title']}\n" for n in global_news[:5])

    if urgent_sells:
        analysis = claude_chat([{"role":"user","content":f"ALERTA URGENTE señales de venta: {urgent_sells}. Analiza y da recomendación inmediata."}], ctx)
        send_email("🚨 APEX ALERTA — Señal de venta urgente", make_email_html("Alerta Urgente", analysis, urgent=True))

    total = sum(p.get("amount",0) for p in portfolio)
    analysis_full = claude_chat([{"role":"user","content":f"Resumen financiero completo del portafolio de ${total:.0f} USD. Estado de cada posición, señales técnicas, impacto de noticias, 2-3 oportunidades adicionales para investigar."}], ctx)
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
    data      = request.json
    symbol    = data.get("symbol","").upper().strip()
    name      = data.get("name", symbol)
    amount    = float(data.get("amount", 0))
    avg_price = float(data.get("avg_price", 0))
    owner     = data.get("owner", "ledy")
    if not symbol or amount <= 0:
        return jsonify({"error": "Símbolo y monto requeridos"}), 400
    portfolio = load_portfolio()
    for p in portfolio:
        if p["symbol"] == symbol and p.get("owner","ledy") == owner:
            p["amount"]    = round(p["amount"] + amount, 2)
            p["avg_price"] = avg_price if avg_price > 0 else p["avg_price"]
            save_portfolio(portfolio)
            return jsonify({"ok": True, "message": f"{symbol} actualizado"})
    portfolio.append({"symbol":symbol,"name":name,"shares":0,"avg_price":avg_price,
                       "amount":amount,"allocation":0,"owner":owner})
    # Recalculate allocations per owner
    for o in ["ledy","yorguin"]:
        owner_p = [p for p in portfolio if p.get("owner","ledy")==o]
        total = sum(p["amount"] for p in owner_p)
        for p in owner_p:
            p["allocation"] = round((p["amount"]/total)*100,1) if total>0 else 0
    save_portfolio(portfolio)
    return jsonify({"ok": True, "message": f"{symbol} agregado al portafolio de {owner.capitalize()}"})

@app.route("/api/portfolio/remove", methods=["POST"])
def remove_investment():
    symbol = request.json.get("symbol","").upper()
    owner  = request.json.get("owner","ledy")
    portfolio = [p for p in load_portfolio() if not(p["symbol"]==symbol and p.get("owner","ledy")==owner)]
    for o in ["ledy","yorguin"]:
        owner_p = [p for p in portfolio if p.get("owner","ledy")==o]
        total = sum(p["amount"] for p in owner_p)
        for p in owner_p:
            p["allocation"] = round((p["amount"]/total)*100,1) if total>0 else 0
    save_portfolio(portfolio)
    return jsonify({"ok": True})

@app.route("/api/market-data")
def market_data():
    portfolio = load_portfolio()
    quotes = []
    for p in portfolio:
        q = get_quote(p["symbol"])
        if q:
            q["rsi"] = get_rsi(p["symbol"])
            q["amount"] = p.get("amount",0)
            q["allocation"] = p.get("allocation",0)
            q["avg_price"]  = p.get("avg_price",0)
        else:
            q = {"symbol":p["symbol"],"error":True,"amount":p.get("amount",0)}
        quotes.append(q)
    tickers = ",".join(p["symbol"] for p in portfolio)
    return jsonify({"quotes":quotes,"crypto":get_crypto(),"news":get_news(tickers),
                    "global_news":get_global_news(),"fear_greed":get_fear_greed(),
                    "status":"ok","updated":datetime.now().strftime("%H:%M:%S")})

@app.route("/api/search")
def search():
    q = request.args.get("q","")
    if not q: return jsonify([])
    results = search_symbol(q)
    for r in results[:3]:
        qd = get_quote(r["symbol"])
        if qd:
            r["price"] = qd["price"]
            r["change_pct"] = qd["change_pct"]
    return jsonify(results)

@app.route("/api/chat", methods=["POST"])
def chat():
    body       = request.json
    msgs       = body.get("messages", [])
    context    = body.get("market_context", "")
    session_id = body.get("session_id", "default")
    user_msg   = msgs[-1]["content"] if msgs else ""

    # Guardar mensaje del usuario
    db_save_message(session_id, "user", user_msg)

    response = claude_chat(msgs, context)

    # Guardar respuesta del asistente
    db_save_message(session_id, "assistant", response)

    return jsonify({"response": response})

@app.route("/api/history/<session_id>")
def get_history(session_id):
    return jsonify(db_get_history(session_id))

@app.route("/api/sessions")
def get_sessions():
    return jsonify(db_get_sessions())

@app.route("/api/opportunities", methods=["POST"])
def opportunities():
    body    = request.json
    horizon = body.get("horizon","both")
    risk    = body.get("risk","medium")
    amount  = body.get("amount",100)
    context = body.get("market_context","")
    ht = {"short":"corto plazo (1-3 meses)","long":"largo plazo (6-24 meses)","both":"corto Y largo plazo"}.get(horizon,"ambos")
    rt = {"low":"conservador","medium":"moderado","high":"agresivo"}.get(risk,"moderado")
    prompt = f"""Analiza el mercado actual y dame las MEJORES 5-8 oportunidades de inversión para {ht} con perfil {rt} y ${amount} USD disponibles.
Para cada oportunidad: símbolo, por qué AHORA, precio entrada ideal, take profit, stop loss, % ganancia esperado, nivel de riesgo 1-5, horizonte."""
    return jsonify({"response": claude_chat([{"role":"user","content":prompt}], context)})

@app.route("/api/trigger-analysis", methods=["POST"])
def trigger_analysis():
    threading.Thread(target=scheduled_analysis).start()
    return jsonify({"ok":True,"message":"Análisis iniciado, recibirás el email en unos minutos."})

# ── SCHEDULER + INIT ──────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_analysis, "interval", hours=4, id="apex_analysis")
scheduler.start()
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

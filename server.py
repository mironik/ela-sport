#!/usr/bin/env python3
"""
VPS Server - Glavni poslužitelj za Ela Sport trgovinu
1. Poslužuje statički frontend (SEO optimiziran).
2. Upravlja hibridnom bazom (SQL + Vektori).
3. Prima heartbeat od Jetsona i prati njegovu IP/status.
4. Kešira LLM odgovore i routa upite prema Jetsonu.
5. Obrađuje narudžbe (privatna/poslovna lica).
"""

import os
import json
import time
import sqlite3
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from flask_cors import CORS
import requests

# Konfiguracija
app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)  # Omogući CORS za komunikaciju s frontendom

DB_PATH = 'products.db'
VECTORS_PATH = 'vectors.json'
LOG_FILE = 'vps_server.log'

# Postavi logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- STANJE SUSTAVA ---
# Rječnik za praćenje aktivnih Jetson uređaja
# Struktura: { "device_id": {"ip": "...", "port": 8000, "last_seen": datetime, "status": "online"} }
jetson_registry = {}

# LRU Cache za LLM odgovore (jednostavna implementacija u memoriji)
# Ključ: hash(upit + jezik), Vrijednost: {odgovor, timestamp}
llm_cache = {}
CACHE_TTL_SECONDS = 3600  # Keširaj odgovore 1 sat

def get_db_connection():
    """Kreira vezu na SQLite bazu."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def detect_language(text):
    """Jednostavna detekcija jezika (može se proširiti s langdetect bibliotekom)."""
    # Ovdje bi išla integracija s bibliotekom poput `langdetect` ili `fasttext`
    # Za sada vraćamo 'hr' kao default ili pokušavamo detektirati po znakovima
    cyrillic_chars = set('абвгдежзийклмнопрстуфхцчшщъыьэюя')
    if any(c in text.lower() for c in cyrillic_chars):
        return 'sr' # ili mk/bg ovisno o kontekstu
    # Dodati više logike za druge jezike
    return 'hr' # Default

def get_cached_response(query_hash):
    """Dohvaća odgovor iz cachea ako postoji i nije istekao."""
    if query_hash in llm_cache:
        entry = llm_cache[query_hash]
        if time.time() - entry['timestamp'] < CACHE_TTL_SECONDS:
            logger.info(f"CACHE HIT za upit: {query_hash[:10]}...")
            return entry['response']
        else:
            del llm_cache[query_hash] # Isteklo
    return None

def save_to_cache(query_hash, response):
    """Sprema odgovor u cache."""
    llm_cache[query_hash] = {
        'response': response,
        'timestamp': time.time()
    }
    logger.info(f"CACHE MISS - Spremljen novi odgovor za: {query_hash[:10]}...")

def get_active_jetson():
    """Pronalazi aktivni Jetson uređaj."""
    now = datetime.now()
    for device_id, info in jetson_registry.items():
        # Ako je viđen u zadnjih 60 sekundi, smatramo ga aktivnim
        if (now - info['last_seen']).total_seconds() < 60:
            return info
    return None

# --- API ENDPOINTS ---

@app.route('/api/jetson/heartbeat', methods=['POST'])
def receive_heartbeat():
    """Prima heartbeat signal od Jetson agenta."""
    data = request.json
    device_id = data.get('device_id')
    public_ip = data.get('public_ip')
    api_port = data.get('api_port')
    
    if not device_id or not public_ip:
        return jsonify({"error": "Missing device_id or public_ip"}), 400
    
    # Ažuriraj registry
    jetson_registry[device_id] = {
        'public_ip': public_ip,
        'api_port': api_port,
        'last_seen': datetime.now(),
        'status': 'online'
    }
    
    logger.info(f"Heartbeat primljen od {device_id} ({public_ip}:{api_port})")
    return jsonify({"status": "ok", "message": "Heartbeat received"})

@app.route('/api/chat', methods=['POST'])
def chat_endpoint():
    """Glavni endpoint za chat s AI trgovcem."""
    data = request.json
    user_query = data.get('query', '')
    user_lang = data.get('language', None) # Ako frontend već detektira
    
    if not user_query:
        return jsonify({"error": "Query is required"}), 400

    # 1. Detekcija jezika ako nije proslijeđena
    if not user_lang:
        user_lang = detect_language(user_query)
    
    # 2. Provjera cachea (ključ = upit + jezik)
    cache_key = f"{user_query}_{user_lang}"
    cached_resp = get_cached_response(cache_key)
    if cached_resp:
        return jsonify({
            "response": cached_resp,
            "source": "cache",
            "language": user_lang
        })

    # 3. Pronađi aktivni Jetson
    jetson = get_active_jetson()
    
    if not jetson:
        # Fallback logika ako Jetson nije dostupan
        logger.warning("Jetson nije dostupan. Vraćam fallback odgovor.")
        fallback_response = f"Trenutno naš AI asistent ({user_lang}) nije dostupan zbog održavanja sustava. Molimo pokušajte kasnije ili pregledajte našu ponudu."
        # Možemo spremiti i fallback u cache na kraće vrijeme
        save_to_cache(cache_key, fallback_response)
        return jsonify({
            "response": fallback_response,
            "source": "fallback",
            "language": user_lang
        })

    # 4. Proslijedi upit na Jetson kroz SSH Tunnel (ili direktno na IP ako je potrebno)
    # Napomena: Ako SSH tunnel radi, VPS spaja na localhost:REMOTE_PORT (npr. 8000)
    # Ako koristimo dinamičku IP direktno (manje sigurno ali moguće), koristili bismo jetson['public_ip']
    
    try:
        # Pretpostavka: SSH tunnel je uspostavljen na port 8000 na VPS-u (localhost)
        # Ako bi htjeli ići direktno na IP: url = f"http://{jetson['public_ip']}:{jetson['api_port']}/generate"
        local_tunnel_url = f"http://127.0.0.1:8000/generate" 
        
        payload = {
            "query": user_query,
            "language": user_lang,
            "context": "trgovina_sportskom_opremom"
        }
        
        resp = requests.post(local_tunnel_url, json=payload, timeout=15)
        resp.raise_for_status()
        ai_response = resp.json().get('response', '')
        
        # 5. Spremi u cache
        save_to_cache(cache_key, ai_response)
        
        return jsonify({
            "response": ai_response,
            "source": "jetson",
            "language": user_lang
        })
        
    except Exception as e:
        logger.error(f"Greška pri komunikaciji s Jetsonom: {e}")
        error_response = "Došlo je do tehničke pogreške pri povezivanju s asistentom."
        return jsonify({
            "response": error_response,
            "source": "error",
            "language": user_lang
        }), 503

@app.route('/api/products/search', methods=['GET'])
def search_products():
    """Pretraživanje proizvoda (SQL + Vektorska hibridna pretraga)."""
    query = request.args.get('q', '')
    category = request.args.get('category', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Osnovna SQL pretraga
    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    
    if query:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
    
    if category:
        sql += " AND category = ?"
        params.append(category)
        
    sql += " LIMIT 20"
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    
    products = [dict(row) for row in rows]
    
    # OVDJE BI IŠLA LOGIKA ZA VEKTORSKU PRETRAGU AKO SQL VRATI MALO REZULTATA
    # 1. Generiraj vektor upita (lokalno mali model ili poziv Jetsonu)
    # 2. Traži najbliže susjede u vectors.json / FAISS indexu
    # 3. Spoji rezultate
    
    return jsonify(products)

@app.route('/api/orders', methods=['POST'])
def create_order():
    """Kreiranje nove narudžbe."""
    data = request.json
    # Validacija podataka (pojednostavljeno)
    required_fields = ['customer_type', 'customer_data', 'items']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Ubaci narudžbu
        cursor.execute("""
            INSERT INTO orders (customer_type, customer_json, items_json, total_amount, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (
            data['customer_type'],
            json.dumps(data['customer_data']),
            json.dumps(data['items']),
            data.get('total_amount', 0.0),
            datetime.now()
        ))
        order_id = cursor.lastrowid
        conn.commit()
        
        # Ovdje dodati slanje emaila trgovcu/kupcu
        
        return jsonify({"success": True, "order_id": order_id}), 201
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Greška pri kreiranju narudžbe: {e}")
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()

# --- SERVINg FRONTENDA ---

@app.route('/')
def serve_index():
    """Poslužuje glavnu HTML stranicu."""
    # U produkciji, ovo bi bio statički file iz 'static' mape
    # ili generirani HTML iz SEO procesa
    return send_from_directory('.', 'index.html')

@app.route('/product/<int:product_id>')
def serve_product_seo(product_id):
    """
    SEO Endpoint: Generira statički HTML za specifičan proizvod.
    Ovo vide samo Google botovi (ili se renderira SSR), 
    a korisnicima se može poslužiti isti sadržaj ili SPA.
    """
    conn = get_db_connection()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    
    if not product:
        return "Proizvod nije pronađen", 404
        
    # Generiraj HTML s meta tagovima za SEO
    html_content = f"""
    <!DOCTYPE html>
    <html lang="hr">
    <head>
        <meta charset="UTF-8">
        <title>{product['name']} - Ela Sport</title>
        <meta name="description" content="{product['description'][:160]}">
        <meta property="og:title" content="{product['name']}">
        <meta property="og:image" content="/images/{product['image']}">
        <!-- Ostali SEO tagovi -->
    </head>
    <body>
        <!-- Sadržaj proizvoda vidljiv i botovima i ljudima -->
        <h1>{product['name']}</h1>
        <p>Cijena: {product['price']} EUR</p>
        <p>{product['description']}</p>
        <script>
            // Ovdje se učitava React/Vue/JS aplikacija za interaktivnost
            window.PRODUCT_DATA = {json.dumps(dict(product))};
        </script>
    </body>
    </html>
    """
    return html_content

if __name__ == '__main__':
    logger.info("Pokretanje VPS Servera...")
    # Pokreni server na svim interfejsima, port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)

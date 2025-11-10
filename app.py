from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from datetime import datetime
import requests
import os
from functools import wraps
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', 'admin123')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# Detect database type
USE_POSTGRES = bool(DATABASE_URL and 'postgres' in DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    print("📊 Using PostgreSQL database")
else:
    import sqlite3
    DATABASE_NAME = "balance_monitor.db"
    print("📊 Using SQLite database")

# ==================== DATABASE CONNECTION ====================
def get_db_connection():
    """Get database connection based on environment"""
    if USE_POSTGRES:
        # Fix for Railway PostgreSQL connection string
        url = DATABASE_URL.replace('postgres://', 'postgresql://')
        return psycopg2.connect(url)
    else:
        return sqlite3.connect(DATABASE_NAME)

# ==================== DATABASE SETUP ====================
def init_database():
    """Initialize database with proper schema"""
    try:
        if USE_POSTGRES:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS balance_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    account_label VARCHAR(255) NOT NULL,
                    account_number VARCHAR(50) NOT NULL,
                    balance DECIMAL(15, 2) NOT NULL,
                    event_type VARCHAR(50),
                    broker VARCHAR(255),
                    currency VARCHAR(10),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create index for faster queries
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_account_label 
                ON balance_history(account_label, account_number, created_at DESC)
            ''')
            
            conn.commit()
            cursor.close()
            conn.close()
            print("✓ PostgreSQL database initialized successfully")
        else:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    account_label TEXT NOT NULL,
                    account_number TEXT NOT NULL,
                    balance REAL NOT NULL,
                    event_type TEXT,
                    broker TEXT,
                    currency TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            print("✓ SQLite database initialized successfully")
            
    except Exception as e:
        print(f"✗ Database initialization error: {str(e)}")

# ==================== AUTHENTICATION ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== HELPER FUNCTIONS ====================
def get_previous_balance(account_label, account_number):
    """Get the most recent balance for an account from database"""
    try:
        conn = get_db_connection()
        
        if USE_POSTGRES:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('''
                SELECT balance 
                FROM balance_history 
                WHERE account_label = %s AND account_number = %s
                ORDER BY created_at DESC
                LIMIT 1
            ''', (account_label, account_number))
        else:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT balance 
                FROM balance_history 
                WHERE account_label = ? AND account_number = ?
                ORDER BY created_at DESC
                LIMIT 1
            ''', (account_label, account_number))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if USE_POSTGRES:
            return result['balance'] if result else None
        else:
            return result[0] if result else None
        
    except Exception as e:
        print(f"✗ Error getting previous balance: {str(e)}")
        return None

# ==================== TELEGRAM MODULE ====================
def send_to_telegram(payload, previous_balance=None):
    """Send balance update notification to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram not configured - skipping notification")
        return False
        
    try:
        account_label = payload.get('account_label', 'Unknown')
        new_balance = payload.get('new_balance', 0.0)
        
        # Format message: Account label // previous --> current
        if previous_balance is not None:
            message = f"{account_label} // {previous_balance:,.2f} --> {new_balance:,.2f}"
        else:
            message = f"{account_label} // {new_balance:,.2f}"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        
        response = requests.post(url, json=data, timeout=10)
        
        if response.status_code == 200:
            print("✓ Telegram notification sent successfully")
            return True
        else:
            print(f"✗ Telegram error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"✗ Telegram exception: {str(e)}")
        return False

# ==================== DATABASE MODULE ====================
def log_to_database(payload):
    """Log balance update to database"""
    try:
        # Ensure database exists
        init_database()
        
        conn = get_db_connection()
        
        if USE_POSTGRES:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO balance_history 
                (timestamp, account_label, account_number, balance, event_type, broker, currency)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                payload.get('timestamp'),
                payload.get('account_label'),
                payload.get('account_number'),
                payload.get('new_balance'),
                payload.get('event_type'),
                payload.get('broker'),
                payload.get('currency')
            ))
        else:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO balance_history 
                (timestamp, account_label, account_number, balance, event_type, broker, currency)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                payload.get('timestamp'),
                payload.get('account_label'),
                payload.get('account_number'),
                payload.get('new_balance'),
                payload.get('event_type'),
                payload.get('broker'),
                payload.get('currency')
            ))
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✓ Data logged to database successfully")
        return True
        
    except Exception as e:
        print(f"✗ Database exception: {str(e)}")
        return False

# ==================== API ENDPOINT ====================
@app.route('/api/balance_update', methods=['POST'])
def balance_update():
    """Main API endpoint to receive balance updates from MT5 EA"""
    try:
        payload = request.get_json()
        
        if not payload:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400
        
        print("\n" + "="*60)
        print("📥 NEW BALANCE UPDATE RECEIVED")
        print("="*60)
        print(f"Account: {payload.get('account_label')} ({payload.get('account_number')})")
        print(f"Balance: {payload.get('new_balance')}")
        print(f"Event: {payload.get('event_type')}")
        print(f"Time: {payload.get('timestamp')}")
        print("="*60 + "\n")
        
        # Get previous balance from database
        previous_balance = get_previous_balance(
            payload.get('account_label'),
            payload.get('account_number')
        )
        
        # Send to Telegram with previous balance
        telegram_success = send_to_telegram(payload, previous_balance)
        
        # Log to Database
        db_success = log_to_database(payload)
        
        return jsonify({
            "status": "success",
            "telegram_sent": telegram_success,
            "database_logged": db_success
        }), 200
        
    except Exception as e:
        print(f"✗ API error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== WEB DASHBOARD ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == DASHBOARD_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout"""
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/accounts')
@login_required
def get_accounts():
    """Get list of all accounts"""
    try:
        # Ensure database exists
        init_database()
        
        conn = get_db_connection()
        
        if USE_POSTGRES:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('''
                SELECT DISTINCT account_label, account_number 
                FROM balance_history 
                ORDER BY account_label
            ''')
            accounts = [{"label": row['account_label'], "number": row['account_number']} for row in cursor.fetchall()]
        else:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT account_label, account_number 
                FROM balance_history 
                ORDER BY account_label
            ''')
            accounts = [{"label": row[0], "number": row[1]} for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return jsonify(accounts)
    except Exception as e:
        print(f"✗ Error getting accounts: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/history')
@login_required
def get_history():
    """Get balance history with optional filters"""
    try:
        # Ensure database exists
        init_database()
        
        account_labels = request.args.get('accounts', '').split(',')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        conn = get_db_connection()
        
        if USE_POSTGRES:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            query = "SELECT timestamp, account_label, account_number, balance, event_type, broker, currency FROM balance_history WHERE 1=1"
            params = []
            
            if account_labels and account_labels[0]:
                placeholders = ','.join(['%s'] * len(account_labels))
                query += f" AND account_label IN ({placeholders})"
                params.extend(account_labels)
            
            if start_date:
                query += " AND timestamp >= %s"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= %s"
                params.append(end_date + " 23:59:59")
            
            query += " ORDER BY timestamp ASC"
            cursor.execute(query, params)
            
            history = [dict(row) for row in cursor.fetchall()]
            
        else:
            cursor = conn.cursor()
            query = "SELECT timestamp, account_label, account_number, balance, event_type, broker, currency FROM balance_history WHERE 1=1"
            params = []
            
            if account_labels and account_labels[0]:
                placeholders = ','.join(['?'] * len(account_labels))
                query += f" AND account_label IN ({placeholders})"
                params.extend(account_labels)
            
            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date + " 23:59:59")
            
            query += " ORDER BY timestamp ASC"
            cursor.execute(query, params)
            
            history = []
            for row in cursor.fetchall():
                history.append({
                    "timestamp": row[0],
                    "account_label": row[1],
                    "account_number": row[2],
                    "balance": row[3],
                    "event_type": row[4],
                    "broker": row[5],
                    "currency": row[6]
                })
        
        cursor.close()
        conn.close()
        return jsonify(history)
        
    except Exception as e:
        print(f"✗ Error getting history: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ==================== HEALTH CHECK ====================
@app.route('/health')
def health():
    """Health check endpoint"""
    db_type = "PostgreSQL" if USE_POSTGRES else "SQLite"
    return jsonify({
        "status": "healthy", 
        "service": "balance-monitor",
        "database": db_type
    }), 200

# ==================== MAIN ====================
if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 Centralized MT5 Balance Monitoring System")
    print("="*60)
    
    init_database()
    
    print("\n⚙️  Configuration:")
    print(f"   Database: {'PostgreSQL ✓' if USE_POSTGRES else 'SQLite'}")
    print(f"   Telegram Bot: {'✓ Configured' if TELEGRAM_BOT_TOKEN else '✗ NOT CONFIGURED'}")
    print(f"   Dashboard Password: {DASHBOARD_PASSWORD}")
    
    port = int(os.environ.get('PORT', 5000))
    
    print(f"\n🌐 Server starting on port {port}")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)

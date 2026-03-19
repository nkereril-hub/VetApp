from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import psycopg2 
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import africastalking
import urllib.parse

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vetlem_isiolo_2026')

# --- AFRICA'S TALKING CONFIG ---
USERNAME = "sandbox"
API_KEY = "atsk_cc2ca89193a5c1a713cf71861bfe7c0b937e6d2e5108fcf349836210e1ba7fcf7bcc2bae"
africastalking.initialize(USERNAME, API_KEY)
sms = africastalking.SMS

# --- DATABASE ENGINE SELECTION ---
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect('vetlem_v3.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    db = get_db()
    cur = db.cursor()
    id_type = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cur.execute(f'CREATE TABLE IF NOT EXISTS users (id {id_type}, username TEXT UNIQUE, password TEXT, agrovet_name TEXT, owner_phone TEXT)')
    cur.execute(f'CREATE TABLE IF NOT EXISTS inventory (id {id_type}, user_id INTEGER, drug_name TEXT, quantity INTEGER, buying_price REAL, price REAL, withdrawal_days INTEGER)')
    cur.execute(f'CREATE TABLE IF NOT EXISTS treatments (id {id_type}, user_id INTEGER, owner_name TEXT, phone TEXT, animal_id TEXT, diagnosis TEXT, drug_name TEXT, cost REAL, buying_price_at_time REAL, payment_method TEXT, safe_date TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    db.commit()
    cur.close()
    db.close()

# --- LOGIN & SIGNUP LOGIC (THE FIX) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        db = get_db()
        cur = db.cursor()
        cur.execute('SELECT * FROM users WHERE username=%s' if DATABASE_URL else 'SELECT * FROM users WHERE username=?', (request.form['username'],))
        user = cur.fetchone()
        cur.close()
        db.close()
        
        if user and check_password_hash(user['password'], request.form['password']):
            session['user_id'] = user['id']
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password")
    return render_template('signup.html') # Reusing signup for login form

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        db = get_db()
        cur = db.cursor()
        pw = generate_password_hash(request.form['password'])
        try:
            cur.execute('INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (%s,%s,%s,%s)' if DATABASE_URL else 'INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (?,?,?,?)', (request.form['username'], pw, request.form['agrovet_name'], request.form['owner_phone']))
            db.commit()
            # Log them in immediately after signup
            cur.execute('SELECT id FROM users WHERE username=%s' if DATABASE_URL else 'SELECT id FROM users WHERE username=?', (request.form['username'],))
            user = cur.fetchone()
            session['user_id'] = user['id']
            cur.close()
            db.close()
            return redirect(url_for('index'))
        except Exception as e:
            flash("That email is already registered. Please try logging in instead.")
            return redirect(url_for('login'))
    return render_template('signup.html')

# --- MAIN DASHBOARD ---

@app.route('/')
def index():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT * FROM users WHERE id = %s' if DATABASE_URL else 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()
    
    # Check if user exists in session but not in DB (happens if DB was reset)
    if not user:
        session.clear()
        return redirect(url_for('login'))

    query = '''SELECT 
        SUM(CASE WHEN payment_method='Cash' THEN cost ELSE 0 END) as cash, 
        SUM(CASE WHEN payment_method='M-Pesa' THEN cost ELSE 0 END) as mpesa, 
        SUM(CASE WHEN payment_method='Credit' THEN cost ELSE 0 END) as total_debt,
        SUM(CASE WHEN payment_method != 'Credit' THEN (cost - buying_price_at_time) ELSE 0 END) as total_profit
        FROM treatments WHERE user_id=%s''' if DATABASE_URL else '''SELECT 
        SUM(CASE WHEN payment_method="Cash" THEN cost ELSE 0 END) as cash, 
        SUM(CASE WHEN payment_method="M-Pesa" THEN cost ELSE 0 END) as mpesa, 
        SUM(CASE WHEN payment_method="Credit" THEN cost ELSE 0 END) as total_debt,
        SUM(CASE WHEN payment_method != "Credit" THEN (cost - buying_price_at_time) ELSE 0 END) as total_profit
        FROM treatments WHERE user_id=?'''
    
    cur.execute(query, (session['user_id'],))
    stats = cur.fetchone()
    cur.execute('SELECT * FROM inventory WHERE user_id=%s AND quantity > 0' if DATABASE_URL else 'SELECT * FROM inventory WHERE user_id=? AND quantity > 0', (session['user_id'],))
    drugs = cur.fetchall()
    cur.execute('SELECT * FROM treatments WHERE user_id=%s ORDER BY id DESC LIMIT 10' if DATABASE_URL else 'SELECT * FROM treatments WHERE user_id=? ORDER BY id DESC LIMIT 10', (session['user_id'],))
    records = cur.fetchall()
    cur.close()
    db.close()
    return render_template('index.html', user=user, stats=stats, drugs=drugs, records=records)

# --- INVENTORY & OTHER ROUTES (CLEANED UP) ---

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    
    # 1. Get user info for the header
    cur.execute('SELECT * FROM users WHERE id = %s' if DATABASE_URL else 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()

    if request.method == 'POST':
        cur.execute('''INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) VALUES (%s,%s,%s,%s,%s,%s)''' if DATABASE_URL else '''INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) VALUES (?,?,?,?,?,?)''', (session['user_id'], request.form['name'], request.form['qty'], request.form['b_price'], request.form['s_price'], request.form['withdrawal']))
        db.commit()
    
    cur.execute('SELECT * FROM inventory WHERE user_id=%s' if DATABASE_URL else 'SELECT * FROM inventory WHERE user_id=?', (session['user_id'],))
    items = cur.fetchall()
    cur.close()
    db.close()
    return render_template('inventory.html', items=items, user=user)

# --- DEBTORS & SMS LOGIC ---
@app.route('/debtors')
def debtors():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s" if DATABASE_URL else "SELECT * FROM users WHERE id = ?", (session['user_id'],))
    user = cur.fetchone()
    search_query = request.args.get('search', '')
    
    if DATABASE_URL:
        base_sql = "SELECT * FROM treatments WHERE user_id = %s AND payment_method = 'Credit'"
    else:
        base_sql = "SELECT * FROM treatments WHERE user_id = ? AND payment_method = 'Credit'"
    
    if search_query:
        search_param = f"%{search_query}%"
        cur.execute(base_sql + (" AND (owner_name ILIKE %s OR animal_id ILIKE %s)" if DATABASE_URL else " AND (owner_name LIKE ? OR animal_id LIKE ?)") + " ORDER BY timestamp DESC", (session['user_id'], search_param, search_param))
    else:
        cur.execute(base_sql + " ORDER BY timestamp DESC", (session['user_id'],))
    
    records = cur.fetchall()
    cur.execute("SELECT SUM(cost) as total FROM treatments WHERE user_id = %s AND payment_method = 'Credit'" if DATABASE_URL else "SELECT SUM(cost) as total FROM treatments WHERE user_id = ? AND payment_method = 'Credit'", (session['user_id'],))
    total_row = cur.fetchone()
    total_val = (total_row['total'] if DATABASE_URL else total_row[0]) or 0
    cur.close()
    db.close()
    return render_template('debtors.html', user=user, records=records, total=total_val, search_val=search_query)

@app.route('/register_treatment', methods=['POST'])
def register_treatment():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT * FROM inventory WHERE id=%s' if DATABASE_URL else 'SELECT * FROM inventory WHERE id=?', (request.form['drug_id'],))
    drug = cur.fetchone()
    final_price = float(request.form['final_price'])
    safe_date = (datetime.now() + timedelta(days=drug['withdrawal_days'])).strftime('%d-%b-%Y')
    cur.execute('''INSERT INTO treatments (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''' if DATABASE_URL else '''INSERT INTO treatments (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) VALUES (?,?,?,?,?,?,?,?,?,?)''', (session['user_id'], request.form['owner'], request.form['phone'], request.form['animal_id'], request.form.get('diagnosis', ''), drug['drug_name'], final_price, drug['buying_price'], request.form['payment_method'], safe_date))
    cur.execute('UPDATE inventory SET quantity = quantity - 1 WHERE id=%s' if DATABASE_URL else 'UPDATE inventory SET quantity = quantity - 1 WHERE id=?', (request.form['drug_id'],))
    db.commit()
    cur.close()
    db.close()
    flash(f"Record Saved! Safe Date: {safe_date}")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context(): init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

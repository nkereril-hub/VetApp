from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import psycopg2 
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import africastalking

app = Flask(__name__)
# Use a fallback key for local dev, but Render will use the Environment Variable
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
        # CRITICAL FIX: sslmode='require' is mandatory for Render PostgreSQL
        conn = psycopg2.connect(
            DATABASE_URL, 
            sslmode='require', 
            cursor_factory=RealDictCursor
        )
        return conn
    else:
        # Local SQLite fallback
        conn = sqlite3.connect('vetlem_v3.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    try:
        db = get_db()
        cur = db.cursor()
        id_type = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
        
        cur.execute(f'CREATE TABLE IF NOT EXISTS users (id {id_type}, username TEXT UNIQUE, password TEXT, agrovet_name TEXT, owner_phone TEXT)')
        cur.execute(f'CREATE TABLE IF NOT EXISTS inventory (id {id_type}, user_id INTEGER, drug_name TEXT, quantity INTEGER, buying_price REAL, price REAL, withdrawal_days INTEGER)')
        cur.execute(f'CREATE TABLE IF NOT EXISTS treatments (id {id_type}, user_id INTEGER, owner_name TEXT, phone TEXT, animal_id TEXT, diagnosis TEXT, drug_name TEXT, cost REAL, buying_price_at_time REAL, payment_method TEXT, safe_date TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        
        db.commit()
        cur.close()
        db.close()
        print("Database initialized successfully!")
    except Exception as e:
        print(f"Database init error: {e}")

# --- ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        db = get_db()
        cur = db.cursor()
        query = 'SELECT * FROM users WHERE username=%s' if DATABASE_URL else 'SELECT * FROM users WHERE username=?'
        cur.execute(query, (request.form['username'],))
        user = cur.fetchone()
        cur.close()
        db.close()
        
        if user and check_password_hash(user['password'], request.form['password']):
            session['user_id'] = user['id']
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password")
    return render_template('signup.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        db = get_db()
        cur = db.cursor()
        pw = generate_password_hash(request.form['password'])
        try:
            sql = 'INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (%s,%s,%s,%s)' if DATABASE_URL else 'INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (?,?,?,?)'
            cur.execute(sql, (request.form['username'], pw, request.form['agrovet_name'], request.form['owner_phone']))
            db.commit()
            
            # Log in automatically
            check_sql = 'SELECT id FROM users WHERE username=%s' if DATABASE_URL else 'SELECT id FROM users WHERE username=?'
            cur.execute(check_sql, (request.form['username'],))
            user = cur.fetchone()
            session['user_id'] = user['id']
            
            cur.close()
            db.close()
            return redirect(url_for('index'))
        except Exception as e:
            flash("Registration error. Try a different username.")
            return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/')
def index():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    
    user_query = 'SELECT * FROM users WHERE id = %s' if DATABASE_URL else 'SELECT * FROM users WHERE id = ?'
    cur.execute(user_query, (session['user_id'],))
    user = cur.fetchone()
    
    if not user:
        session.clear()
        return redirect(url_for('login'))

    # Stats Query
    stats_query = '''SELECT 
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
    
    cur.execute(stats_query, (session['user_id'],))
    stats = cur.fetchone()
    
    inv_query = 'SELECT * FROM inventory WHERE user_id=%s AND quantity > 0' if DATABASE_URL else 'SELECT * FROM inventory WHERE user_id=? AND quantity > 0'
    cur.execute(inv_query, (session['user_id'],))
    drugs = cur.fetchall()
    
    rec_query = 'SELECT * FROM treatments WHERE user_id=%s ORDER BY id DESC LIMIT 10' if DATABASE_URL else 'SELECT * FROM treatments WHERE user_id=? ORDER BY id DESC LIMIT 10'
    cur.execute(rec_query, (session['user_id'],))
    records = cur.fetchall()
    
    cur.close()
    db.close()
    return render_template('index.html', user=user, stats=stats, drugs=drugs, records=records)

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    
    cur.execute('SELECT * FROM users WHERE id = %s' if DATABASE_URL else 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()

    if request.method == 'POST':
        sql = '''INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) VALUES (%s,%s,%s,%s,%s,%s)''' if DATABASE_URL else '''INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) VALUES (?,?,?,?,?,?)'''
        cur.execute(sql, (session['user_id'], request.form['name'], request.form['qty'], request.form['b_price'], request.form['s_price'], request.form['withdrawal']))
        db.commit()
    
    list_sql = 'SELECT * FROM inventory WHERE user_id=%s' if DATABASE_URL else 'SELECT * FROM inventory WHERE user_id=?'
    cur.execute(list_sql, (session['user_id'],))
    items = cur.fetchall()
    cur.close()
    db.close()
    return render_template('inventory.html', items=items, user=user)

@app.route('/register_treatment', methods=['POST'])
def register_treatment():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db()
    cur = db.cursor()
    
    cur.execute('SELECT * FROM inventory WHERE id=%s' if DATABASE_URL else 'SELECT * FROM inventory WHERE id=?', (request.form['drug_id'],))
    drug = cur.fetchone()
    
    final_price = float(request.form['final_price'])
    safe_date = (datetime.now() + timedelta(days=drug['withdrawal_days'])).strftime('%d-%b-%Y')
    
    sql = '''INSERT INTO treatments (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''' if DATABASE_URL else '''INSERT INTO treatments (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) VALUES (?,?,?,?,?,?,?,?,?,?)'''
    cur.execute(sql, (session['user_id'], request.form['owner'], request.form['phone'], request.form['animal_id'], request.form.get('diagnosis', ''), drug['drug_name'], final_price, drug['buying_price'], request.form['payment_method'], safe_date))
    
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

# --- PRODUCTION READY INIT ---
# This runs on every startup, whether Gunicorn or Local
with app.app_context():
    init_db()

if __name__ == '__main__':
    # Only for local testing
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

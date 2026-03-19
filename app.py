from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import psycopg2 
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import africastalking

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
        # Connect to Render PostgreSQL
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        # Connect to Local SQLite (for your laptop)
        conn = sqlite3.connect('vetlem_v3.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    db = get_db()
    cur = db.cursor()
    # Logic to handle ID types for both database types
    id_type = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    
    cur.execute(f'''CREATE TABLE IF NOT EXISTS users (
        id {id_type},
        username TEXT UNIQUE,
        password TEXT,
        agrovet_name TEXT,
        owner_phone TEXT
    )''')
    cur.execute(f'''CREATE TABLE IF NOT EXISTS inventory (
        id {id_type},
        user_id INTEGER,
        drug_name TEXT,
        quantity INTEGER,
        buying_price REAL,
        price REAL,
        withdrawal_days INTEGER
    )''')
    cur.execute(f'''CREATE TABLE IF NOT EXISTS treatments (
        id {id_type},
        user_id INTEGER,
        owner_name TEXT,
        phone TEXT,
        animal_id TEXT,
        diagnosis TEXT,
        drug_name TEXT,
        cost REAL,
        buying_price_at_time REAL,
        payment_method TEXT,
        safe_date TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.commit()
    cur.close()
    db.close()

# --- ROUTES ---

@app.route('/')
def index():
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT * FROM users WHERE id = %s' if DATABASE_URL else 'SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()
    
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

@app.route('/debtors')
def debtors():
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    cur = db.cursor()
    search_query = request.args.get('search', '')
    
    base_sql = 'SELECT * FROM treatments WHERE user_id = %s AND payment_method = %s' if DATABASE_URL else 'SELECT * FROM treatments WHERE user_id = ? AND payment_method = "Credit"'
    
    if search_query:
        if DATABASE_URL:
            cur.execute(base_sql + ' AND (owner_name ILIKE %s OR animal_id ILIKE %s) ORDER BY timestamp DESC', (session['user_id'], 'Credit', f'%{search_query}%', f'%{search_query}%'))
        else:
            cur.execute(base_sql + ' AND (owner_name LIKE ? OR animal_id LIKE ?) ORDER BY timestamp DESC', (session['user_id'], f'%{search_query}%', f'%{search_query}%'))
    else:
        cur.execute(base_sql + ' ORDER BY timestamp DESC', (session['user_id'], 'Credit') if DATABASE_URL else (session['user_id'],))
    
    records = cur.fetchall()
    cur.execute('SELECT SUM(cost) as total FROM treatments WHERE user_id = %s AND payment_method = %s' if DATABASE_URL else 'SELECT SUM(cost) as total FROM treatments WHERE user_id = ? AND payment_method = "Credit"', (session['user_id'], 'Credit') if DATABASE_URL else (session['user_id'],))
    total = cur.fetchone()
    
    cur.close()
    db.close()
    return render_template('debtors.html', records=records, total=total['total'] or 0, search_val=search_query)

@app.route('/whatsapp_reminder/<int:tid>')
def whatsapp_reminder(tid):
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT t., u.agrovet_name FROM treatments t JOIN users u ON t.user_id = u.id WHERE t.id = %s' if DATABASE_URL else 'SELECT t., u.agrovet_name FROM treatments t JOIN users u ON t.user_id = u.id WHERE t.id = ?', (tid,))
    r = cur.fetchone()
    
    if r:
        f_phone = r['phone']
        if f_phone.startswith('0'): f_phone = "254" + f_phone[1:]
        elif f_phone.startswith('+'): f_phone = f_phone[1:]
            
        message = f"Habari {r['owner_name']}, Hii ni kumbusho kutoka {r['agrovet_name']}. Una deni la KES {r['cost']} la matibabu ya {r['animal_id']}. Ahsante!"
        return redirect(f"https://wa.me/{f_phone}?text={message}")
    
    flash("Record not found")
    return redirect(url_for('debtors'))

@app.route('/register_treatment', methods=['POST'])
def register_treatment():
    db = get_db()
    cur = db.cursor()
    cur.execute('SELECT * FROM inventory WHERE id=%s' if DATABASE_URL else 'SELECT * FROM inventory WHERE id=?', (request.form['drug_id'],))
    drug = cur.fetchone()
    
    final_price = float(request.form['final_price'])
    safe_date = (datetime.now() + timedelta(days=drug['withdrawal_days'])).strftime('%d-%b-%Y')
    
    cur.execute('''INSERT INTO treatments 
        (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) 
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''' if DATABASE_URL else '''INSERT INTO treatments 
        (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) 
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (session['user_id'], request.form['owner'], request.form['phone'], 
         request.form['animal_id'], request.form.get('diagnosis', ''), drug['drug_name'], 
         final_price, drug['buying_price'], request.form['payment_method'], safe_date))
    
    cur.execute('UPDATE inventory SET quantity = quantity - 1 WHERE id=%s' if DATABASE_URL else 'UPDATE inventory SET quantity = quantity - 1 WHERE id=?', (request.form['drug_id'],))
    db.commit()
    cur.close()
    db.close()
    flash(f"Record Saved! Safe Date: {safe_date}")
    return redirect(url_for('index'))

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    cur = db.cursor()
    if request.method == 'POST':
        cur.execute('''INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) 
                    VALUES (%s,%s,%s,%s,%s,%s)''' if DATABASE_URL else '''INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) 
                    VALUES (?,?,?,?,?,?)''',
                   (session['user_id'], request.form['name'], request.form['qty'], request.form['b_price'], request.form['s_price'], request.form['withdrawal']))
        db.commit()
    
    cur.execute('SELECT * FROM inventory WHERE user_id=%s' if DATABASE_URL else 'SELECT * FROM inventory WHERE user_id=?', (session['user_id'],))
    items = cur.fetchall()
    cur.close()
    db.close()
    return render_template('inventory.html', items=items)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        db = get_db()
        cur = db.cursor()
        pw = generate_password_hash(request.form['password'])
        cur.execute('INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (%s,%s,%s,%s)' if DATABASE_URL else 'INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (?,?,?,?)', 
                   (request.form['username'], pw, request.form['agrovet_name'], request.form['owner_phone']))
        db.commit()
        cur.execute('SELECT id FROM users WHERE username=%s' if DATABASE_URL else 'SELECT id FROM users WHERE username=?', (request.form['username'],))
        user = cur.fetchone()
        session['user_id'] = user['id']
        cur.close()
        db.close()
        return redirect(url_for('index'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('signup'))

if __name__ == '__main__':
    # This creates the tables if they don't exist yet
    with app.app_context():
        init_db()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

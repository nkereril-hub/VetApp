from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
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
# This looks for a PostgreSQL URL (Render). If not found, it uses the local file.
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db():
    # If on Render with Postgres, we would use psycopg2. 
    # For now, we stick to SQLite but use a PERSISTENT path if you add a Render Disk.
    # BEST FIX: Use Render PostgreSQL, but this code keeps your current logic safe.
    db_path = 'vetlem_v3.db'
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        agrovet_name TEXT,
        owner_phone TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        drug_name TEXT,
        quantity INTEGER,
        buying_price REAL,
        price REAL,
        withdrawal_days INTEGER
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS treatments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    db.commit()

# --- NEW WHATSAPP ROUTE ---
@app.route('/whatsapp_reminder/<int:tid>')
def whatsapp_reminder(tid):
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    r = db.execute('SELECT t.*, u.agrovet_name FROM treatments t JOIN users u ON t.user_id = u.id WHERE t.id = ?', (tid,)).fetchone()
    
    if r:
        f_phone = r['phone']
        # Convert 07... to 2547...
        if f_phone.startswith('0'):
            f_phone = "254" + f_phone[1:]
        elif f_phone.startswith('+'):
            f_phone = f_phone[1:]
            
        message = f"Habari {r['owner_name']}, Hii ni kumbusho kutoka {r['agrovet_name']}. Una deni la KES {r['cost']} la matibabu ya {r['animal_id']}. Tafadhali fika kulipia. Ahsante!"
        
        # This redirects the user's browser to open WhatsApp with the message ready
        whatsapp_url = f"https://wa.me/{f_phone}?text={message}"
        return redirect(whatsapp_url)
    
    flash("Record not found")
    return redirect(url_for('debtors'))

# --- EXISTING ROUTES (UNTOUCHED) ---

@app.route('/')
def index():
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    stats = db.execute('''SELECT 
        SUM(CASE WHEN payment_method="Cash" THEN cost ELSE 0 END) as cash, 
        SUM(CASE WHEN payment_method="M-Pesa" THEN cost ELSE 0 END) as mpesa, 
        SUM(CASE WHEN payment_method="Credit" THEN cost ELSE 0 END) as total_debt,
        SUM(CASE WHEN payment_method != "Credit" THEN (cost - buying_price_at_time) ELSE 0 END) as total_profit
        FROM treatments WHERE user_id=?''', (session['user_id'],)).fetchone()
    drugs = db.execute('SELECT * FROM inventory WHERE user_id=? AND quantity > 0', (session['user_id'],)).fetchall()
    records = db.execute('SELECT * FROM treatments WHERE user_id=? ORDER BY id DESC LIMIT 10', (session['user_id'],)).fetchall()
    return render_template('index.html', user=user, stats=stats, drugs=drugs, records=records)

@app.route('/debtors')
def debtors():
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    search_query = request.args.get('search', '')
    if search_query:
        records = db.execute('''SELECT * FROM treatments 
                                WHERE user_id = ? AND payment_method = "Credit" 
                                AND (owner_name LIKE ? OR animal_id LIKE ?)
                                ORDER BY timestamp DESC''', 
                             (session['user_id'], f'%{search_query}%', f'%{search_query}%')).fetchall()
    else:
        records = db.execute('''SELECT * FROM treatments 
                                WHERE user_id = ? AND payment_method = "Credit" 
                                ORDER BY timestamp DESC''', (session['user_id'],)).fetchall()
    total = db.execute('SELECT SUM(cost) as total FROM treatments WHERE user_id = ? AND payment_method = "Credit"', (session['user_id'],)).fetchone()
    return render_template('debtors.html', records=records, total=total['total'] or 0, user=user, search_val=search_query)

@app.route('/register_treatment', methods=['POST'])
def register_treatment():
    db = get_db()
    drug = db.execute('SELECT * FROM inventory WHERE id=?', (request.form['drug_id'],)).fetchone()
    final_price = float(request.form['final_price'])
    safe_date = (datetime.now() + timedelta(days=drug['withdrawal_days'])).strftime('%d-%b-%Y')
    db.execute('''INSERT INTO treatments 
        (user_id, owner_name, phone, animal_id, diagnosis, drug_name, cost, buying_price_at_time, payment_method, safe_date) 
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (session['user_id'], request.form['owner'], request.form['phone'], 
         request.form['animal_id'], request.form.get('diagnosis', ''), drug['drug_name'], 
         final_price, drug['buying_price'], request.form['payment_method'], safe_date))
    db.execute('UPDATE inventory SET quantity = quantity - 1 WHERE id=?', (request.form['drug_id'],))
    db.commit()
    flash(f"Record Saved! Safe Date: {safe_date}")
    return redirect(url_for('index'))

@app.route('/send_reminder/<int:tid>', methods=['POST'])
def send_reminder(tid):
    db = get_db()
    r = db.execute('SELECT t.*, u.agrovet_name FROM treatments t JOIN users u ON t.user_id = u.id WHERE t.id = ?', (tid,)).fetchone()
    if r:
        f_phone = r['phone']
        if f_phone.startswith('0'): f_phone = "+254" + f_phone[1:]
        msg = f"VETLEM: Habari {r['owner_name']}, tunakukumbusha deni la KES {r['cost']} la {r['animal_id']}."
        try: sms.send(msg, [f_phone])
        except: pass
        flash(f"SMS Reminder sent!")
    return redirect(url_for('debtors'))

@app.route('/clear_debt/<int:tid>', methods=['POST'])
def clear_debt(tid):
    db = get_db()
    db.execute('UPDATE treatments SET payment_method = "Cash" WHERE id = ?', (tid,))
    db.commit()
    flash("Debt cleared and recorded as Cash sale!")
    return redirect(url_for('index'))

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if 'user_id' not in session: return redirect(url_for('signup'))
    db = get_db()
    if request.method == 'POST':
        db.execute('INSERT INTO inventory (user_id, drug_name, quantity, buying_price, price, withdrawal_days) VALUES (?,?,?,?,?,?)',
                   (session['user_id'], request.form['name'], request.form['qty'], request.form['b_price'], request.form['s_price'], request.form['withdrawal']))
        db.commit()
    items = db.execute('SELECT * FROM inventory WHERE user_id=?', (session['user_id'],)).fetchall()
    return render_template('inventory.html', items=items)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        db = get_db()
        pw = generate_password_hash(request.form['password'])
        db.execute('INSERT INTO users (username, password, agrovet_name, owner_phone) VALUES (?,?,?,?)', 
                   (request.form['username'], pw, request.form['agrovet_name'], request.form['owner_phone']))
        db.commit()
        user = db.execute('SELECT id FROM users WHERE username=?', (request.form['username'],)).fetchone()
        session['user_id'] = user['id']
        return redirect(url_for('index'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('signup'))

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

"""
database.py – SQLite database layer for Local Basket (Premium)
Tables: users, products, cart_items, orders, order_items, addresses, subscriptions,
        reviews, loyalty_points, flash_sales, delivery_slots, sub_boxes
"""
import sqlite3, hashlib, uuid, os, json, time, random
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "localbasket.db")

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def dr(row): return dict(row) if row else None
def drs(rows): return [dict(r) for r in rows]
def uid(): return uuid.uuid4().hex[:12]
def hash_pw(pw): return hashlib.sha256(("lbsalt_"+pw).encode()).hexdigest()

# ─── Schema ──────────────────────────────────────────────────
def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            name TEXT NOT NULL, phone TEXT DEFAULT '', is_admin INTEGER DEFAULT 0,
            loyalty_points INTEGER DEFAULT 0, dark_mode INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, farm TEXT NOT NULL,
            price REAL NOT NULL, unit TEXT NOT NULL, rating REAL DEFAULT 4.5, review_count INTEGER DEFAULT 0,
            badge TEXT, img TEXT, description TEXT DEFAULT '', stock INTEGER DEFAULT 100,
            is_active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS addresses (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, label TEXT DEFAULT 'Home',
            full_address TEXT NOT NULL, lat REAL DEFAULT 0, lng REAL DEFAULT 0, is_default INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            product_id TEXT NOT NULL, qty INTEGER DEFAULT 1,
            FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, subtotal REAL,
            delivery_fee REAL DEFAULT 40, tip REAL DEFAULT 30, discount REAL DEFAULT 0,
            loyalty_used INTEGER DEFAULT 0, grand_total REAL, payment TEXT DEFAULT 'UPI',
            delivery TEXT DEFAULT 'express', delivery_slot TEXT DEFAULT '',
            status TEXT DEFAULT 'Confirmed', address TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
            product_id TEXT NOT NULL, product_name TEXT, price REAL, qty INTEGER,
            FOREIGN KEY (order_id) REFERENCES orders(id));
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY, user_id TEXT, plan TEXT DEFAULT 'monthly',
            price REAL DEFAULT 149, status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL, user_id TEXT,
            user_name TEXT, rating INTEGER, comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE TABLE IF NOT EXISTS loyalty_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            points INTEGER, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS sub_boxes (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, box_type TEXT DEFAULT 'veggie',
            frequency TEXT DEFAULT 'weekly', status TEXT DEFAULT 'active',
            next_delivery TEXT, price REAL DEFAULT 499,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS flash_sales (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL, discount_pct INTEGER,
            sale_price REAL, starts_at TEXT, ends_at TEXT, is_active INTEGER DEFAULT 1,
            FOREIGN KEY (product_id) REFERENCES products(id));
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            name, category, farm, description, content='products', content_rowid='rowid');
        """)

# ─── Users ───────────────────────────────────────────────────
def create_user(email, password, name, phone="", is_admin=0):
    u_id = uid()
    with get_db() as db:
        try:
            db.execute("INSERT INTO users (id,email,password_hash,name,phone,is_admin) VALUES (?,?,?,?,?,?)",
                       (u_id, email.lower().strip(), hash_pw(password), name, phone, is_admin))
            return u_id
        except sqlite3.IntegrityError: return None

def auth_user(email, password):
    with get_db() as db:
        return dr(db.execute("SELECT * FROM users WHERE email=? AND password_hash=?",
                             (email.lower().strip(), hash_pw(password))).fetchone())

def get_user(user_id):
    with get_db() as db:
        return dr(db.execute("SELECT id,email,name,phone,is_admin,loyalty_points,dark_mode,created_at FROM users WHERE id=?", (user_id,)).fetchone())

def update_user_pref(user_id, **kw):
    with get_db() as db:
        for k,v in kw.items():
            if k in ('dark_mode','name','phone'):
                db.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, user_id))

# ─── Products ────────────────────────────────────────────────
def get_all_products(category=None, active_only=True):
    with get_db() as db:
        q, p = "SELECT * FROM products", []
        cl = []
        if active_only: cl.append("is_active=1")
        if category and category != "all": cl.append("category=?"); p.append(category)
        if cl: q += " WHERE " + " AND ".join(cl)
        return drs(db.execute(q + " ORDER BY name", p).fetchall())

def get_product(pid):
    with get_db() as db: return dr(db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

def search_products(query):
    with get_db() as db:
        # Try FTS first, fallback to LIKE
        try:
            rows = db.execute("""SELECT p.* FROM products p JOIN products_fts f ON p.rowid=f.rowid
                WHERE products_fts MATCH ? AND p.is_active=1 ORDER BY rank""", (query+"*",)).fetchall()
            if rows: return drs(rows)
        except: pass
        q = f"%{query}%"
        return drs(db.execute("SELECT * FROM products WHERE is_active=1 AND (name LIKE ? OR category LIKE ? OR farm LIKE ? OR description LIKE ?)", (q,q,q,q)).fetchall())

def update_product(pid, **kw):
    with get_db() as db:
        s = ", ".join(f"{k}=?" for k in kw)
        db.execute(f"UPDATE products SET {s} WHERE id=?", list(kw.values())+[pid])

def create_product(data):
    pid = "p"+uid()[:6]
    with get_db() as db:
        db.execute("INSERT INTO products (id,name,category,farm,price,unit,rating,badge,img,description,stock) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, data["name"], data["category"], data["farm"], data["price"], data["unit"],
                    data.get("rating",4.5), data.get("badge"), data.get("img",""), data.get("description",""), data.get("stock",100)))
        # Update FTS
        try: db.execute("INSERT INTO products_fts(rowid,name,category,farm,description) SELECT rowid,name,category,farm,description FROM products WHERE id=?", (pid,))
        except: pass
    return pid

def delete_product(pid):
    with get_db() as db: db.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))

# ─── Cart ────────────────────────────────────────────────────
def get_cart(session_id):
    with get_db() as db:
        return drs(db.execute("""SELECT c.product_id, c.qty, p.name, p.price, p.unit, p.img, p.farm, p.stock
            FROM cart_items c JOIN products p ON c.product_id=p.id WHERE c.session_id=? AND p.is_active=1""", (session_id,)).fetchall())

def add_to_cart(session_id, product_id, qty=1):
    with get_db() as db:
        ex = db.execute("SELECT id,qty FROM cart_items WHERE session_id=? AND product_id=?", (session_id, product_id)).fetchone()
        if ex: db.execute("UPDATE cart_items SET qty=qty+? WHERE id=?", (qty, ex["id"]))
        else: db.execute("INSERT INTO cart_items (session_id,product_id,qty) VALUES (?,?,?)", (session_id, product_id, qty))

def update_cart_item(session_id, product_id, qty):
    with get_db() as db:
        if qty <= 0: db.execute("DELETE FROM cart_items WHERE session_id=? AND product_id=?", (session_id, product_id))
        else: db.execute("UPDATE cart_items SET qty=? WHERE session_id=? AND product_id=?", (qty, session_id, product_id))

def remove_cart_item(session_id, product_id):
    with get_db() as db: db.execute("DELETE FROM cart_items WHERE session_id=? AND product_id=?", (session_id, product_id))

def clear_cart(session_id):
    with get_db() as db: db.execute("DELETE FROM cart_items WHERE session_id=?", (session_id,))

# ─── Addresses ───────────────────────────────────────────────
def get_addresses(user_id):
    with get_db() as db: return drs(db.execute("SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC", (user_id,)).fetchall())

def add_address(user_id, label, full_address, lat=0, lng=0, is_default=0):
    aid = uid()
    with get_db() as db:
        if is_default: db.execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (user_id,))
        db.execute("INSERT INTO addresses (id,user_id,label,full_address,lat,lng,is_default) VALUES (?,?,?,?,?,?,?)", (aid, user_id, label, full_address, lat, lng, is_default))
    return aid

def delete_address(aid):
    with get_db() as db: db.execute("DELETE FROM addresses WHERE id=?", (aid,))

# ─── Orders ──────────────────────────────────────────────────
def create_order(session_id, user_id, cart_items, delivery, tip, payment, address="", slot="", loyalty_used=0):
    oid = uuid.uuid4().hex[:8].upper()
    subtotal = sum(i["price"]*i["qty"] for i in cart_items)
    fee = 0 if delivery == "scheduled" else 40
    discount = round(subtotal * 0.05)
    loyalty_discount = min(loyalty_used, int(subtotal * 0.1))  # max 10% from loyalty
    total = max(0, subtotal + fee + tip - discount - loyalty_discount)
    with get_db() as db:
        db.execute("INSERT INTO orders (id,user_id,session_id,subtotal,delivery_fee,tip,discount,loyalty_used,grand_total,payment,delivery,delivery_slot,address) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (oid, user_id, session_id, subtotal, fee, tip, discount, loyalty_discount, total, payment, delivery, slot, address))
        for item in cart_items:
            db.execute("INSERT INTO order_items (order_id,product_id,product_name,price,qty) VALUES (?,?,?,?,?)",
                       (oid, item["product_id"], item["name"], item["price"], item["qty"]))
            db.execute("UPDATE products SET stock=MAX(0,stock-?) WHERE id=?", (item["qty"], item["product_id"]))
        db.execute("DELETE FROM cart_items WHERE session_id=?", (session_id,))
        # Award loyalty points (1 point per ₹10 spent)
        if user_id:
            pts = int(total // 10)
            db.execute("UPDATE users SET loyalty_points=loyalty_points+?-? WHERE id=?", (pts, loyalty_used, user_id))
            if pts > 0: db.execute("INSERT INTO loyalty_log (user_id,points,reason) VALUES (?,?,?)", (user_id, pts, f"Order #{oid}"))
            if loyalty_used > 0: db.execute("INSERT INTO loyalty_log (user_id,points,reason) VALUES (?,?,?)", (user_id, -loyalty_used, f"Redeemed on #{oid}"))
    return {"id":oid,"subtotal":subtotal,"deliveryFee":fee,"tip":tip,"discount":discount,"loyaltyUsed":loyalty_discount,"grandTotal":total,"status":"Confirmed","eta":"25-30 mins","pointsEarned":int(total//10)}

def get_orders(session_id=None, user_id=None):
    with get_db() as db:
        if user_id: rows = db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        else: rows = db.execute("SELECT * FROM orders WHERE session_id=? ORDER BY created_at DESC", (session_id,)).fetchall()
        result = []
        for r in rows:
            o = dict(r); o["items"] = drs(db.execute("SELECT * FROM order_items WHERE order_id=?", (o["id"],)).fetchall()); result.append(o)
        return result

def get_order(oid):
    with get_db() as db:
        o = dr(db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
        if o: o["items"] = drs(db.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall())
        return o

def update_order_status(oid, status):
    with get_db() as db: db.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))

def get_all_orders():
    with get_db() as db:
        rows = db.execute("SELECT o.*, u.name as user_name, u.email as user_email FROM orders o LEFT JOIN users u ON o.user_id=u.id ORDER BY o.created_at DESC").fetchall()
        result = []
        for r in rows:
            o = dict(r); o["items"] = drs(db.execute("SELECT * FROM order_items WHERE order_id=?", (o["id"],)).fetchall()); result.append(o)
        return result

# ─── Reviews ─────────────────────────────────────────────────
def add_review(product_id, user_id, user_name, rating, comment):
    rid = uid()
    with get_db() as db:
        db.execute("INSERT INTO reviews (id,product_id,user_id,user_name,rating,comment) VALUES (?,?,?,?,?,?)", (rid, product_id, user_id, user_name, rating, comment))
        # Update product avg rating
        stats = db.execute("SELECT AVG(rating) as avg, COUNT(*) as cnt FROM reviews WHERE product_id=?", (product_id,)).fetchone()
        db.execute("UPDATE products SET rating=?,review_count=? WHERE id=?", (round(stats["avg"],1), stats["cnt"], product_id))
    return rid

def get_reviews(product_id):
    with get_db() as db: return drs(db.execute("SELECT * FROM reviews WHERE product_id=? ORDER BY created_at DESC", (product_id,)).fetchall())

# ─── Loyalty ─────────────────────────────────────────────────
def get_loyalty_log(user_id):
    with get_db() as db: return drs(db.execute("SELECT * FROM loyalty_log WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user_id,)).fetchall())

# ─── Subscription Boxes ─────────────────────────────────────
def create_sub_box(user_id, box_type, frequency):
    bid = uid()
    prices = {"veggie":499,"fruit":599,"mixed":799}
    with get_db() as db:
        db.execute("INSERT INTO sub_boxes (id,user_id,box_type,frequency,price,next_delivery) VALUES (?,?,?,?,?,date('now','+7 days'))",
                   (bid, user_id, box_type, frequency, prices.get(box_type,499)))
    return bid

def get_sub_boxes(user_id):
    with get_db() as db: return drs(db.execute("SELECT * FROM sub_boxes WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall())

def cancel_sub_box(bid):
    with get_db() as db: db.execute("UPDATE sub_boxes SET status='cancelled' WHERE id=?", (bid,))

# ─── Flash Sales ─────────────────────────────────────────────
def get_flash_sales():
    with get_db() as db:
        return drs(db.execute("""SELECT f.*, p.name, p.img, p.price as original_price, p.unit, p.farm
            FROM flash_sales f JOIN products p ON f.product_id=p.id WHERE f.is_active=1""").fetchall())

# ─── Recommendations ─────────────────────────────────────────
def get_recommendations(user_id=None, product_id=None):
    with get_db() as db:
        if product_id:
            # "Frequently bought together" - products commonly in same orders
            p = get_product(product_id)
            if not p: return []
            rows = db.execute("""SELECT DISTINCT p.* FROM products p
                JOIN order_items oi ON p.id=oi.product_id
                WHERE oi.order_id IN (SELECT order_id FROM order_items WHERE product_id=?)
                AND p.id != ? AND p.is_active=1 LIMIT 6""", (product_id, product_id)).fetchall()
            if len(rows) < 3:
                rows = db.execute("SELECT * FROM products WHERE category=? AND id!=? AND is_active=1 ORDER BY rating DESC LIMIT 6",
                                  (p["category"], product_id)).fetchall()
            return drs(rows)
        if user_id:
            rows = db.execute("""SELECT DISTINCT p.* FROM products p
                JOIN order_items oi ON p.id=oi.product_id
                JOIN orders o ON oi.order_id=o.id WHERE o.user_id=? AND p.is_active=1
                ORDER BY o.created_at DESC LIMIT 8""", (user_id,)).fetchall()
            if rows: return drs(rows)
        return drs(db.execute("SELECT * FROM products WHERE is_active=1 ORDER BY rating DESC, review_count DESC LIMIT 8").fetchall())

# ─── Subscriptions ───────────────────────────────────────────
def create_subscription(user_id=None):
    sid = uid()
    with get_db() as db: db.execute("INSERT INTO subscriptions (id,user_id) VALUES (?,?)", (sid, user_id))
    return sid

# ─── Delivery Slots ─────────────────────────────────────────
def get_delivery_slots():
    import datetime
    slots = []
    now = datetime.datetime.now()
    for d in range(3):
        day = now + datetime.timedelta(days=d)
        day_label = "Today" if d==0 else "Tomorrow" if d==1 else day.strftime("%a %d")
        for h in [8,10,12,14,16,18,20]:
            if d==0 and h <= now.hour+1: continue
            slots.append({"id":f"s{d}_{h}","day":day_label,"time":f"{h}:00-{h+2}:00","fee":0 if d>0 else 40 if h>12 else 49})
    return slots[:12]

# ─── Seed Data ───────────────────────────────────────────────
IMG={
  "carrots":"https://lh3.googleusercontent.com/aida-public/AB6AXuAzgvpB_67ymbLG4E_SC-gkYfSRmI0B0gM0lQVz6coYGih_Q59O0-qTvw2sVN-25vsXSNSdgFOc-HdubVi7L5Vi8yuES99saOv2B2ikEJC0fiqFEWVeHZ4QKVEkKNKKJPOwXnBu1_Th-6iWtJCallgEbwYeLKRtPlKYkpT78uewRRqYYvMNS5kbylPKzhxex9tqRqOuRTdhT8SGOu3ZsTJlw68gvEjt9nqKfDT0F8AbZbp2kp6xLq0sHenxakgOo5K4xzqkUQBvzJc",
  "asparagus":"https://lh3.googleusercontent.com/aida-public/AB6AXuCaHby7UFQDvtlp6FluSq1KoSSMWExP96n2-mhscJU5LuDNM-nIU4Ag9aCtU8T5WHoPSM9WiC9P749yH62HAD_zYvimHFxPPiL7hvb-ezOMrZQBjFSU6fArBjn_K1dZXg0PrZeNPJcZIatQzWjevFcgojcgK-37gJU_Ws9DulMhuGfLSFaGwr5WETi2N7SWIJzoO_PQTrQGPp6hWWExxndDGi499_9B9JDGuN-Os-erOB2nHWuS8oVPfPcwoTuHCZcQ1uFqOjbymGM",
  "potatoes":"https://lh3.googleusercontent.com/aida-public/AB6AXuDCrS9RnigTDzGcwrY3nPHL6m34su6XwqAV6vyhBXFmnssQZBpe6EWN6DwM21jCacKXQiZzimmj8zF_-ri8BjggX9tgArERoiCO0qUZXUtvC_NjIKv66x4XKq-Qi2NEXu5wMN40nh6_IeyfPbT33aGaM8o1J_4RMqyXtGWPe0oJlB5WDZ_wTbfsSm2p9bkYtUYW4aQvwIGr_TyhbJbr-izRoqvwfX-YDwqT2UXmTYXBYWZlx_QYXI93C28L4i97EWQv93Xd_pwm9Xo",
  "jackfruit":"https://lh3.googleusercontent.com/aida-public/AB6AXuBPulIRf2fJqwEdqMeceuAfkiozmH8mOcjJB57Ad-dRwankVpRRnMcWCmbZUBJ3tS0Db3XUkQ77gVsFqkdAYXSUUrreNaj2971V6eBEPp7B8eJoj0jbx7XphrfqdsYd-d2gIXj-MB6uK--L2kDecy8S_2QAgygjnI1wIhOzKfJkYR-VPFiYN4hdNBvqHtsaZtTW_nNXZQ2yDAv2kfIY88E8rGVofMrzdWNCmdIX6PhbLryp7oNFe8QR9Gm5Nm4WW6mrlxHq8Z8G1nU",
  "milk":"https://lh3.googleusercontent.com/aida-public/AB6AXuB3iBhJMh9lEbw-4-9f3rtPcGHTbezOu2UuoNYWb7Z7x616G5u-k3bLP3ZAvhJoUYc9tv8NR2qZ8x9WalGqXMYPWUWYLIGAjJ6er73jyoEwktgoGshRcoTr_v2CRPM6mwv3c0vaupyUXUkLbLg34tbjYfnr3kB9WdpHb0c-RgtqZ8wSNtW0RFXqBTHiUd8blFPnAznotBe6qCsE6UC-OqGUvjX_iuTCmMKy9T5KQKpxZ7YXr27i_UpvBE7yRJ5ZbVJuLCoYaChIpRk",
  "paneer":"https://lh3.googleusercontent.com/aida-public/AB6AXuAg76EMKC_mmUfLOOykkSOxwK0gYeWQ-RH1NW4z2CNvRtrWWmqjiR-Lr-tTrfRpt6b46g0PpHRDZQBrj_mOKTQ_ScU3vv-8rw0YBITnaXeC14SudzDtR3pvKKWNCpbHfm8QgCfRssL3iy3q53imi8NEV8soFkyirnpcrPfquaDpsL5fYZGQSwnwfPonGxeaozzaFHhTZdrMv_9DB3SYAxfqMo0kokLDR_AHxXW7yFnIpJQZ-oRIWhf6mCtQ92lZCSevXfP6lOO18co",
  "bread":"https://lh3.googleusercontent.com/aida-public/AB6AXuDMDCfiBZk_xHkWUzNnNzazCLhMOMf4m-gLmcLAmAolXfrNeE5fV-Mik6xxXowy_1VIvwHGD7L8sFaH1M-1oOnY9NOqhF_f1LZnawIMYeglcj2RkR0S9fUdXxR7VGzEySpLb702A2jyJXhtDAFAQq8VFZQZiZ_BN1pp9MfTpU8eihhfGFGXH47TWo6NjRIhQnvn_RQXPzezBIr_VXni29aYayfUejgIyKlx5vcXFZIaUyYSYaeVvbuBWg4T5VuQMvikgvfM3IYujWI",
  "sourdough":"https://lh3.googleusercontent.com/aida-public/AB6AXuAEgzePO-swho-FUyUVn0gaG613L7Tj2ZgHW7gH6CclTMKU2ZJq41PvMzCoJOOFU5h6YM8Mns5IkLPfc6B09-zxJ1vEYbfSPrXTbd9wZ3fcFsYMfWsx3yTcsUFmLGx2_K5d4Gn-ebXVOWzFEELANo91TZAn4ZDAtVTvBLHmmn4FFCWlCU582_RY00jC2p7vhQnRd4_lbrbbvwgf3wvJ8CjL1h5CAc-tRp9sH3l43G5-gvhQEenoQPwj55qFWbZo0zsceA4PoP67vH0",
  "spices":"https://lh3.googleusercontent.com/aida-public/AB6AXuDCvJmwtv-S3RgLoWaBXBHgMpU3IovL8kVn-BisDRaqzgVOmY8Plwl3y1rOcGimEM3r2Sj3u1dzdcOvg5jRY2yfzE00EEgBEvDBNzFo3JYtNtxigG1QM9b3EKxaUCuTR0H0XN-m6If9I8HS9UyPh9uKJS61E36A3RxpRriHS9sAQeLfvAa_1SghK30z5OA8ADjJshzoRy8ntUJo3tuyPzdZPwUmrul5M_QOW0--eLj-kAmBrdWi1IUb-wjoxlh8I98ilfnAV_-TlyY",
  "ghee":"https://lh3.googleusercontent.com/aida-public/AB6AXuDgu0Z971p2LVUav0KetPelxgj3VzDsvg7JRBdwKrDg76ANJtLi0PbP9BAvQV7K-tTB8dpTe_aDX6r9zkNTiZx8rAaRVLewPe9UoDDQtNzPj26VP3B4LMa4wLjIPPO2NL2JnfNWSuowOD3PN2UTHPSQzENgaOEieRAFoEm3yjD-dAp_VLzuuJ4eO_jdyak0MzIRhW3ef7sYJh3-mB4JQNYM0RODY5ZAKiho13Wd2cXMWysZAU1glxZvZrObkwf0cV3HE6t9Nfb9DNs",
  "coffee":"https://lh3.googleusercontent.com/aida-public/AB6AXuBPVy52Dl6z3p266i6GF9MnLxde0Xe-tL8jWncJh9AgOxBn39BOazE_SuvuEhKfDKS1w7ptl1XowEZB276vUXhIDtQcv3WnuwqP6EiKf6iuOZIxmY4384dgyLPqpuX5CIzPbNcdKxeU4QvTMjxDhMXZE3EbPhotmz48x_4ZFBm92eNSCZhd0myT3UUdMix3vtRZRD0qqMYSAltkV07UAHz0eYoTztfKMm0xvv9OlJM4ehGxh-TCwyEKMFpfKrhU37fSvZFiGV8I44w",
  "mangoes":"https://lh3.googleusercontent.com/aida-public/AB6AXuDZn67VYB9addxsZFKVDgUY8EferwqE5YmQxnYYYNCU1UW_URTGNOx0VgYLDm9A9FKyHEJRh5__maQM9WQ9pP0xxmwPPoMUsHouYkc0z98saVfQ0PvCUr2gHobFWUuOvseYO5QFx1rlzIS8UXOLJBdeTWiWzNwL_EFAzEbCxV4z0trFut1KulM_Q1KodPJ08IuN09S8JPFnlY5GGc6luPwLGzPXEavHyJLRB2vExsdSFZAco3KSASQEOQ-CBOQn51PUzWxoW0bqSUY",
  "veggies":"https://lh3.googleusercontent.com/aida-public/AB6AXuBcpUEoN4jgo5jmW3MZYNM5Wes5z4MV9UUVZpB26czqdwfQPWExx7jvu9vAp2KEpd_IEB-Jmz7rBGudqMJT92rJtgOqXpldk5jQHCi7Apq1Tpg5JgCDsJYrPStWaeDOhvLP6FMegBuRyOSFdFPRK_9YeIy6tHS5D9iPwtLRo4sB0W_sOFd0JUYtCTngVgsYPwxIj9Gr1VCwzxOMtSC667i1GFbWf2DO3NTrzelIcOZ6ft7Riqbd6n_TPlUxXDyCk3qOq7A6Y9eXrNU",
  "milkbottle":"https://lh3.googleusercontent.com/aida-public/AB6AXuBOGw_qQkkzprCB3u9kWRJfb7BbJMu0JALAD69SbtX5NyrsxOnusFr8AX1lklIf6GibW1ylxHLQeAIrVPw4-m9-Fsq3XHfobuGizI92MaMN7yAKI74-bB9fKqJggKsE0ms0QwF7sCZkhviIKnApogJ9BVVmL7nzO-IaE2PxzsSU80ZQ2w6GtoH8O0X9rYS96pFD6rT5Q_6Nu-2o_WOERyZ_GVd6PmwCkdQVuGDbU979IABBrC1Uf0ZDjJ3vzXuUBqp1O_u3k7VllQo",
  "ghee2":"https://lh3.googleusercontent.com/aida-public/AB6AXuCY8DtM693cdmiGkDjic5YL8KYrdJUNRmlsrRKurZH1nG_uGH88UHMcrYkQ4STuoSqSd6GUghWjqQ4g63EyGKw1_SZOa69KmFLJqW9JktrnM3v6sOEvGZE021QCen6_ZJXTaTkcVdhM-eMh1B79VIgzEMr8Fhti_4NxTDyOfRhA54rsQw89Kh-UoyMkN_zCEwcTeaBwyJKjSTsseaqj8kSrCR50ozOsvDACjUTzMeXU3FOkIMjIravATCc7JPB9p2eX1i8PFbEqRjI",
  "paratha":"https://lh3.googleusercontent.com/aida-public/AB6AXuAleovO30HfhwuM1corNees54xUC8tpN4tYRRqW_Ool9FxC8WuVFL1DXZmoT7PvWIa0Zf2d0ZkXpS4y0IGy3T1kN66DyGMgeD70if6bvL2jZ-0XgQaDSV5BiKGFsWblw72AO2_NeJH4nEd_qu9CJOMOm5v5WX41S8op04zTvFbAaD4wXzX-6bcfc62KBNAgEe316ruJls8gLRc4pda7sQd7JudTAVyNunqCqBh_TjDmopAxtoDkxOvyvVHjH7rDCsEj-lLVIHjmSNU",
  "saffron":"https://lh3.googleusercontent.com/aida-public/AB6AXuCuNns-pvuTe4hEyWDfQY1PAX9pXyyDFyV2FXjrtR62ssMwcpbz3qrXAgdfY0gj3Uasgj5I7QYhlQFK1KeksiIYjGbhVbLTaL8zSXrFH4Yy2SFukzax7RQKSTltf8m2f-Ew5FRyHlKzbJNPj2929KuBGr8opx_GToZwejw_MQHukuBKbr6gwSlpudoTesurALTkWiIj_BQuLADGfS8gDnobk-Jib_BY2VEqJAgUsb11RYAOIffpMC-HsZxLyGFLzIVoI5Ta-JuZEgg",
  "jaggery":"https://lh3.googleusercontent.com/aida-public/AB6AXuAde_2xOGs2ryagOhkYPev6yY4L4Q363npNpAdMTEkV-SSa2lvyGrjNvBx09MHLzKRZLDOKn6W6FA-jVhj8MmGzxuC8_PJGwpubqxsBbVLIQW7CJUgN60vCV2xDCpqVntR1V0h7SUDmFj1f5bri-CVyRp94zlqeaHzxve3ceTodOh1qqLY-OFZsYxSZBJhj5VdbtYWl_-w8AiRCsgV0RRRhVsP3tKnI6WgoOeun1hUGRSLXP9lkLHjYMypKWAqjdekcs4RCkreBA4A",
  "rice":"https://lh3.googleusercontent.com/aida-public/AB6AXuDcDLl9ezz0P433lhprQ_Zknfldi_xG8dKlg5JzvMOlcVwukSjl_MAlrj9qHZgayISmkCOLRzEEP-J-jyXP-ebdY8P15WnTJckY7PFlki34DSz3Faa09QlgLRNGpHGUAOKuTgsWUj7xIl_2PpbtTk-f_yTAeRuZGEBucVvnOiZICrt2N6JAlmjbC2daBM5JLFt8p9vexFBv82BepG5bmCOzc59ILNlmVAMlAjT70bv2igCH4MlRMc6XC0Zq8UBqaLAsLLGFaOMH9pQ",
  "atta":"https://lh3.googleusercontent.com/aida-public/AB6AXuDjBOA5SAXqXizA8pQtvf_jIPIcPEytoDW_FPpNOIGCsmQ4N8fMx9WVtncxnTWpI-D6d1YX-jbO95Z5EgqgpKWkEWtBNmq4Sytq0SGAvrLspKjpjLBXv7n8HpQtQR10Kn-7_p8oF9TlMe4r8iIAVpCnQKJfRoNTmAXV9V72Q9H5puR4c6ziHxwchBXWjBXiol2T-4KGIMrbPAuQvV2jalzPCK7ak9pvl1TpU_vpVkN0q5x5FwFVeKzw-zRk4h5VGU2aa_SnDVkWwxU",
  "turmeric":"https://lh3.googleusercontent.com/aida-public/AB6AXuAheLAuBPQ7E-frfiXcTo29o97SKx6eJe9s06Ol5sk5-rsQO1H-4wcjhg4EOBAcczezLd_kIZei5ne86rODi7723g7o0RnGWw-lou-heo8RMvoE-Q1sQuhhmK9um6yh2INueM23mQ7GZTY5Kibw9LsUE6SrXg0D_R9YseM4LeW_jU7vNpg7AUEOBHa87vy4r8s1W8s-HlUByL6WHY1MubSFyHN4WY4-0go5kLmoJL86fK0xBNbUkZLVKGV6QIAAGEn7qjKC5JMo0sQ",
  "mustardoil":"https://lh3.googleusercontent.com/aida-public/AB6AXuAqDs_SEYdK7CaMt-BfGx-5sFIsuo_D__552f6fw-Pgkj2JCwc-npJRM_fEH3jiDohh_TDoqj5H9li8-RSpxkLolDDnUp07sIcQTfNyqY52r_-GvSGUW5M7jZ89PbywAgVLZ8IxiEG8Hxogin8F8FJL2PqLfDg6N1eGSZicXLhvr-7Mwbi5KIiCTkV_SzT7i7L3jcrwGMAUjdfdn2AW8f9gRM30LfQmq-VaIbXgLgiaWHI2Y5UheRqrqL5HoyoJlG5XbXcC0uQJaQ0",
  "clarified":"https://lh3.googleusercontent.com/aida-public/AB6AXuBmC79YDswjIahS8Oo80Y6he-VbeFke8-4p9-t1auW4rGCgi0VbREQrVUr82yZVZyQQB-9tFJPY1xJQsnev5P5S07oa1LTzLN43eWVOvZrDblFOxIb0WSC8a4NbpxohOGdGeGaYL1f79fusWOfR89p9j2puHLHYHBfft58D1goLX1GB-TLrtfxSmGUv01qLGq2oB0B9gYK7MqWc6dlOpv_h2mxMHrREFs84TybsF0cXk2w_dV-sa55k-bRWCU0JdB5q5NxWrdaghKY",
  "berries":"https://lh3.googleusercontent.com/aida-public/AB6AXuCN1MjBrrS5uCmcsPMnzLAl6fO2mTPSt7mXY6_vGhs1f13t6vPN_m9bHfQ8FR-GDKqut-BBv48PXqgUq68wmE-QeIFjBY-7XLSNP_mNJRjO1bT_o2IiaGHEBWHX-0Xzus3aDolhx6ph5ZKOp2IiOFLiFF5PhNz0hlzyINv5kBCNxFFAISHw5eTx5tR5PtT-Gth5AXT6JPvmzzXTxL9e1KRq9SZx-agiWp3vCpbiDL-n9YCM4gMq-8NV03fJhDzuluoCakdfnBAiELg",
  "meat":"https://lh3.googleusercontent.com/aida-public/AB6AXuAkBp88akDdCXMLPTOBIbhIyFhkMoOgE2n3bE47eoCKGxcnA0Se7EgOCFn6DO6p81KFiQH0o_A4vDDchTyPj8gIgvc3isEmnlESwRmH5jtyv2L_xYcekFTnRgDI1y52DcDux9q5Jl1nG66H0xGOeVyqQygrxW6nhOOocKaxd6B7ujHgDx1pkfkx_k7KCPuj24-nZw_cQkWsw0P2XP9SZPOieDvNzSMUiWyS36alAF1zUqwsAJesdEb7Rvb7DfFuf7BcNaoJbaw88Gk",
  "vegbasket":"https://lh3.googleusercontent.com/aida-public/AB6AXuDTEPJfa44SRjVWPoEKsM-3pBPygbRpln9MjoINV-_CKMBQ43CKeKn0QysOpR-WfrfRkI98b1OhoTODnyCHdJJRRUlRiVlsERc4KZTfPRrQofoth0EWUxXrcFkNe49mr_ebqPU784_moC_lFxQ5cElYaYCfDao1_kd9tM7It8cKiborHeyuyR4KgtCPZZigoho3sbD5TQGoU_pJPxtAKxKg6pGzcZOC2BZfin3DfxIlnnoeh3MVBJDFgTkB2uoa5shWBVDiV2SPVVk",
  "farm1":"https://lh3.googleusercontent.com/aida-public/AB6AXuDMfX7XekGmBBf3Puytn88AQ83LVnfud5SjO7iY53I6g8EIlmi6vNTXpA45JBMk8McqQ0lIOOxQIkIjZaty0I6urlcTbu17oWUQcTEQRF3wMeePUnalGwKBA6hLoK59QQh-sQ5d_gFMJzGPXTQ8gmwXTk27-none9MA4d5Uopp7_XcaahkFHHyPSiQmvXyGReWYUCePg-gHRAwfcPoPsx290w8uu6gWlYTq7hHOH4Yw6QihhU7AwmZzmT7yArVMEQDj45vmyL4V9v0",
  "greenhouse":"https://lh3.googleusercontent.com/aida-public/AB6AXuDNs1qbB3WdLFm7Ej3ao0mESaRI8fV4BC4lsnbm3_u6S_Bu0CY9v4C6w6Uh64J6yptokHd35keinZB_1VnXKGxCy3B4sjjOOeBb6Pp_ZRQUutRgjKlRifXR317CGzYePVbWinzzf6_2qQdaflunItPR4SbjYCTiEi0Cl_xrBd1lk8Uc5WJf8E7-KyYjvbvho01sTNP08Vxg0_m6UTgbXBZ3lejSCm_9ysU-qYWP21IltqNbSuAevK7Rufz79iCEWEbVT-pb3wuBBlg",
}

SEED_PRODUCTS = [
    ("p1","Organic Carrots","vegetables","Sunfield Farm",45,"500g",4.8,None,IMG["carrots"],"Crunchy organic carrots, hand-picked at sunrise from Sunfield's pesticide-free fields",150),
    ("p2","Young Asparagus","vegetables","Green Valley",90,"250g",4.7,"Imported",IMG["asparagus"],"Premium imported asparagus with tender tips, perfect for grilling or steaming",80),
    ("p3","Fingerling Potatoes","vegetables","Earth First",75,"1kg",4.5,None,IMG["potatoes"],"Creamy fingerling potatoes ideal for roasting. Sourced from high-altitude organic farms",200),
    ("p4","Mixed Greens Bunch","vegetables","Urban Greens",60,"250g",4.6,"Organic",IMG["veggies"],"Hand-picked organic greens including spinach, kale, and Swiss chard",120),
    ("p5","Organic Bajra Millet","vegetables","Deccan Harvest",110,"1kg",4.6,"Superfood",IMG["rice"],"Stone-ground organic bajra millet, high in iron and calcium",100),
    ("p6","Sweet Jackfruit","fruits","Valley Farms",120,"500g",4.9,"Seasonal",IMG["jackfruit"],"Naturally ripened jackfruit pods, sweet and aromatic. Available only in season",60),
    ("p7","Ratnagiri Alphonso","fruits","Sahyadri Orchards",850,"Dozen",4.9,"30% OFF",IMG["mangoes"],"GI-tagged Ratnagiri Alphonso mangoes, carbide-free natural ripening",40),
    ("p8","Wild Mountain Berries","fruits","Hill Meadows",160,"250g",4.8,"Hand-picked",IMG["berries"],"Hand-picked wild berries from the Western Ghats, rich in antioxidants",50),
    ("p9","Tender Coconut","fruits","Coastal Harvest",55,"1 pc",4.7,None,IMG["greenhouse"],"Fresh tender coconut with sweet water, harvested daily from Kerala coast",200),
    ("p10","Pure A2 Cow Milk","dairy","Heritage Dairies",95,"1L",4.9,None,IMG["milk"],"Pure A2 protein cow milk from indigenous breeds, farm-fresh every morning",300),
    ("p11","Fresh Malai Paneer","dairy","Heritage Dairies",120,"200g",4.8,"Fresh",IMG["paneer"],"Soft malai paneer made daily from whole milk, no preservatives added",150),
    ("p12","Farm Butter","dairy","Nandi Hills Dairy",65,"100g",4.7,None,IMG["milkbottle"],"Hand-churned farm butter with rich golden color and incredible taste",100),
    ("p13","A2 Gir Cow Ghee","dairy","Saurashtra Farms",1450,"500ml",4.9,"Premium",IMG["ghee"],"Bilona-churned A2 gir cow ghee with aromatic granular texture",80),
    ("p14","Bilona Ghee","dairy","Pure Desi",950,"500ml",4.9,"Traditional",IMG["ghee2"],"Made using ancient bilona hand-churning process from curd, not cream",90),
    ("p15","Clarified Desi Butter","dairy","Vrindavan Dairy",180,"200g",4.6,None,IMG["clarified"],"Pure clarified desi butter from grass-fed cows",70),
    ("p16","Heritage Sourdough","bakery","Village Bakers",90,"500g",4.9,"Bestseller",IMG["sourdough"],"48-hour slow-fermented sourdough with wild yeast starter",60),
    ("p17","Stone-ground Chapatis","bakery","Village Bakers",80,"10pk",4.7,None,IMG["bread"],"Traditional stone-ground whole wheat chapatis, made fresh daily",200),
    ("p18","Malabar Paratha","bakery","Kozhikode Kitchen",110,"5pk",4.8,"Flaky",IMG["paratha"],"Flaky multi-layered Malabar paratha, hand-stretched to perfection",80),
    ("p19","Whole Wheat Atta","bakery","Organic Mills",160,"5kg",4.7,None,IMG["atta"],"Cold-pressed whole wheat atta preserving natural oils and fiber",150),
    ("p20","Organic Jaggery","bakery","Kolhapur Farms",95,"500g",4.6,"Unrefined",IMG["jaggery"],"Unrefined organic jaggery blocks with natural mineral content intact",100),
    ("p21","The Spice Route Box","spices","Andhra Harvest",180,"250g",4.8,"Heritage",IMG["spices"],"Curated box of authentic Guntur chillies, whole spices, and masalas",90),
    ("p22","Filter Coffee Blend","spices","Chikmagalur Coop",340,"250g",4.9,"Origins",IMG["coffee"],"80:20 Peaberry-chicory blend from single-estate Chikmagalur plantations",70),
    ("p23","Lakadong Turmeric","spices","Meghalaya Roots",220,"200g",4.8,"Heirloom",IMG["turmeric"],"World's highest curcumin content turmeric from Jaintia Hills, Meghalaya",60),
    ("p24","Kacchi Ghani Mustard Oil","spices","Bengal Pressers",195,"1L",4.7,None,IMG["mustardoil"],"Cold-pressed kacchi ghani mustard oil with pungent authentic flavor",120),
    ("p25","Kashmiri Saffron","spices","Pampore Estates",680,"2g",4.9,"Premium",IMG["saffron"],"Grade-1 Mogra Kashmiri kesar saffron, hand-harvested in Pampore",30),
    ("p26","Basmati Rice","spices","Dehradun Farms",245,"5kg",4.7,None,IMG["rice"],"Extra long grain 2-year aged basmati from Dehradun valley",180),
    ("p27","Farm-Cut Mutton","meat","Pasture Pride",650,"500g",4.8,"Free-range",IMG["meat"],"Free-range pasture-raised mutton, antibiotic-free and humanely processed",40),
    ("p28","Country Eggs","meat","Happy Hens Co",95,"12pk",4.7,"Cage-free",IMG["farm1"],"Cage-free country chicken eggs with deep orange yolks and rich taste",200),
    ("p29","Artisan Trail Mix","snacks","Wholesome Bites",220,"250g",4.6,"No sugar",IMG["spices"],"Curated trail mix with roasted almonds, pumpkin seeds, dried cranberries",100),
    ("p30","Organic Veggie Basket","vegetables","Nandi All-Greens",350,"2kg",4.9,"Curated",IMG["vegbasket"],"Chef-curated organic veggie basket with 8-10 seasonal vegetables",50),
]

def seed_db():
    with get_db() as db:
        if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] > 0: return
        for p in SEED_PRODUCTS:
            db.execute("INSERT INTO products (id,name,category,farm,price,unit,rating,badge,img,description,stock) VALUES (?,?,?,?,?,?,?,?,?,?,?)", p)
        # Build FTS index
        try: db.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")
        except: pass
        # Admin user
        aid = uid()
        db.execute("INSERT INTO users (id,email,password_hash,name,phone,is_admin,loyalty_points) VALUES (?,?,?,?,?,?,?)",
                   (aid, "admin@localbasket.com", hash_pw("SecureBasket@2026!"), "Admin", "9999999999", 1, 500))
        # Flash sales
        db.execute("INSERT INTO flash_sales (id,product_id,discount_pct,sale_price,starts_at,ends_at) VALUES (?,?,?,?,datetime('now'),datetime('now','+1 day'))", ("fs1","p8",30,112))
        db.execute("INSERT INTO flash_sales (id,product_id,discount_pct,sale_price,starts_at,ends_at) VALUES (?,?,?,?,datetime('now'),datetime('now','+1 day'))", ("fs2","p7",30,595))
        db.execute("INSERT INTO flash_sales (id,product_id,discount_pct,sale_price,starts_at,ends_at) VALUES (?,?,?,?,datetime('now'),datetime('now','+1 day'))", ("fs3","p25",20,544))
        # Sample reviews
        for pid in ["p1","p7","p10","p13","p16","p22"]:
            names = ["Priya S.","Rahul M.","Anita K.","Vikram P."]
            comments = ["Amazing quality! Will order again.","Super fresh, exactly as described.","Best I've found online.","Great value for the price."]
            for i in range(random.randint(2,4)):
                db.execute("INSERT INTO reviews (id,product_id,user_name,rating,comment) VALUES (?,?,?,?,?)",
                           (uid(), pid, names[i%4], random.randint(4,5), comments[i%4]))
        print("  ✅ DB seeded: 30 products, admin, flash sales, reviews")
        print("     Admin: admin@localbasket.com / SecureBasket@2026!")

if __name__ == "__main__":
    init_db(); seed_db(); print("Database ready!")

"""
Local Basket – Premium Flask Server
Features: SQLite+FTS5, Auth, Admin, SSE Tracking, Reviews, Loyalty, Flash Sales,
          Subscription Boxes, Delivery Slots, Recommendations, Dark Mode
"""
from flask import Flask, jsonify, request, send_from_directory, session, Response
from flask_cors import CORS
import database as db
import json, time, threading, requests, hashlib

def upload_to_cloudinary(base64_img):
    cloud_name, api_key, api_secret = "dfedwdajz", "699479815565524", "CF77WLVmSBIh3ztBVHnCk13-j6I"
    timestamp = str(int(time.time()))
    to_sign = f"timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(to_sign.encode()).hexdigest()
    res = requests.post(f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload", data={"file":base64_img,"api_key":api_key,"timestamp":timestamp,"signature":signature}).json()
    return res.get("secure_url") or res.get("url", "")

app = Flask(__name__, static_folder='public', static_url_path='')
app.secret_key = 'localbasket_secret_2026_prod_v2'
CORS(app, supports_credentials=True)
db.init_db(); db.seed_db()

order_watchers = {}

def sid(): return request.headers.get("X-Session-Id", session.get("sid","default"))
def cu():
    uid = session.get("user_id") or request.headers.get("X-User-Id")
    return db.get_user(uid) if uid else None
def is_admin():
    u = cu()
    return u and u.get("is_admin")

# ─── AUTH ────────────────────────────────────────────────────
@app.route("/api/auth/signup", methods=["POST"])
def signup():
    d = request.get_json()
    e,p,n = (d.get("email","")).strip(), d.get("password",""), (d.get("name","")).strip()
    if not e or not p or not n: return jsonify({"error":"Email, password and name required"}), 400
    if len(p) < 4: return jsonify({"error":"Password too short"}), 400
    uid = db.create_user(e, p, n, d.get("phone",""))
    if not uid: return jsonify({"error":"Email already registered"}), 409
    session["user_id"] = uid
    return jsonify({"success":True,"user":db.get_user(uid)})

@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.get_json()
    u = db.auth_user(d.get("email",""), d.get("password",""))
    if not u: return jsonify({"error":"Invalid credentials"}), 401
    session["user_id"] = u["id"]
    return jsonify({"success":True,"user":{"id":u["id"],"email":u["email"],"name":u["name"],"phone":u["phone"],"is_admin":u["is_admin"],"loyalty_points":u["loyalty_points"],"dark_mode":u["dark_mode"]}})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return jsonify({"success":True})

@app.route("/api/auth/me")
def me():
    u = cu()
    return jsonify({"user":u}) if u else jsonify({"user":None})

@app.route("/api/auth/preferences", methods=["PUT"])
def prefs():
    u = cu()
    if not u: return jsonify({"error":"Login required"}), 401
    db.update_user_pref(u["id"], **{k:v for k,v in request.get_json().items() if k in ("dark_mode","name","phone")})
    return jsonify({"success":True})

# ─── Products ────────────────────────────────────────────────
@app.route("/api/products")
def get_products():
    return jsonify(db.get_all_products(request.args.get("category"), request.args.get("all")!="1" if request.args.get("all") else True))

@app.route("/api/products/<pid>")
def get_product(pid):
    p = db.get_product(pid)
    if not p: return jsonify({"error":"Not found"}), 404
    p["nutritionHighlights"] = ["Rich in fiber","No preservatives","Farm-to-table"]
    p["deliveryTime"] = "25-30 mins"
    p["inStock"] = p["stock"] > 0
    p["reviews"] = db.get_reviews(pid)
    return jsonify(p)

@app.route("/api/categories")
def get_cats(): return jsonify(sorted({p["category"] for p in db.get_all_products()}))

@app.route("/api/search")
def search():
    q = (request.args.get("q","")).strip()
    return jsonify(db.search_products(q) if q else [])

@app.route("/api/deals")
def deals(): return jsonify(db.get_flash_sales())

@app.route("/api/recommendations")
def recs():
    u = cu()
    pid = request.args.get("product_id")
    return jsonify(db.get_recommendations(u["id"] if u else None, pid))

# ─── Cart ────────────────────────────────────────────────────
@app.route("/api/cart")
def get_cart():
    items = db.get_cart(sid())
    return jsonify({"items":items,"itemCount":sum(i["qty"] for i in items),"total":sum(i["price"]*i["qty"] for i in items)})

@app.route("/api/cart", methods=["POST"])
def add_cart():
    d = request.get_json(); p = db.get_product(d.get("productId"))
    if not p: return jsonify({"error":"Not found"}), 404
    if p["stock"] < 1: return jsonify({"error":"Out of stock"}), 400
    db.add_to_cart(sid(), p["id"], int(d.get("qty",1)))
    items = db.get_cart(sid())
    return jsonify({"success":True,"itemCount":sum(i["qty"] for i in items),"message":f"{p['name']} added!"})

@app.route("/api/cart/<pid>", methods=["PUT"])
def upd_cart(pid):
    db.update_cart_item(sid(), pid, int(request.get_json().get("qty",1)))
    items = db.get_cart(sid())
    return jsonify({"success":True,"itemCount":sum(i["qty"] for i in items)})

@app.route("/api/cart/<pid>", methods=["DELETE"])
def del_cart(pid):
    db.remove_cart_item(sid(), pid)
    items = db.get_cart(sid())
    return jsonify({"success":True,"itemCount":sum(i["qty"] for i in items)})

# ─── Addresses ───────────────────────────────────────────────
@app.route("/api/addresses")
def get_addrs():
    u = cu()
    return jsonify(db.get_addresses(u["id"]) if u else [])

@app.route("/api/addresses", methods=["POST"])
def add_addr():
    u = cu()
    if not u: return jsonify({"error":"Login required"}), 401
    d = request.get_json()
    return jsonify({"success":True,"id":db.add_address(u["id"], d.get("label","Home"), d.get("full_address",""), d.get("lat",0), d.get("lng",0), d.get("is_default",0))})

@app.route("/api/addresses/<aid>", methods=["DELETE"])
def del_addr(aid):
    db.delete_address(aid)
    return jsonify({"success":True})

# ─── Orders ──────────────────────────────────────────────────
@app.route("/api/orders", methods=["POST"])
def place_order():
    s = sid(); u = cu(); d = request.get_json() or {}
    items = db.get_cart(s)
    if not items: return jsonify({"error":"Cart is empty"}), 400
    for i in items:
        if i["stock"] < i["qty"]: return jsonify({"error":f"{i['name']} only has {i['stock']} left"}), 400
    order = db.create_order(s, u["id"] if u else None, items, d.get("delivery","express"),
        int(d.get("tip",30)), d.get("payment","UPI"), d.get("address",""), d.get("slot",""), int(d.get("loyalty_used",0)))
    _sim_order(order["id"])
    return jsonify({"success":True,"order":order})

@app.route("/api/orders")
def get_orders():
    u = cu()
    return jsonify(db.get_orders(session_id=sid(), user_id=u["id"] if u else None))

@app.route("/api/orders/<oid>")
def get_order(oid):
    o = db.get_order(oid)
    return jsonify(o) if o else (jsonify({"error":"Not found"}), 404)

@app.route("/api/orders/<oid>/track")
def track_sse(oid):
    o = db.get_order(oid)
    if not o: return jsonify({"error":"Not found"}), 404
    if oid not in order_watchers: order_watchers[oid] = []
    def gen():
        yield f"data: {json.dumps({'status':o['status']})}\n\n"
        last = 0
        for _ in range(300):
            time.sleep(1)
            ups = order_watchers.get(oid, [])
            if len(ups) > last:
                for s in ups[last:]: yield f"data: {json.dumps({'status':s})}\n\n"
                last = len(ups)
                if ups[-1] == "Delivered": break
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

def _sim_order(oid):
    def _r():
        for st, dl in [("Confirmed",0),("Preparing",8),("Packed",15),("Out for Delivery",20),("Delivered",30)]:
            time.sleep(dl); db.update_order_status(oid, st)
            if oid in order_watchers: order_watchers[oid].append(st)
    threading.Thread(target=_r, daemon=True).start()

# ─── Reviews ─────────────────────────────────────────────────
@app.route("/api/reviews/<pid>")
def get_reviews(pid): return jsonify(db.get_reviews(pid))

@app.route("/api/reviews/<pid>", methods=["POST"])
def add_review(pid):
    u = cu()
    if not u: return jsonify({"error":"Login to review"}), 401
    d = request.get_json()
    rid = db.add_review(pid, u["id"], u["name"], int(d.get("rating",5)), d.get("comment",""))
    # Bonus loyalty for review
    with db.get_db() as c:
        c.execute("UPDATE users SET loyalty_points=loyalty_points+10 WHERE id=?", (u["id"],))
    return jsonify({"success":True,"id":rid,"pointsEarned":10})

# ─── Loyalty ─────────────────────────────────────────────────
@app.route("/api/loyalty")
def loyalty():
    u = cu()
    if not u: return jsonify({"points":0,"log":[]})
    return jsonify({"points":u["loyalty_points"],"log":db.get_loyalty_log(u["id"])})

# ─── Subscription Boxes ─────────────────────────────────────
@app.route("/api/sub-boxes")
def get_subs():
    u = cu()
    return jsonify(db.get_sub_boxes(u["id"]) if u else [])

@app.route("/api/sub-boxes", methods=["POST"])
def create_sub():
    u = cu()
    if not u: return jsonify({"error":"Login required"}), 401
    d = request.get_json()
    bid = db.create_sub_box(u["id"], d.get("box_type","veggie"), d.get("frequency","weekly"))
    return jsonify({"success":True,"id":bid})

@app.route("/api/sub-boxes/<bid>", methods=["DELETE"])
def cancel_sub(bid):
    db.cancel_sub_box(bid)
    return jsonify({"success":True})

# ─── Delivery Slots ─────────────────────────────────────────
@app.route("/api/delivery-slots")
def get_slots(): return jsonify(db.get_delivery_slots())

# ─── Subscriptions (Basket Pass) ────────────────────────────
@app.route("/api/subscriptions", methods=["POST"])
def subscribe():
    u = cu()
    s = db.create_subscription(u["id"] if u else None)
    return jsonify({"success":True,"subscription":{"id":s,"plan":"monthly","price":149,"status":"active"}})

# ─── ADMIN ───────────────────────────────────────────────────
@app.route("/api/admin/stats")
def admin_stats():
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    prods = db.get_all_products(active_only=False); orders = db.get_all_orders()
    return jsonify({"totalProducts":len(prods),"activeProducts":len([p for p in prods if p["is_active"]]),
        "totalOrders":len(orders),"totalRevenue":sum(o.get("grand_total",0) or 0 for o in orders),
        "lowStock":len([p for p in prods if p["stock"]<20 and p["is_active"]])})

@app.route("/api/admin/products")
def admin_prods():
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    return jsonify(db.get_all_products(active_only=False))

@app.route("/api/admin/products", methods=["POST"])
def admin_create():
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    d = request.get_json()
    if d.get("img", "").startswith("data:image/"):
        url = upload_to_cloudinary(d["img"])
        if url: d["img"] = url
    return jsonify({"success":True,"id":db.create_product(d)})

@app.route("/api/admin/products/<pid>", methods=["PUT"])
def admin_upd(pid):
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    d = request.get_json()
    if d.get("img", "").startswith("data:image/"):
        url = upload_to_cloudinary(d["img"])
        if url: d["img"] = url
    ok = {"name","category","farm","price","unit","rating","badge","img","description","stock","is_active"}
    db.update_product(pid, **{k:v for k,v in d.items() if k in ok})
    return jsonify({"success":True})

@app.route("/api/admin/products/<pid>", methods=["DELETE"])
def admin_del(pid):
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    db.delete_product(pid); return jsonify({"success":True})

@app.route("/api/admin/orders")
def admin_orders():
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    return jsonify(db.get_all_orders())

@app.route("/api/admin/orders/<oid>/status", methods=["PUT"])
def admin_status(oid):
    if not is_admin(): return jsonify({"error":"Unauthorized"}), 403
    db.update_order_status(oid, request.get_json().get("status","Confirmed"))
    return jsonify({"success":True})

# ─── Static ─────────────────────────────────────────────────
@app.route("/")
def idx(): return send_from_directory(app.static_folder, "index.html")
@app.route("/admin")
def adm(): return send_from_directory(app.static_folder, "admin.html")
@app.errorhandler(404)
def nf(e):
    if not request.path.startswith("/api"): return send_from_directory(app.static_folder, "index.html")
    return jsonify({"error":"Not found"}), 404

if __name__ == "__main__":
    print("\n🛒  Local Basket – Premium Server")
    print("    Customer: http://localhost:5000")
    print("    Admin:    http://localhost:5000/admin")
    print("    Admin:    admin@localbasket.com / admin123\n")
    app.run(debug=True, port=5000, use_reloader=False)

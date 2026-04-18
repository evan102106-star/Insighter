from flask import Flask, request, jsonify, render_template, redirect, url_for
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import numpy as np

app = Flask(__name__)
ALLOWED_UI_IPS = [
    "192.168.0.146"   # YOUR PC ONLY (UI access)
]

ALLOWED_AGENT_ROUTES = (
    "/start_session",
    "/end_session",
    "/idle_activity",
    "/track_app",
    "/track_network",
    "/get_actions",
    "/complete_action"
)

@app.before_request
def restrict_access():
    ip = request.remote_addr
    path = request.path

    # ✅ Allow agent/daemon APIs from anywhere (or restrict later)
    if path.startswith(ALLOWED_AGENT_ROUTES):
        return

    # 🔐 Restrict UI access
    if ip not in ALLOWED_UI_IPS:
        return "Access Denied", 403
# =========================================================
# DB
# =========================================================
engine = create_engine("mysql+mysqlconnector://asad:1234@localhost:3000/hack")

# =========================================================
# ML CORE
# =========================================================
model = IsolationForest(contamination=0.08, random_state=42)
scaler = StandardScaler()
trained = False

FEATURES = [
    "login_hour", "duration", "file_count",
    "usb_count", "net_bytes", "idle_time", "app_switch_rate"
]

# =========================================================
# FEATURE ENGINE
# =========================================================
def build_features():
    sessions = pd.read_sql("SELECT * FROM sessions", engine)
    files = pd.read_sql("SELECT * FROM file_activity", engine)
    network = pd.read_sql("SELECT * FROM network_activity", engine)
    usb = pd.read_sql("SELECT * FROM usb_usage", engine)
    idle = pd.read_sql("SELECT * FROM idle_activity", engine)
    apps = pd.read_sql("SELECT * FROM app_usage", engine)

    if sessions.empty:
        return pd.DataFrame()

    df = sessions.copy()
    df["session_id"] = df["id"]

    df["login_time"] = pd.to_datetime(df["login_time"], errors="coerce")
    df["logout_time"] = pd.to_datetime(df["logout_time"], errors="coerce")

    df["login_hour"] = df["login_time"].dt.hour.fillna(0)
    df["duration"] = (df["logout_time"] - df["login_time"]).dt.total_seconds().fillna(0)

    df = df.merge(files.groupby("session_id").size().reset_index(name="file_count"), on="session_id", how="left")
    df = df.merge(usb.groupby("session_id").size().reset_index(name="usb_count"), on="session_id", how="left")

    net = network.groupby("session_id")[["bytes_sent", "bytes_received"]].sum()
    net["net_bytes"] = net["bytes_sent"] + net["bytes_received"]
    df = df.merge(net[["net_bytes"]].reset_index(), on="session_id", how="left")

    idle_latest = idle.sort_values("timestamp").groupby("session_id").tail(1) if not idle.empty else pd.DataFrame()
    if not idle_latest.empty:
        df = df.merge(idle_latest[["session_id", "idle_time"]], on="session_id", how="left")

    df = df.merge(apps.groupby("session_id").size().reset_index(name="app_switch_rate"), on="session_id", how="left")

    return df.fillna(0)

# =========================================================
# ML
# =========================================================
def train(df):
    global trained
    X = scaler.fit_transform(df[FEATURES])
    model.fit(X)
    trained = True

def score(row):
    X = scaler.transform([[row[f] for f in FEATURES]])
    pred = model.predict(X)[0]
    conf = model.decision_function(X)[0]
    return {
        "ml_label": "ANOMALY" if pred == -1 else "NORMAL",
        "ml_score": float(conf)
    }

def decide_action(risk, row):
    if risk >= 4: return "SHUTDOWN"
    elif risk == 3: return "BLOCK_USER"
    elif risk == 2: return "RESTRICT_USER"
    elif row["net_bytes"] > 800_000_000: return "LIMIT_NETWORK"
    return "ALLOW"

# =========================================================
# ACTION SYSTEM
# =========================================================
def send_action(session_id, action):
    with engine.begin() as conn:
        exists = conn.execute(
            text("""
                SELECT COUNT(*) FROM action_queue
                WHERE session_id=:sid AND action=:a AND status='PENDING'
            """),
            {"sid": session_id, "a": action}
        ).scalar()

        if not exists:
            conn.execute(
                text("""
                    INSERT INTO action_queue (session_id, action, status)
                    VALUES (:sid, :a, 'PENDING')
                """),
                {"sid": session_id, "a": action}
            )

# =========================================================
# DETECTION
# =========================================================
def detect_fingerprints():
    global trained

    df = build_features()
    if df.empty:
        return []

    if not trained:
        train(df)

    baseline = df[FEATURES].mean()
    results = []

    for _, row in df.iterrows():
        r = row.to_dict()
        r.update(score(row))

        deviation = sum(abs(row[f] - baseline[f]) for f in FEATURES)

        risk = 0
        if r["ml_label"] == "ANOMALY": risk += 1
        if deviation > baseline.mean() * 1.5: risk += 1
        if row["net_bytes"] > baseline["net_bytes"] * 2: risk += 1
        if row["file_count"] > baseline["file_count"] * 2: risk += 1

        r["risk_score"] = risk
        r["behavior_deviation"] = float(deviation)

        action = decide_action(risk, r)
        r["action"] = action

        if action != "ALLOW":
            send_action(r["session_id"], action)

        results.append(r)

    return results

# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/live_stream")
def live_stream():
    return jsonify(detect_fingerprints())

# ================= SESSION DETAIL =================
@app.route("/session/<int:session_id>")
def session_detail(session_id):
    with engine.connect() as conn:
        session = pd.read_sql(text("SELECT * FROM sessions WHERE id=:id"), conn, params={"id": session_id})
        files = pd.read_sql(text("SELECT * FROM file_activity WHERE session_id=:id"), conn, params={"id": session_id})
        network = pd.read_sql(text("SELECT * FROM network_activity WHERE session_id=:id"), conn, params={"id": session_id})
        usb = pd.read_sql(text("SELECT * FROM usb_usage WHERE session_id=:id"), conn, params={"id": session_id})
        apps = pd.read_sql(text("SELECT * FROM app_usage WHERE session_id=:id"), conn, params={"id": session_id})

    return render_template(
        "user.html",
        session=session.to_dict(orient="records")[0] if not session.empty else {},
        files=files.to_dict(orient="records"),
        network=network.to_dict(orient="records"),
        usb=usb.to_dict(orient="records"),
        apps=apps.to_dict(orient="records")
    )

# ================= SEARCH =================
@app.route("/search", methods=["POST"])
def search():
    query = request.form.get("query", "").strip()

    # If numeric → go to session page
    if query.isdigit():
        return redirect(url_for("session_detail", session_id=int(query)))

    data = detect_fingerprints()
    results = []

    for s in data:
        username = str(s.get("username", "")).lower()

        if query.lower() in username:
            results.append(s)

        elif query.lower() == "anomaly" and s["ml_label"] == "ANOMALY":
            results.append(s)

    return jsonify(results)

# =========================================================
# ACTION APIs (FOR DAEMON)
# =========================================================
@app.route("/get_actions")
def get_actions():
    session_id = request.args.get("session_id")

    if not session_id:
        return jsonify({"actions": []})

    with engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT * FROM action_queue
                WHERE session_id = :sid AND status = 'PENDING'
            """),
            conn,
            params={"sid": session_id}
        )

    return jsonify({"actions": df.to_dict(orient="records")})

@app.route("/complete_action", methods=["POST"])
def complete_action():
    data = request.json

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE action_queue SET status='DONE' WHERE id=:id"),
            {"id": data.get("id")}
        )

    return jsonify({"status": "done"})

# =========================================================
# AGENT APIs
# =========================================================
@app.route("/start_session", methods=["POST"])
def start_session():
    d = request.json
    with engine.begin() as conn:
        res = conn.execute(text("""
            INSERT INTO sessions (username, system_id, ip_address, login_time)
            VALUES (:u, :s, :ip, NOW())
        """), {"u": d["username"], "s": d["system_id"], "ip": d.get("ip_address","")})

    return jsonify({"session_id": res.lastrowid})

@app.route("/idle_activity", methods=["POST"])
def idle_activity():
    d = request.json
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO idle_activity (session_id, idle_time, timestamp)
            VALUES (:sid, :idle, NOW())
        """), {"sid": d["session_id"], "idle": d["idle_time"]})
    return jsonify({"status": "ok"})

@app.route("/track_app", methods=["POST"])
def track_app():
    d = request.json
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO app_usage (session_id, app_name, timestamp)
            VALUES (:sid, :app, NOW())
        """), {"sid": d["session_id"], "app": d["app_name"]})
    return jsonify({"status": "ok"})

@app.route("/track_network", methods=["POST"])
def track_network():
    d = request.json
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO network_activity (session_id, bytes_sent, bytes_received, timestamp)
            VALUES (:sid, :sent, :recv, NOW())
        """), {"sid": d["session_id"], "sent": d["bytes_sent"], "recv": d["bytes_received"]})
    return jsonify({"status": "ok"})

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(port=5000,host="0.0.0.0",debug=True)
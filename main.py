from flask import Flask, request, jsonify, render_template, redirect, url_for
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import numpy as np

app = Flask(__name__)

# =========================================================
# DB CONNECTION
# =========================================================
engine = create_engine("mysql+mysqlconnector://asad:1234@localhost:3000/hack")

# =========================================================
# ML CORE
# =========================================================
model = IsolationForest(contamination=0.08, random_state=42)
scaler = StandardScaler()
trained = False

FEATURES = [
    "login_hour",
    "duration",
    "file_count",
    "usb_count",
    "net_bytes",
    "idle_time",
    "app_switch_rate"
]

# =========================================================
# FEATURE ENGINE (FIXED)
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

    file_count = files.groupby("session_id").size().reset_index(name="file_count")
    usb_count = usb.groupby("session_id").size().reset_index(name="usb_count")

    net = network.groupby("session_id")[["bytes_sent", "bytes_received"]].sum()
    net["net_bytes"] = net["bytes_sent"] + net["bytes_received"]
    net = net[["net_bytes"]].reset_index()

    idle_latest = pd.DataFrame(columns=["session_id", "idle_time"])
    if not idle.empty and "timestamp" in idle.columns:
        idle_latest = idle.sort_values("timestamp").groupby("session_id").tail(1)
        idle_latest = idle_latest[["session_id", "idle_time"]]

    app_switch = apps.groupby("session_id").size().reset_index(name="app_switch_rate")

    df = df.merge(file_count, on="session_id", how="left")
    df = df.merge(usb_count, on="session_id", how="left")
    df = df.merge(net, on="session_id", how="left")
    df = df.merge(idle_latest, on="session_id", how="left")
    df = df.merge(app_switch, on="session_id", how="left")

    return df.fillna(0)

# =========================================================
# TRAIN
# =========================================================
def train(df):
    global trained
    X = scaler.fit_transform(df[FEATURES])
    model.fit(X)
    trained = True

# =========================================================
# SCORE
# =========================================================
def score(row):
    X = np.array([[row[f] for f in FEATURES]])
    X = scaler.transform(X)

    pred = model.predict(X)[0]
    conf = model.decision_function(X)[0]

    return {
        "ml_label": "ANOMALY" if pred == -1 else "NORMAL",
        "ml_score": float(conf)
    }

# =========================================================
# ACTION ENGINE
# =========================================================
def decide_action(risk_score, row):
    if risk_score >= 4:
        return "SHUTDOWN"
    elif risk_score == 3:
        return "BLOCK_USER"
    elif risk_score == 2:
        return "RESTRICT_USER"
    elif row["net_bytes"] > 800_000_000:
        return "LIMIT_NETWORK"
    return "ALLOW"

# =========================================================
# SEND ACTION
# =========================================================
def send_action(session_id, action):
    with engine.begin() as conn:

        exists = conn.execute(
            text("""
                SELECT COUNT(*) FROM action_queue
                WHERE session_id=:sid AND action=:action AND status='PENDING'
            """),
            {"sid": session_id, "action": action}
        ).scalar()

        if exists:
            return

        conn.execute(
            text("""
                INSERT INTO action_queue (session_id, action, status)
                VALUES (:sid, :action, 'PENDING')
            """),
            {"sid": session_id, "action": action}
        )

# =========================================================
# DETECTION ENGINE
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
        if r["ml_label"] == "ANOMALY":
            risk += 1
        if deviation > baseline.mean() * 1.5:
            risk += 1
        if row["net_bytes"] > baseline["net_bytes"] * 2:
            risk += 1
        if row["file_count"] > baseline["file_count"] * 2:
            risk += 1

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
    return render_template("index.html", suspicious=detect_fingerprints())


@app.route("/live_stream")
def live_stream():
    return jsonify(detect_fingerprints())


@app.route("/session/<int:session_id>")
def session_detail(session_id):

    with engine.connect() as conn:

        session = pd.read_sql(
            text("SELECT * FROM sessions WHERE id=:id"),
            conn,
            params={"id": session_id}
        )

        files = pd.read_sql(
            text("SELECT * FROM file_activity WHERE session_id=:id"),
            conn,
            params={"id": session_id}
        )

        network = pd.read_sql(
            text("SELECT * FROM network_activity WHERE session_id=:id"),
            conn,
            params={"id": session_id}
        )

        usb = pd.read_sql(
            text("SELECT * FROM usb_usage WHERE session_id=:id"),
            conn,
            params={"id": session_id}
        )

        apps = pd.read_sql(
            text("SELECT * FROM app_usage WHERE session_id=:id"),
            conn,
            params={"id": session_id}
        )

    return render_template(
        "session.html",
        session=session.to_dict(orient="records")[0] if not session.empty else {},
        files=files.to_dict(orient="records"),
        network=network.to_dict(orient="records"),
        usb=usb.to_dict(orient="records"),
        apps=apps.to_dict(orient="records")
    )

# =========================================================
# SEARCH (FIXED)
# =========================================================
@app.route("/search", methods=["POST"])
def search():
    query = request.form.get("query", "").strip()

    if query.isdigit():
        return redirect(url_for("session_detail", session_id=int(query)))

    return redirect(url_for("dashboard"))

# =========================================================
# SESSION APIs
# =========================================================
@app.route("/start_session", methods=["POST"])
def start_session():
    data = request.json

    with engine.begin() as conn:
        res = conn.execute(
            text("INSERT INTO sessions (username, system_id, login_time) VALUES (:u,:s,NOW())"),
            {"u": data["username"], "s": data["system_id"]}
        )
        return jsonify({"session_id": res.lastrowid})

@app.route("/end_session", methods=["POST"])
def end_session():
    data = request.json

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE sessions SET logout_time=NOW() WHERE id=:id"),
            {"id": data["session_id"]}
        )

    return jsonify({"status": "ended"})

# =========================================================
# ACTION API
# =========================================================
@app.route("/get_actions")
def get_actions():
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT * FROM action_queue WHERE status='PENDING'"), conn)

    return jsonify({"actions": df.to_dict(orient="records")})


@app.route("/complete_action", methods=["POST"])
def complete_action():
    data = request.json

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE action_queue SET status='DONE' WHERE id=:id"),
            {"id": data["id"]}
        )

    return jsonify({"status": "done"})

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
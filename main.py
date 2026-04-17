from flask import Flask, request, jsonify, render_template
import mysql.connector
import pandas as pd

app = Flask(__name__)

# ---------------- DB CONNECTION ----------------
db = mysql.connector.connect(
    host="localhost",
    user="asad",
    password="1234",
    database="hack",
    port=3000
)

cursor = db.cursor(dictionary=True)

# ---------------- ANOMALY DETECTION ----------------
def detect_anomalies():

    sessions = pd.read_sql("SELECT * FROM sessions", db)
    files = pd.read_sql("SELECT * FROM file_activity", db)

    if sessions.empty:
        return []

    sessions['login_hour'] = pd.to_datetime(sessions['login_time']).dt.hour

    mean_login = sessions['login_hour'].mean()
    std_login = sessions['login_hour'].std()

    sessions['login_anomaly'] = abs(sessions['login_hour'] - mean_login) > std_login

    sessions['login_time'] = pd.to_datetime(sessions['login_time'])
    sessions['logout_time'] = pd.to_datetime(sessions['logout_time'])

    sessions['duration'] = (sessions['logout_time'] - sessions['login_time']).dt.total_seconds()

    mean_dur = sessions['duration'].mean()
    std_dur = sessions['duration'].std()

    sessions['duration_anomaly'] = abs(sessions['duration'] - mean_dur) > std_dur

    if not files.empty:
        file_counts = files.groupby('session_id').size().reset_index(name='file_count')

        mean_files = file_counts['file_count'].mean()
        std_files = file_counts['file_count'].std()

        file_counts['file_anomaly'] = file_counts['file_count'] > mean_files + std_files

        sessions = sessions.merge(
            file_counts,
            left_on='id',
            right_on='session_id',
            how='left'
        )
    else:
        sessions['file_anomaly'] = False

    sessions['risk_score'] = (
        sessions['login_anomaly'].astype(int) +
        sessions['duration_anomaly'].astype(int) +
        sessions['file_anomaly'].fillna(False).astype(int)
    )

    suspicious = sessions[sessions['risk_score'] >= 2]

    return suspicious.to_dict(orient='records')


# ---------------- HOME: ONLY SUSPICIOUS ----------------
@app.route('/')
def dashboard():
    suspicious = detect_anomalies()
    return render_template("index.html", suspicious=suspicious)


# ---------------- USER DETAIL ----------------
@app.route('/user/<username>')
def user_detail(username):

    cursor.execute("SELECT * FROM sessions WHERE username=%s", (username,))
    sessions = cursor.fetchall()

    session_ids = [str(s['id']) for s in sessions]

    if not session_ids:
        return "No data"

    ids = ",".join(session_ids)

    cursor.execute(f"SELECT * FROM file_activity WHERE session_id IN ({ids})")
    files = cursor.fetchall()

    cursor.execute(f"SELECT * FROM network_activity WHERE session_id IN ({ids})")
    network = cursor.fetchall()

    cursor.execute(f"SELECT * FROM usb_usage WHERE session_id IN ({ids})")
    usb = cursor.fetchall()

    cursor.execute(f"SELECT * FROM app_usage WHERE session_id IN ({ids})")
    apps = cursor.fetchall()

    return render_template(
        "user.html",
        username=username,
        sessions=sessions,
        files=files,
        network=network,
        usb=usb,
        apps=apps
    )


# ---------------- API ROUTES ----------------
@app.route('/start_session', methods=['POST'])
def start_session():
    data = request.json

    cursor.execute(
        "INSERT INTO sessions (username, system_id, login_time) VALUES (%s, %s, NOW())",
        (data['username'], data['system_id'])
    )

    db.commit()
    return jsonify({"session_id": cursor.lastrowid})


@app.route('/end_session', methods=['POST'])
def end_session():
    data = request.json

    cursor.execute(
        "UPDATE sessions SET logout_time = NOW() WHERE id = %s",
        (data['session_id'],)
    )

    db.commit()
    return jsonify({"status": "ended"})


@app.route('/app_usage', methods=['POST'])
def app_usage():
    data = request.json

    cursor.execute(
        "INSERT INTO app_usage (session_id, app_name, usage_time) VALUES (%s, %s, %s)",
        (data['session_id'], data['app_name'], data['usage_time'])
    )

    db.commit()
    return jsonify({"status": "ok"})


@app.route('/usb_usage', methods=['POST'])
def usb_usage():
    data = request.json

    cursor.execute(
        "INSERT INTO usb_usage (session_id, device_name, start_time, end_time, duration) VALUES (%s, %s, %s, %s, %s)",
        (data['session_id'], data['device_name'], data['start_time'], data['end_time'], data['duration'])
    )

    db.commit()
    return jsonify({"status": "ok"})


@app.route('/network_activity', methods=['POST'])
def network_activity():
    data = request.json

    cursor.execute(
        "INSERT INTO network_activity (session_id, bytes_sent, bytes_received, connections, timestamp) VALUES (%s, %s, %s, %s, NOW())",
        (data['session_id'], data['bytes_sent'], data['bytes_received'], data['connections'])
    )

    db.commit()
    return jsonify({"status": "ok"})


@app.route('/file_activity', methods=['POST'])
def file_activity():
    data = request.json

    cursor.execute(
        "INSERT INTO file_activity (session_id, file_path, file_size, event_type, timestamp) VALUES (%s, %s, %s, %s, NOW())",
        (data['session_id'], data['file_path'], data['file_size'], data['event_type'])
    )

    db.commit()
    return jsonify({"status": "ok"})
@app.route('/search', methods=['POST'])
def search_user():
    username = request.form.get('username')

    if not username:
        return "Enter username"

    return user_detail(username)

if __name__ == "__main__":
    app.run(debug=True)
import pandas as pd
from sklearn.ensemble import IsolationForest

model = None

def train_model(engine):
    global model

    sessions = pd.read_sql("SELECT * FROM sessions", engine)
    files = pd.read_sql("SELECT * FROM file_activity", engine)

    if sessions.empty:
        return None

    # ---------------- FEATURES ----------------
    sessions['login_hour'] = pd.to_datetime(sessions['login_time']).dt.hour

    sessions['login_time'] = pd.to_datetime(sessions['login_time'])
    sessions['logout_time'] = pd.to_datetime(sessions['logout_time'])

    sessions['duration'] = (
        sessions['logout_time'] - sessions['login_time']
    ).dt.total_seconds().fillna(0)

    # FILE COUNT
    if not files.empty:
        file_counts = files.groupby('session_id').size().reset_index(name='file_count')
        sessions = sessions.merge(
            file_counts,
            left_on='id',
            right_on='session_id',
            how='left'
        )
    else:
        sessions['file_count'] = 0

    sessions['file_count'] = sessions['file_count'].fillna(0)

    X = sessions[['login_hour', 'duration', 'file_count']]

    # ---------------- TRAIN ----------------
    model = IsolationForest(contamination=0.1)
    model.fit(X)

    return model


def predict_session(session_row):
    global model

    if model is None:
        return "UNKNOWN"

    X = [[
        session_row['login_hour'],
        session_row['duration'],
        session_row.get('file_count', 0)
    ]]

    pred = model.predict(X)[0]

    return "HIGH" if pred == -1 else "LOW"
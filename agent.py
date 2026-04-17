import requests
import getpass
import socket
import time
import psutil
import win32gui
import win32api
import os
import json
import subprocess
import platform

# =========================================================
# CONFIG
# =========================================================
SERVER = "http://localhost:5000"
SESSION_FILE = "session.json"
POLL_INTERVAL = 5

session_id = None
app_start_times = {}

# =========================================================
# SESSION
# =========================================================
def load_session():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    return None


def save_session(data):
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f)


def start_session():
    global session_id

    existing = load_session()
    if existing:
        session_id = existing["session_id"]
        return

    data = {
        "username": getpass.getuser(),
        "system_id": socket.gethostname(),
        "ip_address": socket.gethostbyname(socket.gethostname())
    }

    res = requests.post(f"{SERVER}/start_session", json=data, timeout=5)
    session_id = res.json()["session_id"]
    save_session({"session_id": session_id})

    print("Session started:", session_id)

# =========================================================
# IDLE
# =========================================================
def get_idle_time():
    last_input = win32api.GetLastInputInfo()
    return (win32api.GetTickCount() - last_input) / 1000


def track_idle():
    try:
        requests.post(
            f"{SERVER}/idle_activity",
            json={"session_id": session_id, "idle_time": get_idle_time()},
            timeout=3
        )
    except:
        pass

# =========================================================
# ACTION FETCH
# =========================================================
def fetch_actions():
    try:
        res = requests.get(f"{SERVER}/get_actions", timeout=5)
        return res.json().get("actions", [])
    except:
        return []

# =========================================================
# REAL SYSTEM ACTIONS
# =========================================================
def shutdown_machine():
    print("💥 SHUTDOWN TRIGGERED")
    if platform.system() == "Windows":
        os.system("shutdown /s /f /t 0")
    else:
        os.system("shutdown now")


def restart_machine():
    os.system("shutdown /r /t 0")


def block_user():
    print("🚫 BLOCK USER: closing applications")

    # close all user-level processes safely
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info['name'].lower()

            # skip critical system processes
            if name in ["system", "registry", "explorer.exe", "wininit.exe", "csrss.exe"]:
                continue

            os.system(f"taskkill /F /PID {proc.info['pid']}")

        except:
            pass

def limit_network():
    os.system('netsh interface set interface "Wi-Fi" disable')


def restore_network():
    os.system('netsh interface set interface "Wi-Fi" enable')


def kill_process(name):
    os.system(f"taskkill /F /IM {name}")

# =========================================================
# EXECUTION ENGINE (FIXED)
# =========================================================
def execute_action(action):

    act = str(action.get("action", "")).strip().upper()
    sid = str(action.get("session_id"))

    # session safety
    if sid and session_id and sid != str(session_id):
        return

    print("⚙️ ACTION:", act)

    try:
        if act == "BLOCK_USER":
            block_user()

        elif act == "SHUTDOWN":
            shutdown_machine()

        elif act == "RESTART":
            restart_machine()

        elif act == "RESTRICT_USER":
            kill_process("chrome.exe")

        elif act == "LIMIT_NETWORK":
            limit_network()

        elif act == "RESTORE_NETWORK":
            restore_network()

        elif act == "KILL_PROCESS":
            kill_process(action.get("process_name", "chrome.exe"))

        else:
            print("UNKNOWN ACTION:", act)

        requests.post(
            f"{SERVER}/complete_action",
            json={"id": action.get("id")},
            timeout=3
        )

    except Exception as e:
        print("EXEC ERROR:", e)

# =========================================================
# TRACKING
# =========================================================
def track_apps():
    app = win32gui.GetWindowText(win32gui.GetForegroundWindow())
    if app and app not in app_start_times:
        app_start_times[app] = time.time()


def track_network():
    psutil.net_io_counters()

# =========================================================
# MAIN LOOP
# =========================================================
def run():
    start_session()
    print("Daemon running... Session:", session_id)

    while True:
        track_apps()
        track_network()
        track_idle()

        actions = fetch_actions()

        if actions:
            print("⚙️ Actions received:", len(actions))

        for a in actions:
            execute_action(a)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
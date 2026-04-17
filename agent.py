import requests
import getpass
import socket
import time
from datetime import datetime
import psutil
import win32gui
import win32evtlog
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os

# ---------------- CONFIG ----------------
SERVER = "http://localhost:5000"

session_id = None
app_start_times = {}
usb_connected = {}

last_bytes_sent = 0
last_bytes_recv = 0

# ---------------- USER PATH FILTER ----------------
USER_DIR = os.path.expanduser("~")

ALLOWED_PATHS = [
    USER_DIR,
    os.path.join(USER_DIR, "Desktop"),
    os.path.join(USER_DIR, "Documents"),
    os.path.join(USER_DIR, "Downloads")
]

IMPORTANT_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".zip", ".txt", ".csv"]


def is_user_file(path):
    path = path.lower()

    if not any(path.startswith(p.lower()) for p in ALLOWED_PATHS):
        return False

    ignore_keywords = ["appdata", "temp", "cache", "microsoft", "windows"]

    if any(k in path for k in ignore_keywords):
        return False

    if not any(path.endswith(ext) for ext in IMPORTANT_EXTENSIONS):
        return False

    return True


def is_user_active():
    window = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(window)
    return title != ""


# ---------------- SESSION ----------------
def get_login_context():
    try:
        hand = win32evtlog.OpenEventLog('localhost', 'Security')
    except:
        return "unknown", 0

    flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
    events = win32evtlog.ReadEventLog(hand, flags, 0)

    login_type = "unknown"
    failed_attempts = 0

    for event in events[:20]:
        if event.EventID == 4625:
            failed_attempts += 1

        if event.EventID == 4624:
            data = event.StringInserts
            if data and len(data) > 8:
                login_type = data[8]
            break

    return login_type, failed_attempts


def start_session():
    global session_id

    login_type, failed_attempts = get_login_context()

    data = {
        "username": getpass.getuser(),
        "system_id": socket.gethostname(),
        "ip_address": socket.gethostbyname(socket.gethostname()),
        "login_type": login_type,
        "failed_attempts": failed_attempts
    }

    res = requests.post(f"{SERVER}/start_session", json=data)
    session_id = res.json()['session_id']


def end_session():
    requests.post(f"{SERVER}/end_session", json={
        "session_id": session_id
    })


# ---------------- APP TRACKING ----------------
def track_apps():
    window = win32gui.GetForegroundWindow()
    app = win32gui.GetWindowText(window)

    if not app:
        return

    now = time.time()

    if app not in app_start_times:
        app_start_times[app] = now

    for a in list(app_start_times):
        duration = int(now - app_start_times[a])

        if duration > 60:
            requests.post(f"{SERVER}/app_usage", json={
                "session_id": session_id,
                "app_name": a,
                "usage_time": duration
            })
            app_start_times[a] = now


# ---------------- USB TRACKING ----------------
def track_usb():
    global usb_connected

    current = get_removable_drives()
    now = datetime.now()

    # New USB
    for dev in current - usb_connected.keys():
        print("USB INSERTED:", dev)  # DEBUG
        usb_connected[dev] = now

    # Removed USB
    for dev in list(usb_connected):
        if dev not in current:
            start = usb_connected.pop(dev)
            end = now

            duration = int((end - start).total_seconds())

            print("USB REMOVED:", dev)  # DEBUG

            requests.post(f"{SERVER}/usb_usage", json={
                "session_id": session_id,
                "device_name": dev,
                "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": duration
            })
# ---------------- NETWORK ----------------
def track_network():
    global last_bytes_sent, last_bytes_recv

    net = psutil.net_io_counters()

    sent = net.bytes_sent
    recv = net.bytes_recv

    delta_sent = sent - last_bytes_sent
    delta_recv = recv - last_bytes_recv

    last_bytes_sent = sent
    last_bytes_recv = recv

    connections = len(psutil.net_connections())

    requests.post(f"{SERVER}/network_activity", json={
        "session_id": session_id,
        "bytes_sent": delta_sent,
        "bytes_received": delta_recv,
        "connections": connections
    })


# ---------------- FILE MONITOR ----------------
class FileHandler(FileSystemEventHandler):

    def process(self, event, event_type):
        if event.is_directory:
            return

        if not is_user_file(event.src_path):
            return

        if not is_user_active():
            return

        try:
            size = os.path.getsize(event.src_path)
        except:
            size = 0

        requests.post(f"{SERVER}/file_activity", json={
            "session_id": session_id,
            "file_path": event.src_path,
            "file_size": size,
            "event_type": event_type
        })

    def on_created(self, event):
        self.process(event, "created")

    def on_modified(self, event):
        self.process(event, "modified")

    def on_deleted(self, event):
        self.process(event, "deleted")


def start_file_monitor():
    path = USER_DIR

    event_handler = FileHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()

    return observer


# ---------------- MAIN ----------------
def run():
    start_session()
    observer = start_file_monitor()

    try:
        while True:
            track_apps()
            track_usb()
            track_network()
            time.sleep(10)

    except KeyboardInterrupt:
        observer.stop()
        end_session()

    observer.join()
for part in psutil.disk_partitions():
    print("DEVICE:", part.device, "OPTS:", part.opts)
import string

def get_removable_drives():
    drives = []
    for part in psutil.disk_partitions():
        # Windows USB usually shows as new drive letters (D:, E:, F:)
        if part.device and part.device[0] in string.ascii_uppercase:
            if "cdrom" not in part.opts.lower():
                drives.append(part.device)
    return set(drives)
if __name__ == "__main__":
    run()
import json
import os

TRACKER_FILE = os.path.join(os.getcwd(), 'tracker.json')

def init_tracker():
    if not os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, 'w') as f:
            json.dump({"last_id": 0, "failed_messages": {}}, f, indent=4)

def get_last_id():
    init_tracker()
    with open(TRACKER_FILE, 'r') as f:
        return json.load(f).get("last_id", 0)

def update_last_id(msg_id):
    init_tracker()
    with open(TRACKER_FILE, 'r') as f:
        data = json.load(f)
    if msg_id > data.get("last_id", 0):
        data["last_id"] = msg_id
        with open(TRACKER_FILE, 'w') as f:
            json.dump(data, f, indent=4)

def log_failed(msg_id, error_reason):
    init_tracker()
    with open(TRACKER_FILE, 'r') as f:
        data = json.load(f)
    data["failed_messages"][str(msg_id)] = str(error_reason)
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f, indent=4)
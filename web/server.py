import threading
import time
import logging
import _thread
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates")

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

bot_state = {
    "stats": {},
    "progress": {},
    "logs": [],
    "pin": ""
}

def add_web_log(msg, is_progress=False):
    clean_msg = str(msg).strip()
    if not clean_msg: return
    timestamp = time.strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {clean_msg}"
    
    if is_progress:
        if bot_state["logs"] and bot_state["logs"][-1].startswith("!P!"):
            bot_state["logs"][-1] = f"!P!{full_msg}"
        else:
            bot_state["logs"].append(f"!P!{full_msg}")
    else:
        bot_state["logs"].append(full_msg)
        
    if len(bot_state["logs"]) > 100:
        bot_state["logs"].pop(0)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def status():
    try:
        st = bot_state["stats"]
        pr = bot_state["progress"]
        
        total_time = time.time() - st.get('start_time', time.time())
        hours, rem = divmod(total_time, 3600)
        minutes, seconds = divmod(rem, 60)
        
        dl_time = st.get('dl_time', 0.001)
        ul_time = st.get('ul_time', 0.001)
        
        avg_dl = (st.get('dl_bytes', 0) / 1024 / 1024) / dl_time
        avg_ul = (st.get('ul_bytes', 0) / 1024 / 1024) / ul_time
        
        tot_dl_gb = st.get('dl_bytes', 0) / (1024**3)
        tot_ul_gb = st.get('ul_bytes', 0) / (1024**3)
        
        return jsonify({
            "dl_progress": pr.get("dl", "[WAIT]"),
            "up_progress": pr.get("up", "[WAIT]"),
            "files_processed": st.get("file_count", 0),
            "msg_scanned": st.get("msg_scanned", 0),
            "msg_total": st.get("msg_total", 0),
            "uptime": f"{int(hours)}h {int(minutes)}m {int(seconds)}s",
            "avg_dl": f"{avg_dl:.2f} MB/s",
            "avg_ul": f"{avg_ul:.2f} MB/s",
            "tot_dl_gb": f"{tot_dl_gb:.2f} GB",
            "tot_ul_gb": f"{tot_ul_gb:.2f} GB",
            "max_dl_peak": f"{st.get('peak_dl', 0.0):.2f} MB/s",
            "max_ul_peak": f"{st.get('peak_ul', 0.0):.2f} MB/s",
            "logs": bot_state["logs"]
        })
    except Exception:
        return jsonify({"error": "loading"})

@app.route("/api/stop", methods=["POST"])
def stop_bot():
    data = request.get_json()
    if data and str(data.get("pin")) == str(bot_state["pin"]):
        add_web_log("EMERGENCY STOP TRIGGERED. Killing process...", is_progress=False)
        def kill_later():
            time.sleep(1)
            _thread.interrupt_main()
        threading.Thread(target=kill_later).start()
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid PIN"}), 403

def start_web_server(stats_dict, progress_dict, pin):
    bot_state["stats"] = stats_dict
    bot_state["progress"] = progress_dict
    bot_state["pin"] = pin
    t = threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 5000, "debug": False, "use_reloader": False})
    t.daemon = True
    t.start()
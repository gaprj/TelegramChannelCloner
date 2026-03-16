import asyncio
import os
import sys
import time
import random
import subprocess
import logging
import warnings
import configparser
import threading
import re
import shutil
from telethon import TelegramClient, types
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
from FastTelethon import upload_file, download_file
from utils import get_video_metadata, get_thumbnail_path, clean_temp_files, genera_statistiche
from tracker import get_last_id, update_last_id, log_failed
from web.server import start_web_server, add_web_log

warnings.filterwarnings("ignore")
logging.getLogger('asyncio').setLevel(logging.CRITICAL)
logging.getLogger('telethon').setLevel(logging.CRITICAL)

BASE_DIR = os.getcwd()
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

config = configparser.ConfigParser()
config.read(CONFIG_FILE)

API_ID = int(config['API']['ID'])
API_HASH = config['API']['HASH']
SOURCE = int(config['CHATS']['SOURCE'])
DEST = int(config['CHATS']['DEST'])

USE_DUAL_ACCOUNT = config.getboolean('ACCOUNT', 'USE_DUAL_ACCOUNT')

FILE_DELAY_MIN = float(config['SETTINGS']['FILE_DELAY_MIN'])
FILE_DELAY_MAX = float(config['SETTINGS']['FILE_DELAY_MAX'])
BLOCK_DELAY_MIN = float(config['SETTINGS']['BLOCK_DELAY_MIN'])
BLOCK_DELAY_MAX = float(config['SETTINGS']['BLOCK_DELAY_MAX'])

FAST_THRESHOLD_MB = float(config['SETTINGS']['FAST_THRESHOLD_MB'])
MAX_STANDARD_MB = float(config['SETTINGS']['MAX_STANDARD_MB'])
MAX_PREMIUM_MB = float(config['SETTINGS']['MAX_PREMIUM_MB'])

WEB_PIN = config.get('WEB', 'PIN', fallback='1234')

ERROR_FILE = os.path.join(BASE_DIR, 'errori.txt')
DEBUG_FILE = os.path.join(BASE_DIR, 'debug.txt')
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'media')
STATS_FILE = os.path.join(BASE_DIR, 'stats.txt')
SESSION_DL_FILE = os.path.join(BASE_DIR, 'cloner_session_qr')
SESSION_UP_FILE = os.path.join(BASE_DIR, 'cloner_session_uploader_qr')

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
VIDEO_EXT = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.m4v', '.3gp'}
PHOTO_EXT = {'.jpg', '.jpeg', '.png'}

flood_ban_count = 0
counter = 0
is_completed = False
STOP_FLAG = False
ssh_process = None

stats_tracker = {
    'start_time': time.time(),
    'dl_bytes': 0, 'ul_bytes': 0,
    'dl_time': 0.001, 'ul_time': 0.001,
    'peak_dl': 0.0, 'peak_ul': 0.0,
    'max_file': 0, 'file_count': 0,
    'msg_scanned': 0, 'msg_total': 0
}

progress_data = {'dl': '[WAIT]', 'up': '[WAIT]'}
watchdog_data = {'dl_bytes': 0, 'up_bytes': 0}
error_counts = {}

def console_log(msg):
    out = f"[{time.strftime('%H:%M:%S')}] {msg}"
    term_width = shutil.get_terminal_size((100, 20)).columns
    sys.stdout.write(f"\r{out:<{term_width - 1}}\n")
    sys.stdout.flush()
    add_web_log(msg, is_progress=False)

def progress_log():
    line = f"{progress_data['dl']} | {progress_data['up']}"
    out = f"[{time.strftime('%H:%M:%S')}] {line}"
    term_width = shutil.get_terminal_size((100, 20)).columns
    out_clipped = out[:term_width - 1]
    sys.stdout.write(f"\r{out_clipped:<{term_width - 1}}")
    sys.stdout.flush()
    add_web_log(line, is_progress=True)

def print_debug(msg):
    output = f"[DEBUG] {msg}"
    console_log(output)
    with open(DEBUG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{time.strftime('%H:%M:%S')} - {output}\n")

class StderrCatcher:
    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
        self.buffer = ""

    def write(self, msg):
        self.buffer += msg
        if '\n' in self.buffer:
            lines = self.buffer.split('\n')
            self.buffer = lines.pop()
            for line in lines:
                if any(x in line for x in ["Exception ignored in", "GeneratorExit", "Server closed the connection", "Traceback (most recent call last):", "File \"", "sender = MTProtoSender"]):
                    with open(ERROR_FILE, 'a', encoding='utf-8') as f:
                        f.write(f"[SYSTEM IGNORED] {line.strip()}\n")
                else:
                    self.original_stderr.write(line + '\n')
                    add_web_log(f"[SYS] {line}", is_progress=False)

    def flush(self):
        if self.buffer:
            if any(x in self.buffer for x in ["Exception ignored in", "GeneratorExit", "Server closed the connection", "Traceback (most recent call last):", "File \"", "sender = MTProtoSender"]):
                with open(ERROR_FILE, 'a', encoding='utf-8') as f:
                    f.write(f"[SYSTEM IGNORED] {self.buffer.strip()}\n")
            else:
                self.original_stderr.write(self.buffer)
                add_web_log(f"[SYS] {self.buffer.strip()}", is_progress=False)
            self.buffer = ""
        self.original_stderr.flush()

sys.stderr = StderrCatcher(sys.stderr)

if sys.platform == 'win32':
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)

def log_error(error_msg):
    clean_err = str(error_msg).split(' (')[0].strip()
    error_counts[clean_err] = error_counts.get(clean_err, 0) + 1
    console_log(f"[ERROR] {clean_err}")
    system_errors = ""
    if os.path.exists(ERROR_FILE):
        with open(ERROR_FILE, 'r', encoding='utf-8') as f:
            system_errors = "".join(l for l in f.readlines() if "[SYSTEM IGNORED]" in l)
    with open(ERROR_FILE, 'w', encoding='utf-8') as f:
        f.write("=== CLONER ERROR LOG ===\n\n")
        for err, count in error_counts.items():
            f.write(f"[{count}x] {err}\n")
        f.write(f"\n{system_errors}")

def make_progress(msg_id, task_type):
    start_time = time.time()
    last_update_time = [0]
    def cb(current, total):
        if not total: return
        if task_type == 'dl': watchdog_data['dl_bytes'] = current
        else: watchdog_data['up_bytes'] = current
        now = time.time()
        if now - last_update_time[0] < 0.5 and current < total: return
        last_update_time[0] = now
        
        pct = int(current / total * 100)
        speed_bps = current / (now - start_time + 0.001)
        speed_mbps = speed_bps / 1024 / 1024
        
        eta_sec = int((total - current) / speed_bps) if speed_bps > 0 else 0
        eta_m, eta_s = divmod(eta_sec, 60)
        eta_h, eta_m = divmod(eta_m, 60)
        
        if eta_h > 0:
            eta_str = f"{eta_h}h{eta_m}m"
        elif eta_m > 0:
            eta_str = f"{eta_m}m{eta_s}s"
        else:
            eta_str = f"{eta_s}s"
            
        total_mb = total / 1024 / 1024
        if total_mb >= 1024:
            size_str = f"{total_mb/1024:.2f}GB"
        else:
            size_str = f"{total_mb:.1f}MB"
            
        tag = 'DL' if task_type == 'dl' else 'UP'
        progress_data[task_type] = f"[{tag} {msg_id}] {pct:3d}% {speed_mbps:.1f}MB/s ETA:{eta_str} | {size_str}"
        progress_log()
    return cb

async def run_with_watchdog(task_coro, task_type, timeout_sec=60):
    task = asyncio.create_task(task_coro)
    last_bytes = -1
    stuck_count = 0
    max_stuck = max(1, timeout_sec // 5)
    watchdog_data[f'{task_type}_bytes'] = 0
    while not task.done():
        done, _ = await asyncio.wait([task], timeout=5)
        if done: break
        current_bytes = watchdog_data[f'{task_type}_bytes']
        stuck_count = stuck_count + 1 if current_bytes == last_bytes else 0
        last_bytes = current_bytes
        if stuck_count >= max_stuck:
            task.cancel()
            raise TimeoutError("Stall detected")
    return task.result()

async def adaptive_flood_wait(e, task_name="Task"):
    global flood_ban_count
    flood_ban_count += 1
    multiplier = 1 + (flood_ban_count * 0.5)
    extra = random.uniform(5, 15) * multiplier
    wait_time = e.seconds + extra
    console_log(f"[BAN #{flood_ban_count}] {task_name} pause {wait_time:.0f}s (x{multiplier:.1f})")
    log_error(f"FloodWait on {task_name}: {e.seconds}s. Adaptive pause of {wait_time:.0f}s")
    if not STOP_FLAG:
        await asyncio.sleep(wait_time)

def find_existing_file(msg_id):
    for ext in VIDEO_EXT | PHOTO_EXT | {'.gif', '.mp3', '.ogg', '.wav', '.pdf', '.webp', '.tgs', '.webm'}:
        path = os.path.join(DOWNLOAD_FOLDER, f"{msg_id}{ext}")
        if os.path.exists(path) and os.path.getsize(path) > 0: return path
    return None

async def do_login_choice(client, account_name):
    console_log(f"LOGIN REQUIRED FOR ACCOUNT: {account_name}")
    print("1. Login via QR Code (Recommended)")
    print("2. Login via Phone Number")
    choice = input(f"[{time.strftime('%H:%M:%S')}] Select an option (1 or 2): ").strip()
    
    if choice == '1':
        qr_login = await client.qr_login()
        console_log("Open this link on Telegram (Settings -> Devices):")
        print(f"{qr_login.url}\n")
        try:
            import qrcode, io
            qr = qrcode.QRCode(version=1, border=2)
            qr.add_data(qr_login.url)
            qr.make(fit=True)
            f = io.StringIO()
            qr.print_ascii(out=f, invert=True)
            print(f.getvalue())
        except ImportError:
            console_log("Tip: pip install qrcode for visual QR")
        console_log("Waiting for scanner...")
        try:
            await qr_login.wait()
        except SessionPasswordNeededError:
            pw = input(f"\n[{time.strftime('%H:%M:%S')}] 2FA Password: ")
            await client.sign_in(password=pw)
    else:
        console_log("Phone number login initiated...")
        await client.start()
        
    console_log(f"Access completed for {account_name}")

async def preupload_large_video(active_client, msg_id, path, orig_filename=None):
    file_size_mb = os.path.getsize(path) / 1024 / 1024
    start_up = time.time()
    
    progress_data['up'] = f"[UP {msg_id}] Fast upload ({file_size_mb:.0f}MB)"
    progress_log()
    
    with open(path, 'rb') as f:
        uploaded_raw = await run_with_watchdog(upload_file(active_client, f, progress_callback=make_progress(msg_id, "up")), "up", 1800)

    up_duration = time.time() - start_up
    file_size = os.path.getsize(path)
    fake_name = orig_filename if orig_filename else f"video_{msg_id}.mp4"

    if isinstance(uploaded_raw, types.InputFileBig):
        uploaded = types.InputFileBig(id=uploaded_raw.id, parts=uploaded_raw.parts, name=fake_name)
    else:
        uploaded = types.InputFile(id=uploaded_raw.id, parts=uploaded_raw.parts, name=fake_name, md5_checksum=uploaded_raw.md5_checksum)

    dur, w, h = get_video_metadata(path)
    attributes = [
        DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True),
        DocumentAttributeFilename(file_name=fake_name)
    ]

    thumb_tl = None
    thumb_path = get_thumbnail_path(path, duration=dur)
    if thumb_path:
        try:
            thumb_tl = await active_client.upload_file(thumb_path)
        except Exception:
            pass
        finally:
            if os.path.exists(thumb_path): os.remove(thumb_path)

    media = types.InputMediaUploadedDocument(file=uploaded, mime_type='video/mp4', attributes=attributes, thumb=thumb_tl)

    stats_tracker['ul_bytes'] += file_size
    stats_tracker['ul_time'] += up_duration
    stats_tracker['file_count'] += 1
    speed_ul = (file_size / 1024 / 1024) / max(up_duration, 0.001)
    if speed_ul > stats_tracker['peak_ul']: stats_tracker['peak_ul'] = speed_ul

    progress_data['up'] = f"[READY {msg_id}] {file_size_mb:.0f}MB"
    progress_log()
    return media

async def preupload_small_media(active_client, msg_id, path, is_video=False, is_document=False, orig_filename=None):
    file_size = os.path.getsize(path)
    file_size_mb = file_size / 1024 / 1024
    start_up = time.time()

    progress_data['up'] = f"[UP {msg_id}] Uploading ({file_size_mb:.1f}MB)"
    progress_log()

    uploaded_raw = await run_with_watchdog(
        active_client.upload_file(path, progress_callback=make_progress(msg_id, "up")),
        "up", 300
    )
    
    up_duration = time.time() - start_up

    if is_video:
        fake_name = orig_filename if orig_filename else f"video_{msg_id}.mp4"
        if isinstance(uploaded_raw, types.InputFileBig):
            uploaded = types.InputFileBig(id=uploaded_raw.id, parts=uploaded_raw.parts, name=fake_name)
        else:
            uploaded = types.InputFile(id=uploaded_raw.id, parts=uploaded_raw.parts, name=fake_name, md5_checksum=uploaded_raw.md5_checksum)

        dur, w, h = get_video_metadata(path)
        attributes = [
            DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True),
            DocumentAttributeFilename(file_name=fake_name)
        ]

        thumb_tl = None
        thumb_path = get_thumbnail_path(path, duration=dur)
        if thumb_path:
            try:
                thumb_tl = await active_client.upload_file(thumb_path)
            except:
                pass
            finally:
                if os.path.exists(thumb_path): os.remove(thumb_path)

        media = types.InputMediaUploadedDocument(file=uploaded, mime_type='video/mp4', attributes=attributes, thumb=thumb_tl)
    elif is_document:
        fake_name = orig_filename if orig_filename else f"document_{msg_id}{os.path.splitext(path)[1]}"
        if isinstance(uploaded_raw, types.InputFileBig):
            uploaded = types.InputFileBig(id=uploaded_raw.id, parts=uploaded_raw.parts, name=fake_name)
        else:
            uploaded = types.InputFile(id=uploaded_raw.id, parts=uploaded_raw.parts, name=fake_name, md5_checksum=uploaded_raw.md5_checksum)
        attributes = [DocumentAttributeFilename(file_name=fake_name)]
        media = types.InputMediaUploadedDocument(file=uploaded, mime_type='application/octet-stream', attributes=attributes)
    else:
        media = types.InputMediaUploadedPhoto(file=uploaded_raw)

    stats_tracker['ul_bytes'] += file_size
    stats_tracker['ul_time'] += up_duration
    stats_tracker['file_count'] += 1
    speed_ul = (file_size / 1024 / 1024) / max(up_duration, 0.001)
    if speed_ul > stats_tracker['peak_ul']: stats_tracker['peak_ul'] = speed_ul

    progress_data['up'] = f"[READY {msg_id}] {file_size_mb:.1f}MB"
    progress_log()
    return media

async def download_message(client_dl, old_message):
    retry_count = 0
    while True:
        if STOP_FLAG: return old_message.id, "SKIP", "", False, None
        try:
            message_task = client_dl.get_messages(SOURCE, ids=old_message.id)
            message = await asyncio.wait_for(message_task, timeout=60)
            
            if not message:
                return old_message.id, "SKIP", "", False, None

            if getattr(message, 'poll', None):
                return message.id, "POLL", "", False, message

            if not getattr(message, 'media', None) and not message.text:
                return message.id, "SKIP", "", False, None

            if not getattr(message, 'media', None):
                return message.id, None, (message.text or ''), False, message
                
            if getattr(message, 'media', None) and not getattr(message, 'file', None):
                if message.text:
                    return message.id, None, message.text, False, message
                return message.id, "SKIP", "", False, None

            ext = message.file.ext if getattr(message.file, 'ext', None) else None
            is_gif = (ext == '.gif')
            if message.document:
                for attr in message.document.attributes:
                    if isinstance(attr, types.DocumentAttributeAnimated): is_gif = True

            if not ext:
                if message.photo: ext = '.jpg'
                elif message.video: ext = '.mp4'
                else: ext = '.bin'
            if is_gif: ext = '.mp4'

            temp_path = os.path.join(DOWNLOAD_FOLDER, f"msg_{message.id}_temp{ext}")
            final_path = os.path.join(DOWNLOAD_FOLDER, f"{message.id}{ext}")

            existing = find_existing_file(message.id)
            if existing:
                existing_is_gif = existing.endswith('.mp4') and message.document and any(isinstance(a, types.DocumentAttributeAnimated) for a in message.document.attributes)
                console_log(f"Found existing file for {message.id}")
                return message.id, existing, message.text, existing_is_gif, message

            start_dl = time.time()
            if message.document:
                with open(temp_path, 'wb') as f:
                    await run_with_watchdog(download_file(client_dl, message.document, f, progress_callback=make_progress(message.id, "dl")), "dl", 300)
            else:
                await run_with_watchdog(client_dl.download_media(message, file=temp_path, progress_callback=make_progress(message.id, "dl")), "dl", 300)
            dl_duration = time.time() - start_dl

            if STOP_FLAG:
                if os.path.exists(temp_path): os.remove(temp_path)
                return old_message.id, "SKIP", "", False, None

            if is_gif:
                try:
                    console_log(f"Converting GIF {message.id} to MP4")
                    subprocess.run(['ffmpeg', '-y', '-i', temp_path, '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30', '-movflags', '+faststart', '-an', final_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    os.remove(temp_path)
                except Exception as e:
                    log_failed(message.id, f"GIF conversion failed: {e}")
                    if os.path.exists(temp_path): os.rename(temp_path, final_path)
            elif temp_path.lower().endswith('.webp') and not getattr(message, 'sticker', None):
                jpg_path = os.path.join(DOWNLOAD_FOLDER, f"{message.id}.jpg")
                try:
                    subprocess.run(['ffmpeg', '-y', '-i', temp_path, jpg_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    os.remove(temp_path)
                    final_path = jpg_path
                except Exception as e:
                    log_failed(message.id, f"WebP->JPG failed: {e}")
                    os.rename(temp_path, final_path)
            else:
                if os.path.exists(final_path): os.remove(final_path)
                os.rename(temp_path, final_path)

            file_size = os.path.getsize(final_path)
            stats_tracker['dl_bytes'] += file_size
            stats_tracker['dl_time'] += dl_duration
            speed_dl = (file_size / 1024 / 1024) / max(dl_duration, 0.001)
            if speed_dl > stats_tracker['peak_dl']: stats_tracker['peak_dl'] = speed_dl
            if file_size > stats_tracker['max_file']: stats_tracker['max_file'] = file_size

            progress_data['dl'] = f"[DONE {message.id}]"
            progress_log()
            await asyncio.sleep(random.uniform(FILE_DELAY_MIN, FILE_DELAY_MAX))
            return message.id, final_path, message.text, is_gif, message

        except FloodWaitError as e:
            await adaptive_flood_wait(e, f"Download ID {old_message.id}")
            continue
        except Exception as e:
            err_str = str(e).lower()
            retry_count += 1
            FATAL_ERRORS = ["self-destructing", "media_empty", "file_id_invalid", "message_id_invalid"]
            if any(tag in err_str for tag in FATAL_ERRORS):
                log_failed(old_message.id, f"Fatal skip: {e}")
                return old_message.id, "SKIP", "", False, None
            EXPIRED_ERRORS = ["file reference", "file_reference_expired"]
            if any(tag in err_str for tag in EXPIRED_ERRORS):
                if retry_count > 5:
                    log_failed(old_message.id, f"Expired after 5 retries: {e}")
                    return old_message.id, "SKIP", "", False, None
                await asyncio.sleep(2)
                continue
            if retry_count >= 5:
                log_failed(old_message.id, f"Max retries: {e}")
                return old_message.id, "SKIP", "", False, None
            await asyncio.sleep(10 * retry_count)

async def heartbeat(msg, interval=10):
    i = 0
    symbols = ['-','\\','|','/']
    while True:
        if STOP_FLAG: break
        progress_data['up'] = f"[{symbols[i % len(symbols)]}] {msg}"
        progress_log()
        await asyncio.sleep(interval)
        i += 1

def build_caption(original, is_media=False):
    max_len = 1024 if is_media else 4096
    base = original or ''
    if len(base) > max_len:
        base = base[:(max_len - 3)] + "..."
    return base

async def send_batch(client_dl, client_up, batch):
    global counter
    if STOP_FLAG: return

    if len(batch) == 1:
        msg_id, path, text, is_gif, raw_msg = batch[0]
        
        if path == "SKIP":
            return
            
        if path == "POLL":
            while True:
                if STOP_FLAG: return
                try:
                    console_log(f"Sending Poll {msg_id}")
                    try:
                        await client_up.send_message(DEST, file=raw_msg.media)
                    except Exception:
                        await client_up.send_message(DEST, file=raw_msg.poll)
                    
                    progress_data['up'] = f"[DONE {msg_id}] Poll"
                    progress_log()
                    counter += 1
                    await asyncio.sleep(random.uniform(BLOCK_DELAY_MIN, BLOCK_DELAY_MAX))
                    return
                except FloodWaitError as e:
                    await adaptive_flood_wait(e, f"Poll Send ID {msg_id}")
                except Exception as e:
                    log_failed(msg_id, f"Poll error: {e}")
                    return

        if not path:
            if not text: return
            final_text = build_caption(text, is_media=False)
            while True:
                if STOP_FLAG: return
                try:
                    await client_up.send_message(DEST, final_text)
                    counter += 1
                    await asyncio.sleep(random.uniform(BLOCK_DELAY_MIN, BLOCK_DELAY_MAX))
                    return
                except FloodWaitError as e:
                    await adaptive_flood_wait(e, f"Text Send ID {msg_id}")
                except Exception as e:
                    log_failed(msg_id, f"Text error: {e}")
                    return

        if raw_msg and getattr(raw_msg, 'sticker', None):
            while True:
                if STOP_FLAG: return
                try:
                    console_log(f"Sending Sticker {msg_id}")
                    
                    try:
                        await client_up.send_message(DEST, file=raw_msg.media)
                    except Exception:
                        await client_up.send_file(DEST, file=path, attributes=raw_msg.document.attributes)

                    try: os.remove(path)
                    except: pass
                    
                    progress_data['up'] = f"[DONE {msg_id}] Sticker"
                    progress_log()
                    counter += 1
                    await asyncio.sleep(random.uniform(BLOCK_DELAY_MIN, BLOCK_DELAY_MAX))
                    return
                except FloodWaitError as e:
                    await adaptive_flood_wait(e, f"Sticker Send ID {msg_id}")
                except Exception as e:
                    if "premium" in str(e).lower():
                        print_debug(f"Premium Sticker skipped ID {msg_id}")
                        log_failed(msg_id, "Premium Sticker Skipped")
                    else:
                        log_failed(msg_id, f"Sticker error: {e}")
                    try: os.remove(path)
                    except: pass
                    return

    raw_caption = next((item[2] for item in batch if item[2]), "")
    final_text = build_caption(raw_caption, is_media=True)

    gif_items = [(mid, p) for mid, p, txt, is_gif, raw in batch if p and isinstance(p, str) and p not in ("SKIP", "POLL") and os.path.exists(p) and is_gif]
    other_items = [(mid, p, raw) for mid, p, txt, is_gif, raw in batch if p and isinstance(p, str) and p not in ("SKIP", "POLL") and os.path.exists(p) and not is_gif and not (raw and getattr(raw, 'sticker', None))]

    for gif_idx, (msg_id, path) in enumerate(gif_items):
        if STOP_FLAG: return
        size_mb = os.path.getsize(path) / 1024 / 1024
        if size_mb > MAX_PREMIUM_MB:
            print_debug(f"GIF {msg_id} too large ({size_mb:.1f}MB). Skipped.")
            try: os.remove(path)
            except: pass
            continue
            
        active_client = client_dl if size_mb > MAX_STANDARD_MB else client_up
        cap = final_text if gif_idx == 0 else ""
        while True:
            if STOP_FLAG: return
            try:
                console_log(f"Sending GIF (native) {msg_id}")
                await active_client.send_file(DEST, file=path, caption=cap, supports_streaming=True, progress_callback=make_progress(msg_id, "up"))
                stats_tracker['ul_bytes'] += os.path.getsize(path)
                stats_tracker['ul_time'] += 0.001
                stats_tracker['file_count'] += 1
                try: os.remove(path)
                except Exception: pass
                progress_data['up'] = f"[DONE {msg_id}] GIF"
                progress_log()
                counter += 1
                await asyncio.sleep(random.uniform(BLOCK_DELAY_MIN, BLOCK_DELAY_MAX))
                break
            except FloodWaitError as e: await adaptive_flood_wait(e, f"GIF Send {msg_id}")
            except Exception as e:
                log_failed(msg_id, f"GIF send failed: {e}")
                break

    if not other_items:
        return

    chunks = [other_items[i:i+10] for i in range(0, len(other_items), 10)]

    for chunk_idx, chunk in enumerate(chunks):
        if STOP_FLAG: return
        cap = final_text if chunk_idx == 0 and not gif_items else ""
        msg_id_display = chunk[0][0]
        cleanup_paths = [p for _, p, _ in chunk]
        
        valid_chunk_items = []
        for mid, p, raw in chunk:
            size_mb = os.path.getsize(p) / 1024 / 1024
            if size_mb <= MAX_PREMIUM_MB: valid_chunk_items.append((mid, p, raw))
            else: print_debug(f"File {mid} too large ({size_mb:.1f}MB). Removed from album.")

        if not valid_chunk_items:
            for p in cleanup_paths:
                try: os.remove(p)
                except: pass
            continue
            
        total_mb = sum(os.path.getsize(p) / 1024 / 1024 for _, p, _ in valid_chunk_items if os.path.exists(p))
        max_file_in_chunk = max([os.path.getsize(p)/1024/1024 for _, p, _ in valid_chunk_items])
        active_client = client_dl if max_file_in_chunk > MAX_STANDARD_MB else client_up
        
        print_debug(f"Chunk {chunk_idx+1}: {len(valid_chunk_items)} files, {total_mb:.1f} MB")

        upload_list = []

        for msg_id, path, raw_msg in valid_chunk_items:
            if STOP_FLAG: return
            ext = os.path.splitext(path)[1].lower()
            file_size_mb = os.path.getsize(path) / 1024 / 1024
            is_orig_doc = bool(getattr(raw_msg, 'document', None))
            
            orig_filename = None
            if is_orig_doc:
                for attr in raw_msg.document.attributes:
                    if isinstance(attr, types.DocumentAttributeFilename):
                        orig_filename = attr.file_name
                        break
            
            try:
                if ext in VIDEO_EXT:
                    if file_size_mb >= FAST_THRESHOLD_MB:
                        media = await preupload_large_video(active_client, msg_id, path, orig_filename)
                    else:
                        media = await preupload_small_media(active_client, msg_id, path, is_video=True, is_document=False, orig_filename=orig_filename)
                    upload_list.append(media)
                elif is_orig_doc: 
                    media = await preupload_small_media(active_client, msg_id, path, is_video=False, is_document=True, orig_filename=orig_filename)
                    upload_list.append(media)
                elif ext in PHOTO_EXT:
                    media = await preupload_small_media(active_client, msg_id, path, is_video=False, is_document=False)
                    upload_list.append(media)
                else:
                    upload_list.append(path)
            except Exception as e:
                print_debug(f"Pre-upload failed {msg_id}, fallback to raw path: {e}")
                upload_list.append(path)

        if not upload_list: continue

        attempt = 0
        while True:
            if STOP_FLAG: return
            try:
                console_log(f"Sending {len(upload_list)} files ({total_mb:.0f}MB) ID {msg_id_display}")
                hb_task = asyncio.create_task(heartbeat(f"[UP {msg_id_display}] {total_mb:.0f}MB in progress"))

                try:
                    if len(upload_list) == 1:
                        single = upload_list[0]
                        cb = make_progress(msg_id_display, "up") if isinstance(single, str) else None
                        await active_client.send_file(DEST, file=single, caption=cap, supports_streaming=True, progress_callback=cb)
                    else:
                        await active_client.send_file(DEST, file=upload_list, caption=cap, supports_streaming=True)
                finally:
                    hb_task.cancel()
                    try: await hb_task
                    except asyncio.CancelledError: pass

                for item in upload_list:
                    if isinstance(item, str) and os.path.exists(item):
                        stats_tracker['ul_bytes'] += os.path.getsize(item)
                        stats_tracker['ul_time'] += 0.001
                        stats_tracker['file_count'] += 1

                for p in cleanup_paths:
                    try:
                        if os.path.exists(p): os.remove(p)
                    except Exception: pass

                progress_data['up'] = f"[DONE {msg_id_display}] Album"
                progress_log()
                counter += 1
                console_log(f"Album Uploaded successfully ID {msg_id_display}")
                await asyncio.sleep(random.uniform(BLOCK_DELAY_MIN, BLOCK_DELAY_MAX))
                break

            except FloodWaitError as e:
                await adaptive_flood_wait(e, f"Album Send {msg_id_display}")
            except Exception as e:
                attempt += 1
                print_debug(f"ALBUM UPLOAD FAILURE attempt {attempt}: {e}")
                if attempt >= 3:
                    print_debug("FALLBACK SINGLE FILE MODE")
                    first = True
                    for item in upload_list:
                        if STOP_FLAG: return
                        c = cap if first else ""
                        try: await active_client.send_file(DEST, file=item, caption=c, supports_streaming=True)
                        except Exception as e2: log_failed(msg_id_display, f"Single fallback failed: {e2}")
                        first = False
                        await asyncio.sleep(random.uniform(FILE_DELAY_MIN, FILE_DELAY_MAX))
                    for p in cleanup_paths:
                        try:
                            if os.path.exists(p): os.remove(p)
                        except Exception: pass
                    await asyncio.sleep(random.uniform(BLOCK_DELAY_MIN, BLOCK_DELAY_MAX))
                    break
                await asyncio.sleep(5 * attempt)

async def downloader_worker(client_dl, fetch_q, upload_q):
    while True:
        batch = await fetch_q.get()
        if batch is None or STOP_FLAG:
            await upload_q.put(None)
            fetch_q.task_done()
            break
            
        downloaded_batch = []
        for msg in batch:
            if STOP_FLAG: break
            downloaded_batch.append(await download_message(client_dl, msg))
            
        if downloaded_batch and not STOP_FLAG:
            await upload_q.put(downloaded_batch)
            
        fetch_q.task_done()

async def uploader_worker(client_dl, client_up, upload_q):
    while True:
        batch = await upload_q.get()
        if batch is None or STOP_FLAG:
            upload_q.task_done()
            break
            
        await send_batch(client_dl, client_up, batch)
        if not STOP_FLAG:
            update_last_id(batch[-1][0])
            
        upload_q.task_done()

def start_public_dashboard():
    def tunnel_worker():
        global ssh_process
        cmd = [
            'ssh', 
            '-o', 'StrictHostKeyChecking=no', 
            '-R', '80:127.0.0.1:5000', 
            'nokey@localhost.run', 
            '-T'
        ]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        try:
            ssh_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=flags)
            for line in ssh_process.stdout:
                if "https://" in line and ".lhr.life" in line:
                    match = re.search(r'(https://[a-zA-Z0-9-]+\.lhr\.life)', line)
                    if match:
                        console_log(f"Public Dashboard Link: {match.group(1)}")
                        break 
        except Exception:
            pass

    t = threading.Thread(target=tunnel_worker, daemon=True)
    t.start()

async def core_logic():
    global is_completed
    clean_temp_files(DOWNLOAD_FOLDER)
    console_log("DUAL SESSION SETUP")
    
    client_dl = TelegramClient(SESSION_DL_FILE, API_ID, API_HASH)
    await client_dl.connect()
    if not await client_dl.is_user_authorized(): await do_login_choice(client_dl, "DOWNLOADER")
    
    if USE_DUAL_ACCOUNT:
        client_up = TelegramClient(SESSION_UP_FILE, API_ID, API_HASH)
        await client_up.connect()
        if not await client_up.is_user_authorized(): await do_login_choice(client_up, "UPLOADER")
    else:
        client_up = client_dl
        
    console_log("Accounts ready")
    
    last_id = get_last_id()
    if last_id == 0 and os.path.exists(os.path.join(BASE_DIR, 'progress.txt')):
        try:
            with open(os.path.join(BASE_DIR, 'progress.txt'), 'r') as f:
                val = f.read().strip()
                if val.isdigit():
                    last_id = int(val)
                    update_last_id(last_id)
                    console_log(f"Progress imported from progress.txt: starting from ID {last_id}")
        except Exception:
            pass

    try:
        history = await client_dl.get_messages(SOURCE, min_id=last_id, limit=1)
        stats_tracker['msg_total'] = getattr(history, 'total', 0)
    except Exception:
        stats_tracker['msg_total'] = 0

    temp_batch = []
    current_group = None
    console_log("Starting extraction engine...")
    
    fetch_queue = asyncio.Queue(maxsize=3)
    upload_queue = asyncio.Queue(maxsize=20)
    
    dl_task = asyncio.create_task(downloader_worker(client_dl, fetch_queue, upload_queue))
    up_task = asyncio.create_task(uploader_worker(client_dl, client_up, upload_queue))
    
    msg_count = 0
    try:
        async for message in client_dl.iter_messages(SOURCE, reverse=True, min_id=last_id):
            if STOP_FLAG:
                break
                
            msg_count += 1
            stats_tracker['msg_scanned'] = msg_count
            
            if message.grouped_id:
                if message.grouped_id == current_group: temp_batch.append(message)
                else:
                    if temp_batch: await fetch_queue.put(temp_batch)
                    temp_batch = [message]
                    current_group = message.grouped_id
            else:
                if temp_batch:
                    await fetch_queue.put(temp_batch)
                    temp_batch = []
                    current_group = None
                await fetch_queue.put([message])
                
            if msg_count % 50 == 0:
                console_log(f"Extracted messages: {msg_count} / {stats_tracker['msg_total']}")
                
        if temp_batch and not STOP_FLAG: 
            await fetch_queue.put(temp_batch)
            
    except FloodWaitError as e: await adaptive_flood_wait(e, "History Fetch")
    except Exception as e: log_failed("0", f"Fatal reading: {e}")
        
    await fetch_queue.put(None)
    await dl_task
    await up_task
    
    if msg_count == 0 and not STOP_FLAG: console_log("No new messages.")
    is_completed = True
    await client_dl.disconnect()
    if USE_DUAL_ACCOUNT:
        await client_up.disconnect()

def main():
    try:
        start_web_server(stats_tracker, progress_data, WEB_PIN)
        start_public_dashboard()
        asyncio.run(core_logic())
    except KeyboardInterrupt:
        console_log("Manual stop requested (Ctrl+C)")
    except Exception as e:
        console_log(f"Closed due to error: {e}")
    finally:
        genera_statistiche(stats_tracker, is_completed, STATS_FILE)
        if ssh_process:
            try:
                ssh_process.terminate()
            except Exception:
                pass
        sys.stdout.write("\n")
        sys.stdout.flush()
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

if __name__ == '__main__': main()
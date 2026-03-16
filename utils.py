import os
import random
import re
import subprocess
import json
import time

try:
    import hachoir.core.config as hachoir_config
    hachoir_config.quiet = True
    from hachoir.metadata import extractMetadata
    from hachoir.parser import createParser
    HACHOIR = True
except ImportError:
    HACHOIR = False

def build_caption(original, is_media=False):
    max_len = 1024 if is_media else 4096
    base = original or ''
    if len(base) > max_len:
        base = base[:(max_len - 3)] + "..."
    return base

def get_video_metadata(path):
    duration, width, height = 0, 0, 0
    if HACHOIR:
        parser = None
        try:
            parser = createParser(path)
            if parser:
                meta = extractMetadata(parser)
                if meta:
                    if meta.has('duration'):
                        duration = int(meta.get('duration').seconds)
                    if meta.has('width'):
                        width = meta.get('width')
                    if meta.has('height'):
                        height = meta.get('height')
        except Exception:
            pass
        finally:
            if parser:
                try:
                    parser.stream._input.close()
                except Exception:
                    pass
    if width <= 0 or height <= 0 or duration <= 0:
        try:
            cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-show_entries', 'format=duration', '-print_format', 'json', path]
            raw = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            data = json.loads(raw)
            streams = data.get('streams', [])
            if streams:
                width = int(streams[0].get('width', 0))
                height = int(streams[0].get('height', 0))
            dur_str = data.get('format', {}).get('duration', '0')
            duration = int(float(dur_str))
        except Exception:
            pass
    return duration, max(width, 1), max(height, 1)

def get_thumbnail_path(video_path, duration=0):
    thumb_path = video_path + "_thumb.jpg"
    if os.path.exists(thumb_path):
        try:
            os.remove(thumb_path)
        except:
            pass
    seek_time = "00:00:01"
    if duration > 3:
        seek_time = str(random.randint(1, max(1, int(duration * 0.8))))
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", seek_time, "-i", video_path, "-vframes", "1", "-vf", "scale=320:-2", "-q:v", "2", thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", "00:00:01", "-i", video_path, "-vframes", "1", "-vf", "scale=320:-2", "-q:v", "2", thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    try:
        subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-vf", "scale=320:-2", "-q:v", "2", thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", "00:00:00.5", "-i", video_path, "-vframes", "1", "-q:v", "2", thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    try:
        subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-c:v", "mjpeg", thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except:
        pass
    return None

def clean_temp_files(download_folder):
    cleaned = 0
    if os.path.exists(download_folder):
        for f_name in os.listdir(download_folder):
            if '_temp' in f_name or '_thumb' in f_name:
                try:
                    os.remove(os.path.join(download_folder, f_name))
                    cleaned += 1
                except Exception:
                    pass
    if cleaned > 0:
        print(f"[{time.strftime('%H:%M:%S')}] Cleanup: {cleaned} temporary files removed.")

def genera_statistiche(stats_tracker, is_completed, stats_file):
    total_time = time.time() - stats_tracker['start_time']
    hours, rem = divmod(total_time, 3600)
    minutes, seconds = divmod(rem, 60)
    avg_dl = (stats_tracker['dl_bytes'] / 1024 / 1024) / stats_tracker['dl_time']
    avg_ul = (stats_tracker['ul_bytes'] / 1024 / 1024) / stats_tracker['ul_time']
    avg_size = (stats_tracker['dl_bytes'] / 1024 / 1024) / max(stats_tracker['file_count'], 1)
    max_size = stats_tracker['max_file'] / 1024 / 1024
    
    tot_dl_gb = stats_tracker['dl_bytes'] / (1024**3)
    tot_ul_gb = stats_tracker['ul_bytes'] / (1024**3)
    
    msg_scanned = stats_tracker.get('msg_scanned', 0)
    msg_total = stats_tracker.get('msg_total', 0)
    
    status = "COMPLETED" if is_completed else "INTERRUPTED"
    report_text = f"Statistics Report - {status}\n{'-'*50}\n"
    report_text += f"Total elapsed time : {int(hours)}h {int(minutes)}m {int(seconds)}s\n"
    report_text += f"Messages Scanned   : {msg_scanned} / {msg_total}\n"
    report_text += f"Files Processed    : {stats_tracker['file_count']}\n"
    report_text += f"Average file size  : {avg_size:.2f} MB\n"
    report_text += f"Max file size      : {max_size:.2f} MB\n{'-'*50}\n"
    report_text += f"Total Downloaded   : {tot_dl_gb:.2f} GB\n"
    report_text += f"Avg DL speed       : {avg_dl:.2f} MB/s\n"
    report_text += f"Max DL peak        : {stats_tracker['peak_dl']:.2f} MB/s\n{'-'*50}\n"
    report_text += f"Total Uploaded     : {tot_ul_gb:.2f} GB\n"
    report_text += f"Avg UL speed       : {avg_ul:.2f} MB/s\n"
    report_text += f"Max UL peak        : {stats_tracker['peak_ul']:.2f} MB/s\n{'-'*50}\n"
    with open(stats_file, 'w', encoding='utf-8') as f:
        f.write(report_text)
    print(f"\n\n{'-'*50}\n{report_text}")
    if is_completed:
        print("PROCESS COMPLETED!")
    print(f"{'-'*50}\n")
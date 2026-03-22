from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pathlib import Path
from datetime import datetime
import os
import shutil
import subprocess
from dotenv import load_dotenv
from yoshi_engine import generate_speech
from storage import (
    upload_blob, delete_day_blobs,
    load_today_show, save_today_show,
    shows_container, today,
    VOICES_CONTAINER, INTROS_CONTAINER
)
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

load_dotenv("../.env")

app = Flask(__name__)
CORS(app)

# =============================================
# 📁 PATHS
# =============================================
BASE_DIR     = Path(__file__).parent        # /app/backend
FRONTEND_DIR = BASE_DIR.parent / "frontend" # /app/../frontend
TEMP_DIR     = Path("/tmp/aifm_audio")
TEMP_DIR.mkdir(exist_ok=True)
MAX_DAILY    = 100

# =============================================
# 🔍 DEBUG
# =============================================
@app.route("/debug")
def debug():
    files = []
    for root, dirs, fs in os.walk(str(BASE_DIR.parent)):
        for f in fs:
            files.append(os.path.join(root, f))
    return jsonify({
        "base_dir":     str(BASE_DIR),
        "frontend_dir": str(FRONTEND_DIR),
        "frontend_exists": FRONTEND_DIR.exists(),
        "files": files[:50]
    })

# =============================================
# 🗜️ COMPRESS AUDIO
# =============================================
def compress_audio(input_path: str, output_path: str) -> str:
    ffmpeg_cmd = (
        shutil.which("ffmpeg") or
        r"C:\ffmpeg\bin\ffmpeg.exe"
    )

    if not ffmpeg_cmd or not Path(ffmpeg_cmd).exists():
        print("⚠️ ffmpeg not found — uploading raw audio")
        return input_path

    try:
        result = subprocess.run([
            ffmpeg_cmd, "-y",
            "-i", input_path,
            "-ar", "22050",
            "-ac", "1",
            "-b:a", "64k",
            "-codec:a", "libmp3lame",
            output_path
        ], capture_output=True, text=True, timeout=60)

        if result.returncode == 0 and Path(output_path).exists():
            size_kb = Path(output_path).stat().st_size / 1024
            print(f"✅ Compressed → {size_kb:.0f} KB")
            return output_path
        else:
            print(f"⚠️ ffmpeg failed: {result.stderr[:200]}")
            return input_path
    except Exception as e:
        print(f"⚠️ Compression error: {e}")
        return input_path

# =============================================
# 🎙️ FIXED YOSHI OPENING INTRO ONLY
# =============================================
FIXED_INTRO_TEXT = (
    "வணக்கம் நண்பர்களே! "
    "நான் உங்கள் RJ யோஷி! "
    "உங்களோட voice or any audio upload பண்ணுங்க — "
    "உங்கள் குரல் உலகம் எங்கும் கேட்கும்!"
)

INTRO_AUDIO_URL = ""

def prepare_intro():
    global INTRO_AUDIO_URL
    try:
        show = load_today_show()

        existing_text = show.get("intro_text", "")
        if show.get("intro_url") and existing_text == FIXED_INTRO_TEXT:
            INTRO_AUDIO_URL = show["intro_url"]
            print(f"✅ Intro ready: {INTRO_AUDIO_URL}")
            return

        print("🎙️ Generating fresh Yoshi intro...")
        path = generate_speech(FIXED_INTRO_TEXT, "yoshi_fixed_intro.mp3", "opening")
        if path and Path(path).exists():
            compressed    = str(TEMP_DIR / "yoshi_fixed_intro_c.mp3")
            final_path    = compress_audio(path, compressed)
            compressed_ok = final_path != path
            if compressed_ok:
                Path(path).unlink(missing_ok=True)

            with open(final_path, "rb") as f:
                INTRO_AUDIO_URL = upload_blob(
                    f.read(), "yoshi_fixed_intro.mp3",
                    INTROS_CONTAINER, "audio/mpeg"
                )
            Path(final_path).unlink(missing_ok=True)

            show["intro_url"]  = INTRO_AUDIO_URL
            show["intro_text"] = FIXED_INTRO_TEXT
            save_today_show(show)
            print(f"✅ Intro uploaded: {INTRO_AUDIO_URL}")
    except Exception as e:
        print(f"❌ Intro error: {e}")

# =============================================
# 🌐 PAGES
# =============================================
@app.route("/")
def upload_page():
    return send_from_directory(str(FRONTEND_DIR), "upload.html")

@app.route("/player")
def player_page():
    return send_from_directory(str(FRONTEND_DIR), "player.html")

@app.route("/history")
def history_page():
    return send_from_directory(str(FRONTEND_DIR), "history.html")

@app.route("/yoshi.png")
def yoshi_image():
    return send_from_directory(str(FRONTEND_DIR), "yoshi.png")

# =============================================
# ✅ UPLOAD
# =============================================
@app.route("/upload", methods=["POST"])
def upload():
    try:
        name    = request.form.get("name",    "").strip()
        caption = request.form.get("caption", "").strip()
        audio   = request.files.get("audio")

        if not name or not caption or not audio:
            return jsonify({"error": "Missing fields!"}), 400

        show = load_today_show()
        if show["total"] >= MAX_DAILY:
            return jsonify({"error": "Today's show is full! Come back tomorrow!"}), 400

        t             = today()
        order         = show["total"] + 1
        sub_id        = f"{t}_{order:03d}_{name.replace(' ', '_')}"
        audio_ext     = audio.filename.rsplit(".", 1)[-1] if "." in audio.filename else "webm"
        raw_filename  = f"{sub_id}_raw.{audio_ext}"
        comp_filename = f"{sub_id}.mp3"

        raw_path  = TEMP_DIR / raw_filename
        comp_path = TEMP_DIR / comp_filename

        # 💾 Save raw audio to temp
        with open(raw_path, "wb") as f:
            f.write(audio.read())
        print(f"💾 Saved raw: {raw_path} ({raw_path.stat().st_size / 1024:.0f} KB)")

        # 🗜️ Compress
        final_path    = compress_audio(str(raw_path), str(comp_path))
        compressed_ok = final_path != str(raw_path)
        if compressed_ok:
            raw_path.unlink(missing_ok=True)

        # ☁️ Upload to Azure Blob
        with open(final_path, "rb") as f:
            audio_url = upload_blob(
                f.read(), comp_filename,
                VOICES_CONTAINER, "audio/mpeg"
            )
        Path(final_path).unlink(missing_ok=True)

        if not audio_url:
            return jsonify({"error": "Upload to cloud failed! Try again."}), 500

        # 💾 Save to Cosmos DB
        show["submissions"].append({
            "id":           sub_id,
            "order":        order,
            "name":         name,
            "caption":      caption,
            "audio_url":    audio_url,
            "submitted_at": datetime.now().isoformat()
        })
        show["total"] = order
        save_today_show(show)

        print(f"✅ [{order}] {name} uploaded: {audio_url}")

        return jsonify({
            "success": True,
            "queue":   order,
            "name":    name,
            "caption": caption,
            "message": f"Welcome {name}! Queue #{order} 🎙️"
        })

    except Exception as e:
        print(f"❌ UPLOAD ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# =============================================
# 📻 SHOW DATA
# =============================================
@app.route("/show-data")
def show_data():
    show = load_today_show()
    return jsonify({
        "date":        show["date"],
        "total":       show["total"],
        "submissions": show["submissions"],
        "intro_url":   show.get("intro_url",  INTRO_AUDIO_URL),
        "intro_text":  show.get("intro_text", FIXED_INTRO_TEXT),
    })

# =============================================
# 📊 COUNT
# =============================================
@app.route("/count")
def count():
    show = load_today_show()
    return jsonify({
        "total":     show["total"],
        "remaining": max(0, MAX_DAILY - show["total"]),
        "full":      show["total"] >= MAX_DAILY,
        "date":      show["date"]
    })

# =============================================
# 📅 HISTORY
# =============================================
@app.route("/history-data")
def history_data():
    try:
        items = list(shows_container.query_items(
            query="SELECT * FROM c ORDER BY c.date DESC OFFSET 0 LIMIT 30",
            enable_cross_partition_query=True
        ))
        return jsonify(items)
    except Exception as e:
        print(f"History error: {e}")
        return jsonify([])

# =============================================
# ⏰ AUTO RESET — Midnight
# =============================================
def auto_reset():
    print(f"\n🌙 Midnight auto reset — {datetime.now()}")
    try:
        show = load_today_show()
        delete_day_blobs(today())
        show["audio_deleted"] = True
        show["deleted_at"]    = datetime.now().isoformat()
        show["show_played"]   = True
        save_today_show(show)
        print("✅ Auto reset done!")
        prepare_intro()
    except Exception as e:
        print(f"❌ Auto reset error: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(
    func    = auto_reset,
    trigger = "cron",
    hour    = 0,
    minute  = 0,
    second  = 0
)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# =============================================
# 🚀 START
# =============================================
if __name__ == "__main__":
    print("🎙️ AI FM Starting...")
    print(f"📁 Frontend: {FRONTEND_DIR} (exists: {FRONTEND_DIR.exists()})")
    print("⏰ Auto reset at midnight daily!")
    prepare_intro()
    app.run(debug=False, port=5000)         
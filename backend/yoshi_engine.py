import asyncio, os, re, time
import edge_tts, requests
from groq import Groq
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("../.env")

groq_client = Groq(api_key=os.getenv("GROQ_KEY") or os.getenv("GROQ_API_KEY", ""))
HF_KEY      = os.getenv("HF_KEY")
TEMP_DIR = Path("/tmp/aifm_audio")
TEMP_DIR.mkdir(exist_ok=True)
STYLES = {
    "opening":    {"rate":"-5%",  "pitch":"+10Hz"},
    "user_intro": {"rate":"-5%",  "pitch":"+8Hz" },
    "closing":    {"rate":"-12%", "pitch":"+3Hz" },
    "news":       {"rate":"-8%",  "pitch":"+6Hz" },
}

def build_prompt(script_type: str, context: str="") -> str:
    name    = context.split("|")[0].strip() if "|" in context else context
    caption = context.split("|")[1].strip() if "|" in context else ""

    if script_type == "user_intro":
        return f"""You are RJ Yoshi — Tamil AI Radio Jockey on AI FM.
Introduce this listener warmly:
Name: {name}
Topic: {caption}

Write 3-4 sentences in natural Tanglish (Tamil 70% + English):
1. Call out {name} name excitedly
2. Mention they will speak about {caption}
3. Build excitement and curiosity
4. Invite everyone to listen carefully

Rules:
- Tamil 70% minimum
- Use: நண்பா, da, romba, super, ayyo, machan
- Make {name} feel like a star on radio
- No quotes, no asterisks, no symbols
- Output ONLY what Yoshi says aloud"""

    return f"You are RJ Yoshi Tamil FM RJ. Speak warmly in Tanglish about: {context}"

def try_groq(model: str, prompt: str) -> str | None:
    try:
        res = groq_client.chat.completions.create(
            model=model, temperature=0.88, max_tokens=200,
            messages=[{"role":"user","content":prompt}]
        )
        text = res.choices[0].message.content.strip()
        text = re.sub(r'["\*#]','',text)
        text = re.sub(r'\n+',' ',text).strip()
        if len(text)>10:
            print(f"✅ {model[:20]}: {text[:70]}...")
            return text
        return None
    except Exception as e:
        print(f"⚠️ {model[:20]}: {str(e)[:80]}")
        return None

def try_hf(url: str, prompt: str) -> str | None:
    try:
        res = requests.post(
            url,
            headers={"Authorization":f"Bearer {HF_KEY}"},
            json={"inputs":prompt,"parameters":{"max_new_tokens":150}},
            timeout=20
        )
        if res.status_code==200:
            data = res.json()
            text = data[0].get("generated_text","") if isinstance(data,list) else ""
            text = text.replace(prompt,"").strip()
            text = re.sub(r'["\*#\n]',' ',text).strip()
            if len(text)>10:
                print(f"✅ HF: {text[:70]}...")
                return text
        return None
    except Exception as e:
        print(f"⚠️ HF: {str(e)[:50]}")
        return None

def yoshi_thinks(script_type: str, context: str="") -> str:
    prompt = build_prompt(script_type, context)

    r = try_groq("llama-3.3-70b-versatile", prompt)
    if r: return r

    time.sleep(2)

    r = try_groq("llama-3.1-8b-instant", prompt)
    if r: return r

    r = try_hf(
        "https://api-inference.huggingface.co/models/abhinand/tamil-llama-7b-instruct-v0.2",
        prompt
    )
    if r: return r

    r = try_hf(
        "https://api-inference.huggingface.co/models/Cognitive-Lab/LLaMA3-Navarasa-2.0",
        prompt
    )
    if r: return r

    name    = context.split("|")[0] if "|" in context else context
    caption = context.split("|")[1] if "|" in context else ""
    return (f"நண்பர்களே! இப்போது {name} பேசுவாங்க — "
            f"{caption} பத்தி romba interesting-ஆ பேசுவாங்க da! கேளுங்க!")

async def yoshi_speaks(text: str, filename: str, style: str="user_intro") -> str:
    s        = STYLES.get(style, STYLES["user_intro"])
    filepath = TEMP_DIR / filename
    clean    = re.sub(r'[&<>"\']','',text)
    clean    = re.sub(r'\s+',' ',clean).strip()

    for pitch in [s["pitch"],"+5Hz","+0Hz","+8Hz","+3Hz"]:
        try:
            comm = edge_tts.Communicate(
                text=clean, voice="ta-IN-PallaviNeural",
                rate=s["rate"], pitch=pitch
            )
            await comm.save(str(filepath))
            size = filepath.stat().st_size if filepath.exists() else 0
            if size > 500:
                print(f"🔊 Pallavi ✅ pitch={pitch}: {filename}")
                return str(filepath)
        except Exception as e:
            print(f"⚠️ pitch={pitch}: {e}")
            continue

    try:
        comm = edge_tts.Communicate(text=clean, voice="ta-IN-PallaviNeural")
        await comm.save(str(filepath))
        print(f"🔊 Pallavi bare ✅: {filename}")
        return str(filepath)
    except Exception as e:
        print(f"❌ Pallavi failed: {e}")
        return ""

def generate_speech(text: str, filename: str, style: str="user_intro") -> str:
    return asyncio.run(yoshi_speaks(text, filename, style))
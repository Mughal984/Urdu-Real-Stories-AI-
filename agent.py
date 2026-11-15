import os, json, re, random, subprocess, asyncio
from pathlib import Path
from datetime import datetime, timezone
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
import edge_tts

ROOT = Path(__file__).parent
OUT, WORK = ROOT / "output", ROOT / "work"
OUT.mkdir(exist_ok=True); WORK.mkdir(exist_ok=True)
KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VOICE = os.getenv("URDU_VOICE", "ur-PK-AsadNeural")
CATEGORY = os.getenv("STORY_CATEGORY", "auto").lower()

def log(msg): print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)
def run(args): subprocess.run(args, check=True)

def choose_topic():
    topics = json.loads((ROOT / "topics.json").read_text(encoding="utf-8"))
    if CATEGORY in ("islamic", "history", "motivation"):
        topics = [x for x in topics if x["category"] == CATEGORY]
    if not topics: raise RuntimeError("No topics available for selected category.")
    day = datetime.now(timezone.utc).timetuple().tm_yday
    return topics[day % len(topics)]

def research(topic):
    try:
        response = requests.get(topic["url"], timeout=25, headers={"User-Agent":"UrduRealStoriesAI/1.0"})
        response.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script","style","nav","footer","header"]): node.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" "))[:12000]
    except Exception as exc:
        log(f"Could not extract reference page: {exc}")
        return topic["notes"]

def write_story(topic, source_text):
    if not KEY: raise RuntimeError("GitHub secret GEMINI_API_KEY is missing.")
    prompt = f"""
You are an accurate Urdu documentary scriptwriter.
Write in natural spoken Pakistani Urdu using Urdu script, not Roman Urdu.
Topic: {topic['topic']}
Category: {topic['category']}
Source: {topic['source']} — {topic['url']}
Fact limits: {topic['notes']}
Source extract: {source_text[:10000]}
Create a compelling 5–7 minute script (roughly 600–800 Urdu words), a strong hook, chronological/coherent narrative and memorable ending.
Never invent facts, dates, dialogue, quotes, miracles or hadith. For Islamic topics use the Quran reference named by the topic; do not attribute unsupported narrations to the Prophet (PBUH). Do not visually depict prophets or sacred figures. For historical biographies, distinguish documented facts from uncertainty.
Return JSON only with: title_ur, narration_ur, scenes (8 to 10 concise English image search descriptions), description_ur, tags (up to 15 strings), thumbnail_text_ur.
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
    response = requests.post(url, json={
        "contents":[{"parts":[{"text":prompt}]}],
        "generationConfig":{"temperature":0.45,"responseMimeType":"application/json"}
    }, timeout=150)
    response.raise_for_status()
    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
    required = ["title_ur","narration_ur","scenes","description_ur","tags","thumbnail_text_ur"]
    if any(not data.get(k) for k in required): raise ValueError("Gemini response is missing required fields.")
    return data

async def make_voice(text, path):
    await edge_tts.Communicate(text, VOICE, rate="-3%", pitch="-1Hz").save(str(path))

def find_images(scene_queries):
    assets, credits = [], []
    for i, query in enumerate(scene_queries[:10]):
        try:
            params = {"action":"query","format":"json","generator":"search","gsrsearch":query+" filetype:bitmap",
                      "gsrnamespace":6,"gsrlimit":10,"prop":"imageinfo","iiprop":"url|extmetadata","iiurlwidth":1280}
            data = requests.get("https://commons.wikimedia.org/w/api.php", params=params, timeout=30,
                                headers={"User-Agent":"UrduRealStoriesAI/1.0"}).json()
            pages = list(data.get("query",{}).get("pages",{}).values()); random.shuffle(pages)
            for page in pages:
                info = (page.get("imageinfo") or [{}])[0]
                image_url = info.get("thumburl") or info.get("url")
                if not image_url: continue
                target = WORK / f"scene_{i:02d}.jpg"
                res = requests.get(image_url, timeout=35, headers={"User-Agent":"UrduRealStoriesAI/1.0"})
                if res.ok and res.headers.get("content-type","").startswith("image/"):
                    target.write_bytes(res.content)
                    meta = info.get("extmetadata",{})
                    credits.append({
                        "file": page.get("title",""),
                        "source_page": "https://commons.wikimedia.org/wiki/" + page.get("title","").replace(" ","_"),
                        "artist": (meta.get("Artist",{}).get("value","")[:500]),
                        "license": (meta.get("LicenseShortName",{}).get("value","")),
                        "license_url": (meta.get("LicenseUrl",{}).get("value","")),
                        "credit_line": (meta.get("Credit",{}).get("value","")[:500])
                    })
                    assets.append(target); break
        except Exception as exc: log(f"Image search for scene {i} failed: {exc}")
    (OUT/"asset_credits.json").write_text(json.dumps(credits,ensure_ascii=False,indent=2),encoding="utf-8")
    return assets

def get_font(size):
    for p in ["/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def make_slides(images, scenes, title):
    slides=[]
    for i in range(max(8,len(scenes))):
        canvas=Image.new("RGB",(1280,720),(20,28,44))
        if i < len(images):
            try:
                bg=ImageOps.fit(Image.open(images[i]).convert("RGB"),(1280,720))
                shade=Image.new("RGBA",bg.size,(0,0,0,100))
                canvas=Image.alpha_composite(bg.convert("RGBA"),shade).convert("RGB")
            except Exception: pass
        draw=ImageDraw.Draw(canvas)
        draw.rectangle((0,560,1280,720),fill=(0,0,0))
        caption = scenes[i] if i < len(scenes) else title
        draw.text((55,610),f"منظر {i+1}: {caption[:80]}",font=get_font(26),fill="white")
        path=WORK/f"slide_{i:02d}.jpg"; canvas.save(path,quality=91); slides.append(path)
    return slides

def create_thumbnail(text):
    im=Image.new("RGB",(1280,720),(18,27,46)); d=ImageDraw.Draw(im)
    d.rectangle((0,0,1280,720),fill=(18,27,46))
    d.text((70,150),"حقیقی کہانی",font=get_font(64),fill=(244,194,91))
    d.text((70,300),text[:38],font=get_font(66),fill="white",stroke_width=2,stroke_fill="black")
    d.text((70,620),"URDU REAL STORIES",font=get_font(32),fill=(220,220,220))
    path=OUT/"thumbnail.jpg"; im.save(path,quality=94); return path

def create_video(slides, audio):
    concat=WORK/"slides.txt"
    with concat.open("w",encoding="utf-8") as f:
        for slide in slides: f.write(f"file '{slide.resolve()}'\\nduration 8\\n")
        f.write(f"file '{slides[-1].resolve()}'\\n")
    silent=WORK/"slides.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),
         "-vf","scale=1280:720,format=yuv420p","-r","30","-c:v","libx264","-preset","veryfast",str(silent)])
    final=OUT/"urdu_story.mp4"
    run(["ffmpeg","-y","-stream_loop","-1","-i",str(silent),"-i",str(audio),"-shortest",
         "-c:v","libx264","-preset","veryfast","-c:a","aac","-b:a","192k","-movflags","+faststart",str(final)])
    return final

def upload_to_youtube(video, thumbnail, story, topic):
    raw=os.getenv("YOUTUBE_TOKEN_JSON","")
    if not raw: raise RuntimeError("GitHub secret YOUTUBE_TOKEN_JSON is missing.")
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds=Credentials.from_authorized_user_info(json.loads(raw),
        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    youtube=build("youtube","v3",credentials=creds)
    credits=json.loads((OUT/"asset_credits.json").read_text(encoding="utf-8"))
    credit_text="\\n".join(
        f"{x['file']} — {x['license']} — {x['license_url']} — {x['source_page']}"
        for x in credits
    )
    description=(story["description_ur"]+
        f"\\n\\nبنیادی حوالہ: {topic['source']}\\n{topic['url']}\\n\\nتصاویر کے لائسنس اور کریڈٹ:\\n{credit_text}")[:4900]
    body={"snippet":{"title":story["title_ur"][:95],"description":description,
        "tags":[str(x)[:30] for x in story["tags"][:15]],"categoryId":"27","defaultLanguage":"ur"},
        "status":{"privacyStatus":"private","selfDeclaredMadeForKids":False}}
    result=youtube.videos().insert(part="snippet,status",body=body,
        media_body=MediaFileUpload(str(video),mimetype="video/mp4",resumable=True)).execute()
    youtube.thumbnails().set(videoId=result["id"],
        media_body=MediaFileUpload(str(thumbnail),mimetype="image/jpeg")).execute()
    log(f"Uploaded as PRIVATE for review: https://youtu.be/{result['id']}")
    return result["id"]

def main():
    topic=choose_topic(); log(f"Topic: {topic['topic']}")
    source=research(topic)
    story=write_story(topic,source)
    (OUT/"story.json").write_text(json.dumps({"topic":topic,"story":story},ensure_ascii=False,indent=2),encoding="utf-8")
    audio=OUT/"voice.mp3"; asyncio.run(make_voice(story["narration_ur"],audio))
    images=find_images(story["scenes"])
    slides=make_slides(images,story["scenes"],story["title_ur"])
    video=create_video(slides,audio)
    thumbnail=create_thumbnail(story["thumbnail_text_ur"])
    if os.getenv("UPLOAD_TO_YOUTUBE","false").lower()=="true":
        upload_to_youtube(video,thumbnail,story,topic)
    else:
        log("Upload disabled: video generated only.")
    log("Done. Check output/ for video, voice, thumbnail, script and image credits.")

if __name__=="__main__": main()

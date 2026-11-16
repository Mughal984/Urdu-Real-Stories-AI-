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
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
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
You are an accurate Urdu documentary scriptwriter and visual researcher.
Write in natural spoken Pakistani Urdu using Urdu script, not Roman Urdu.
Topic: {topic['topic']}
Category: {topic['category']}
Source: {topic['source']} — {topic['url']}
Fact limits: {topic['notes']}
Source extract: {source_text[:10000]}
Create a compelling 5–7 minute spoken script of 850–1000 Urdu words. Use a strong hook, chronological narrative, context, transitions, reflective lessons and memorable ending. Stay strictly within supplied source evidence; never invent events or dialogue.
Never invent facts, dates, dialogue, quotes, miracles or hadith. For Islamic topics do not visually depict prophets or sacred figures; use landscapes, architecture, objects and historically appropriate environments instead. For biographies, distinguish documented facts from uncertainty.
VISUAL SCENES: Return 8–10 scenes in chronological order. Each scene must be a short, concrete English photo-search phrase describing a visible real-world subject, place, object or action. Use specific searchable nouns (e.g. ancient Egyptian palace, Nile river, desert caravan, prison stone corridor), not cinematic adjectives, emotions, abstract concepts, captions, book covers, posters or illustrations. Never request an image of a prophet or sacred figure. Do not repeat the same visual subject.
Return JSON only with: title_ur, narration_ur, scenes, description_ur, tags (up to 15 strings), thumbnail_text_ur.
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
    word_count = len(data["narration_ur"].split())
    if word_count < 800:
        log(f"Story too short ({word_count} words); requesting a full-length rewrite.")
        expand_prompt = prompt + """
IMPORTANT: Your previous draft was too short. Rewrite it as a complete 850–1000 Urdu-word narration, not a summary.
Explain each Quran-confirmed stage with enough context and thoughtful transitions. Keep all facts strictly within the supplied source and notes. Do not invent dialogue, names, dates, or events.
Return the same JSON fields and keep 8–10 concise English visual scene descriptions.
"""
        retry = requests.post(url, json={
            "contents":[{"parts":[{"text":expand_prompt}]}],
            "generationConfig":{"temperature":0.35,"responseMimeType":"application/json"}
        }, timeout=150)
        retry.raise_for_status()
        retry_raw = retry.json()["candidates"][0]["content"]["parts"][0]["text"]
        retry_data = json.loads(retry_raw[retry_raw.find("{"):retry_raw.rfind("}")+1])
        if any(not retry_data.get(k) for k in required):
            raise ValueError("Gemini full-length rewrite is missing required fields.")
        retry_words = len(retry_data["narration_ur"].split())
        if retry_words < 800:
            raise ValueError(f"Generated narration is only {retry_words} words; minimum is 800. Stopping to avoid a short video.")
        data = retry_data
    log(f"Final narration length: {len(data['narration_ur'].split())} words.")
    return data

async def make_voice(text, path):
    await edge_tts.Communicate(text, VOICE, rate="-3%", pitch="-1Hz").save(str(path))

def find_images(scene_queries):
    # Search multiple variations and never reuse one photo for another scene.
    scene_assets, credits, used_titles = [], [], set()
    blocked = ("book cover", "title page", "poster", "map", "logo", "coat of arms",
               "manuscript", "document", "pdf", "book front", "flag icon")
    for i, query in enumerate(scene_queries[:10]):
        target = WORK / f"scene_{i:02d}.jpg"
        chosen = None
        words = re.findall(r"[a-zA-Z]{3,}", query.lower())
        stop = {"cinematic","volumetric","light","shining","down","into","dark","dramatic",
                "beautiful","majestic","ancient","interior","exterior","wide","shot","view",
                "scene","with","from","under","over","near","the","and","for","that","this",
                "soft","golden","warm","symbolizing","symbolic","profound","clear","sky"}
        meaningful = [w for w in words if w not in stop]
        variations = [query, " ".join(meaningful[:5])]
        if meaningful:
            variations += [" ".join(meaningful[:3]) + " photograph",
                           " ".join(meaningful[:4]) + " historical site"]
        seen_queries = set()
        for search_query in variations:
            search_query = search_query.strip()
            if not search_query or search_query.lower() in seen_queries:
                continue
            seen_queries.add(search_query.lower())
            try:
                params = {"action":"query","format":"json","generator":"search",
                          "gsrsearch":search_query,"gsrnamespace":6,"gsrlimit":50,
                          "prop":"imageinfo","iiprop":"url|extmetadata|mime|size",
                          "iiurlwidth":1280}
                response = requests.get("https://commons.wikimedia.org/w/api.php",
                    params=params, timeout=25, headers={"User-Agent":"UrduRealStoriesAI/1.0 (story video generator)"})
                response.raise_for_status()
                pages = list(response.json().get("query",{}).get("pages",{}).values())
                # Prefer distinct files and real photographs over diagrams/artwork.
                pages.sort(key=lambda p: (
                    any(x in p.get("title","").lower() for x in ("photograph","photo")),
                    p.get("imageinfo",[{}])[0].get("width",0)
                ), reverse=True)
                for page in pages:
                    title = page.get("title","")
                    title_l = title.lower()
                    if title in used_titles or any(term in title_l for term in blocked):
                        continue
                    info = (page.get("imageinfo") or [{}])[0]
                    if info.get("mime") not in ("image/jpeg","image/png","image/webp"):
                        continue
                    if info.get("width",0) < 600 or info.get("height",0) < 350:
                        continue
                    image_url = info.get("thumburl") or info.get("url")
                    if not image_url:
                        continue
                    res = requests.get(image_url, timeout=25,
                        headers={"User-Agent":"UrduRealStoriesAI/1.0"})
                    if not res.ok or not res.headers.get("content-type","").startswith("image/"):
                        continue
                    from io import BytesIO
                    try:
                        check = Image.open(BytesIO(res.content))
                        check.verify()
                    except Exception:
                        continue
                    target.write_bytes(res.content)
                    used_titles.add(title)
                    meta = info.get("extmetadata",{})
                    credits.append({
                        "file": title, "scene": i + 1, "search_query": search_query,
                        "source_page": "https://commons.wikimedia.org/wiki/" + title.replace(" ","_"),
                        "artist": meta.get("Artist",{}).get("value","")[:500],
                        "license": meta.get("LicenseShortName",{}).get("value",""),
                        "license_url": meta.get("LicenseUrl",{}).get("value",""),
                        "credit_line": meta.get("Credit",{}).get("value","")[:500]
                    })
                    chosen = target
                    break
                if chosen:
                    break
            except Exception as exc:
                log(f"Image source retry for scene {i+1}: {type(exc).__name__}")
        if chosen:
            log(f"Scene {i+1}: unique photo selected.")
        else:
            log(f"Scene {i+1}: no unique photo found after retries.")
        scene_assets.append(chosen)
    (OUT/"asset_credits.json").write_text(json.dumps(credits,ensure_ascii=False,indent=2),encoding="utf-8")
    usable = [x for x in scene_assets if x is not None]
    log(f"Downloaded {len(usable)} unique images for {len(scene_queries[:10])} scenes.")
    # Do not silently repeat an unrelated photo across the whole video.
    if len(usable) < min(6, len(scene_queries[:10])):
        raise RuntimeError(f"Only {len(usable)} unique relevant photos found; need at least 6. Retry the workflow later.")
    return scene_assets

def get_font(size):
    for p in ["/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def get_latin_font(size):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def make_slides(images, scenes, title, narration, audio):
    # Build Urdu subtitle segments from the actual narration and time them to audio length.
    probe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(audio)],
        capture_output=True,text=True,check=True)
    total_seconds = max(1.0,float(probe.stdout.strip()))
    sentences = [x.strip() for x in re.split(r'(?<=[۔؟!])\s*', narration) if x.strip()]
    if not sentences:
        sentences = [narration]
    groups = [[] for _ in range(len(images))]
    total_words = max(1,len(narration.split()))
    targets = [total_words*(i+1)/len(images) for i in range(len(images))]
    current, count, idx = 0, 0, 0
    for sentence in sentences:
        groups[idx].append(sentence)
        count += len(sentence.split())
        if idx < len(images)-1 and count >= targets[idx]:
            idx += 1
    slides, durations = [], []
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        def rtl(value): return get_display(arabic_reshaper.reshape(value))
    except Exception:
        def rtl(value): return value
    weights = [max(1,len(" ".join(g).split())) for g in groups]
    weight_sum = sum(weights)
    for i, image_path in enumerate(images):
        canvas = Image.new("RGB",(1280,720),(20,28,44))
        try:
            bg=ImageOps.fit(Image.open(image_path).convert("RGB"),(1280,720))
            shade=Image.new("RGBA",bg.size,(0,0,0,95))
            canvas=Image.alpha_composite(bg.convert("RGBA"),shade).convert("RGB")
        except Exception as exc:
            log(f"Scene background fallback {i+1}: {type(exc).__name__}")
        draw=ImageDraw.Draw(canvas)
        draw.rectangle((0,535,1280,720),fill=(0,0,0,185))
        subtitle = " ".join(groups[i])
        # Keep subtitles readable and right-to-left; wrap by words, not arbitrary characters.
        words = subtitle.split()
        lines, line = [], ""
        for word in words:
            trial = (line + " " + word).strip()
            if len(trial) > 58 and line:
                lines.append(line); line = word
            else:
                line = trial
        if line: lines.append(line)
        lines = lines[:4]
        font = get_font(34)
        y = 555 if len(lines) <= 3 else 540
        for line in lines:
            try:
                draw.text((1215,y),rtl(line),font=font,fill="white",anchor="rt",
                          direction="rtl",language="ur",stroke_width=1,stroke_fill=(0,0,0))
            except (TypeError,ValueError):
                draw.text((1215,y),rtl(line),font=font,fill="white",anchor="rt",
                          stroke_width=1,stroke_fill=(0,0,0))
            y += 42
        path=WORK/f"slide_{i:02d}.jpg"
        canvas.save(path,quality=92); slides.append(path)
        durations.append(total_seconds*weights[i]/weight_sum)
    return slides, durations

def create_thumbnail(text, images=None, title=""):
    # Use the first real scene photo as the thumbnail background, never a plain text card.
    source = None
    for candidate in (images or []):
        try:
            if candidate and Path(candidate).exists():
                source = Image.open(candidate).convert("RGB")
                break
        except Exception:
            continue
    if source is None:
        source = Image.new("RGB",(1280,720),(18,27,46))
    im = ImageOps.fit(source,(1280,720),method=Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA",im.size,(0,0,0,0))
    od = ImageDraw.Draw(overlay)
    # Dark left-to-right gradient leaves the photograph visible while keeping Urdu text readable.
    for x in range(1280):
        alpha = int(205 * (1 - x / 1500))
        od.line((x,0,x,720),fill=(5,10,20,max(45,alpha)))
    im = Image.alpha_composite(im.convert("RGBA"),overlay).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((55,55,440,112),radius=20,fill=(190,135,35))
    d.text((82,67),"URDU REAL STORIES",font=get_latin_font(27),fill="white")
    headline = (text or title or "ایک حیرت انگیز کہانی").strip()
    # Limit headline length and wrap into short Urdu lines.
    headline = headline[:42]
    chunks = [headline[i:i+18] for i in range(0,len(headline),18)]
    font = get_font(72)
    y = 250 if len(chunks) <= 2 else 205
    for line in chunks[:3]:
        try:
            d.text((1200,y),line,font=font,fill="white",anchor="rt",
                   direction="rtl",language="ur",stroke_width=3,stroke_fill=(0,0,0))
        except (TypeError,ValueError):
            d.text((1200,y),line,font=font,fill="white",anchor="rt",
                   stroke_width=3,stroke_fill=(0,0,0))
        y += 105
    # Small visual accent and bottom bar.
    d.rounded_rectangle((55,635,1225,650),radius=7,fill=(244,194,91))
    path=OUT/"thumbnail.jpg"
    im.save(path,quality=95)
    return path

def create_video(slides, audio, durations):
    concat=WORK/"slides.txt"
    with concat.open("w",encoding="utf-8") as f:
        for slide,duration in zip(slides,durations):
            f.write(f"file '{slide.resolve()}'\nduration {max(1.0,duration):.3f}\n")
        f.write(f"file '{slides[-1].resolve()}'\n")
    silent=WORK/"slides.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),
         "-vf","scale=1280:720,format=yuv420p","-r","30","-c:v","libx264","-preset","veryfast",str(silent)])
    final=OUT/"urdu_story.mp4"
    run(["ffmpeg","-y","-i",str(silent),"-i",str(audio),"-shortest",
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
    credit_text="\n".join(
        f"{x['file']} — {x['license']} — {x['license_url']} — {x['source_page']}"
        for x in credits
    )
    description=(story["description_ur"]+
        f"\n\nبنیادی حوالہ: {topic['source']}\n{topic['url']}\n\nتصاویر کے لائسنس اور کریڈٹ:\n{credit_text}")[:4900]
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
    slides,durations=make_slides(images,story["scenes"],story["title_ur"],story["narration_ur"],audio)
    video=create_video(slides,audio,durations)
    thumbnail=create_thumbnail(story["thumbnail_text_ur"], images, story["title_ur"])
    if os.getenv("UPLOAD_TO_YOUTUBE","false").lower()=="true":
        upload_to_youtube(video,thumbnail,story,topic)
    else:
        log("Upload disabled: video generated only.")
    log("Done. Check output/ for video, voice, thumbnail, script and image credits.")

if __name__=="__main__": main()

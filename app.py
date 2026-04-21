cat << 'EOF' > app.py
import os, json, time, uuid, subprocess, textwrap, threading, re, asyncio
from flask import Flask, request, jsonify, redirect, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import requests, edge_tts

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fs2024')

CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
GROQ_KEY = os.environ.get('GROQ_API_KEY')
TG_TOKEN = os.environ.get('TG_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
REDIRECT_URI = 'https://zeus-video-server.onrender.com/oauth/callback'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
PENDING_FILE = 'pending.json'
TG_API = f'https://api.telegram.org/bot{TG_TOKEN}'
AR_VOICES = ['ar-DZ-AminaNeural', 'ar-SA-ZariyahNeural', 'ar-EG-ShakirNeural']

def get_yt_creds():
    yt_env = os.environ.get('YOUTUBE_TOKENS')
    if yt_env:
        try:
            return Credentials.from_authorized_user_info(json.loads(yt_env), SCOPES)
        except: pass
    return None

def load_pending():
    try:
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE) as f: return json.load(f)
    except: pass
    return {}

def save_pending(data):
    try:
        with open(PENDING_FILE, 'w') as f: json.dump(data, f)
    except: pass

pending = load_pending()

def tg(text, kb=None):
    d = {'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
    if kb: d['reply_markup'] = json.dumps(kb)
    try: return requests.post(f'{TG_API}/sendMessage', json=d, timeout=10).json()
    except: return {}

def tg_edit(mid, text):
    try: requests.post(f'{TG_API}/editMessageText', json={'chat_id': TG_CHAT_ID, 'message_id': mid, 'text': text, 'parse_mode': 'HTML'}, timeout=10)
    except: pass

async def make_audio_async(text, path):
    for voice in AR_VOICES:
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(path)
            if os.path.exists(path) and os.path.getsize(path) > 1000: return True
        except: continue
    return False

def make_audio(text, path):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    res = loop.run_until_complete(make_audio_async(text, path))
    loop.close()
    return res

def upload_yt(path, sd, progress_msg_id=None):
    creds = get_yt_creds()
    if not creds: raise Exception("يجب ربط اليوتيوب أولاً عبر /auth")
    yt = build('youtube', 'v3', credentials=creds)
    body = {'snippet': {'title': sd.get('title', 'فلسفة ديزاد')[:100], 'description': f"{sd.get('description', '')}\n{sd.get('hashtags', '')}", 'tags': ['فلسفة', 'الجزائر'], 'categoryId': '22'}, 'status': {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False}}
    media = MediaFileUpload(path, mimetype='video/mp4', resumable=True)
    req = yt.videos().insert(part='snippet,status', body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status and progress_msg_id:
            p = int(status.progress() * 100)
            tg_edit(progress_msg_id, f"📤 جاري الرفع: {p}%")
    return f"https://youtu.be/{resp['id']}"

@app.route('/')
def index(): return "سيرفر زوس يعمل بنجاح ✅"

@app.route('/get_tokens')
def get_tokens():
    yt_env = os.environ.get('YOUTUBE_TOKENS')
    return yt_env if yt_env else jsonify({"error": "No token in ENV"})

@app.route('/auth')
def auth():
    flow = Flow.from_client_config({"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
    return redirect(auth_url)

@app.route('/oauth/callback')
def callback():
    flow = Flow.from_client_config({"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(authorization_response=request.url)
    return jsonify(json.loads(flow.credentials.to_json()))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
EOF


import os,json,time,uuid,subprocess,textwrap,threading,re,asyncio
from flask import Flask,request,jsonify,redirect,session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload,DEFAULT_CHUNK_SIZE
from googleapiclient.errors import HttpError
import requests,edge_tts

app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY','fs2024')
CLIENT_ID=os.environ.get('GOOGLE_CLIENT_ID')
CLIENT_SECRET=os.environ.get('GOOGLE_CLIENT_SECRET')
GROQ_KEY=os.environ.get('GROQ_API_KEY')
TG_TOKEN=os.environ.get('TG_TOKEN')
TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
REDIRECT_URI='https://zeus-video-server.onrender.com/oauth/callback'
SCOPES=['https://www.googleapis.com/auth/youtube.upload']
TOKENS_FILE='youtube_tokens.json'
PENDING_FILE='pending.json'
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'
AR_VOICES=['ar-DZ-AminaNeural','ar-SA-ZariyahNeural','ar-EG-ShakirNeural']

# ── Pending Storage دائم ──────────────────────────────────────
def load_pending():
    try:
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE) as f: return json.load(f)
    except: pass
    return {}

def save_pending(data):
    try:
        with open(PENDING_FILE,'w') as f: json.dump(data,f)
    except: pass

# Load YouTube tokens from ENV
if not os.path.exists(TOKENS_FILE):
    yt_env = os.environ.get('YOUTUBE_TOKENS')
    if yt_env:
        with open(TOKENS_FILE,'w') as f: f.write(yt_env)
pending = load_pending()

# ── Telegram ──────────────────────────────────────────────────
def tg(text,kb=None):
    d={'chat_id':TG_CHAT_ID,'text':text,'parse_mode':'HTML'}
    if kb: d['reply_markup']=json.dumps(kb)
    try: r=requests.post(f'{TG_API}/sendMessage',json=d,timeout=10); return r.json()
    except: return{}

def tg_edit(mid,text):
    try: requests.post(f'{TG_API}/editMessageText',json={'chat_id':TG_CHAT_ID,'message_id':mid,'text':text,'parse_mode':'HTML'},timeout=10)
    except: pass

def notify(vid):
    v=pending.get(vid)
    if not v: return
    sd=v['script_data']
    msg=(f"🎬 <b>فيديو جاهز للمراجعة!</b>\n\n"
         f"📌 <b>{sd.get('title','')}</b>\n"
         f"🎭 {sd.get('character','')} {sd.get('emoji','')}\n"
         f"📝 {v.get('topic','')}\n\n"
         f"<i>{sd.get('script','')[:300]}...</i>\n\n"
         f"{sd.get('hashtags','')}")
    kb={'inline_keyboard':[[
        {'text':'✅ نشر على YouTube','callback_data':f'approve:{vid}'},
        {'text':'❌ رفض','callback_data':f'reject:{vid}'},
    ],[{'text':'🔄 أعد الكتابة','callback_data':f'regen:{vid}'}]]}
    res=tg(msg,kb)
    if res.get('ok'): pending[vid]['tg_mid']=res['result']['message_id']; save_pending(pending)

# ── Groq Script ───────────────────────────────────────────────
def gen_script(topic):
    prompt=f'''اكتب سكريبت فيديو 30-45 ثانية لصفحة فلسفة ديزاد عن: {topic}
فاكهة تتكلم بالدارجة الجزائرية (راني،واش،بصح،والو،كاش،يزي)
JSON فقط: {{"title":"عنوان","character":"الشخصية","emoji":"🍌","script":"النص","hashtags":"#فلسفة_ديزاد #الجزائر","description":"وصف","fruit_search":"banana"}}'''
    r=requests.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization':f'Bearer {GROQ_KEY}','Content-Type':'application/json'},
        json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}],'max_tokens':800,'temperature':0.9},
        timeout=30)
    text=r.json()['choices'][0]['message']['content']
    clean=text.replace('```json','').replace('```','').strip()
    m=re.search(r'\{[\s\S]*\}',clean)
    return json.loads(m.group(0)) if m else{}

# ── Audio ─────────────────────────────────────────────────────
async def make_audio_async(text,path):
    for voice in AR_VOICES:
        try:
            communicate=edge_tts.Communicate(text,voice)
            await communicate.save(path)
            if os.path.exists(path) and os.path.getsize(path)>1000: return True
        except: continue
    return False

def make_audio(text,path):
    try:
        loop=asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result=loop.run_until_complete(make_audio_async(text,path))
        loop.close()
        return result
    except: return False

# ── Image ─────────────────────────────────────────────────────
def get_image(search):
    try:
        r=requests.get(f'https://source.unsplash.com/1080x1920/?{search},fruit',timeout=15,allow_redirects=True)
        if r.status_code==200 and len(r.content)>5000:
            path=f'/tmp/{uuid.uuid4()}.jpg'
            with open(path,'wb') as f: f.write(r.content)
            return path
    except: pass
    return None

# ── Video ─────────────────────────────────────────────────────
def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'
    audio=f'videos/{vid}.mp3'
    char=sd.get('character','الفيلسوف').replace("'",' ').replace(':',' ')
    emoji=sd.get('emoji','🍌')
    script=sd.get('script','فلسفة ديزاد')
    lines='\n'.join(textwrap.wrap(script,width=26)[:7]).replace("'",' ').replace(':',' ').replace('%',' ')
    has_audio=make_audio(script,audio)
    img=get_image(sd.get('fruit_search','fruit'))
    vf=(f"drawtext=text='{emoji}':fontsize=150:x=(w-text_w)/2:y=150:fontcolor=white:shadowcolor=black:shadowx=3:shadowy=3,"
        f"drawtext=text='{char}':fontsize=52:x=(w-text_w)/2:y=370:fontcolor=#FFD700:shadowcolor=black:shadowx=2:shadowy=2,"
        f"drawtext=text='{lines}':fontsize=40:x=60:y=580:fontcolor=white:line_spacing=14:shadowcolor=black:shadowx=2:shadowy=2,"
        f"drawtext=text='فلسفة ديزاد':fontsize=36:x=(w-text_w)/2:y=1820:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2")
    if img:
        vi=['-loop','1','-i',img]
    else:
        vi=['-f','lavfi','-i','color=c=#1a0a2e:size=1080x1920:duration=50:rate=30']
    if has_audio and os.path.exists(audio):
        cmd=['ffmpeg','-y']+vi+['-i',audio,'-vf',vf,'-c:v','libx264','-preset','ultrafast','-crf','26','-c:a','aac','-shortest','-t','50',out]
    else:
        cmd=['ffmpeg','-y']+vi+['-vf',vf,'-c:v','libx264','-preset','ultrafast','-crf','26','-t','45',out]
    r=subprocess.run(cmd,capture_output=True,timeout=240)
    if os.path.exists(audio): os.remove(audio)
    if img and os.path.exists(img): os.remove(img)
    if r.returncode!=0: raise Exception(f'FFmpeg:{r.stderr.decode()[:200]}')
    return out

# ── YouTube Upload مع تقدم ────────────────────────────────────
def upload_yt(path,sd,progress_msg_id=None):
    if not os.path.exists(TOKENS_FILE): raise Exception('YouTube غير مربوط — روح /auth')
    with open(TOKENS_FILE) as f: t=json.load(f)
    creds=Credentials(token=t['access_token'],refresh_token=t['refresh_token'],
                      token_uri='https://oauth2.googleapis.com/token',
                      client_id=CLIENT_ID,client_secret=CLIENT_SECRET,scopes=SCOPES)
    yt=build('youtube','v3',credentials=creds)
    body={'snippet':{'title':sd.get('title','فلسفة ديزاد')[:100],
                     'description':f"{sd.get('description','')}\n{sd.get('hashtags','')}",
                     'tags':['فلسفة','الجزائر','فلسفة_ديزاد','ضحك'],'categoryId':'22'},
          'status':{'privacyStatus':'public','selfDeclaredMadeForKids':False}}
    
    file_size=os.path.getsize(path)
    media=MediaFileUpload(path,mimetype='video/mp4',resumable=True,chunksize=1024*1024)
    req=yt.videos().insert(part='snippet,status',body=body,media_body=media)
    
    resp=None
    last_progress=-1
    retry=0
    
    while resp is None:
        try:
            status,resp=req.next_chunk()
            if status:
                progress=int(status.progress()*100)
                # أرسل تحديث كل 25%
                if progress-last_progress>=25 and progress_msg_id:
                    bar='▓'*(progress//10)+'░'*(10-progress//10)
                    tg_edit(progress_msg_id,
                        f'📤 <b>جاري الرفع على YouTube...</b>\n\n'
                        f'{bar} {progress}%\n\n'
                        f'⚡ لا تغلق التطبيق')
                    last_progress=progress
            retry=0
        except HttpError as e:
            if e.resp.status in [500,502,503,504] and retry<5:
                retry+=1
                time.sleep(2**retry)
            else: raise
        except Exception as e:
            if retry<3:
                retry+=1
                time.sleep(5)
            else: raise
    
    return f"https://youtu.be/{resp['id']}"

# ── Webhook ───────────────────────────────────────────────────
@app.route('/telegram',methods=['POST'])
def webhook():
    data=request.json or{}
    if 'callback_query' in data:
        cb=data['callback_query'];cid=cb['id']
        parts=cb.get('data','').split(':',1);action=parts[0];vid=parts[1] if len(parts)>1 else''
        mid=cb['message']['message_id']
        requests.post(f'{TG_API}/answerCallbackQuery',json={'callback_query_id':cid,'text':'⏳'},timeout=5)
        
        if action=='approve' and vid in pending:
            # أرسل رسالة تقدم جديدة
            prog=tg('📤 <b>جاري الرفع على YouTube...</b>\n\n░░░░░░░░░░ 0%\n\n⚡ لا تغلق التطبيق')
            prog_mid=prog.get('result',{}).get('message_id')
            tg_edit(mid,'✅ <b>تمت الموافقة — جاري الرفع...</b>')
            
            def up():
                v=pending.get(vid,{})
                try:
                    url=upload_yt(v['video_path'],v['script_data'],prog_mid)
                    if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
                    pending.pop(vid,None); save_pending(pending)
                    if prog_mid:
                        tg_edit(prog_mid,f'🎉 <b>تم النشر بنجاح!</b>\n\n🔗 {url}\n\n✅ {v["script_data"].get("title","")}')
                except Exception as e:
                    if prog_mid: tg_edit(prog_mid,f'❌ <b>فشل الرفع:</b> {str(e)[:150]}\n\nاضغط ✅ مرة أخرى للإعادة')
            threading.Thread(target=up).start()
        
        elif action=='reject' and vid in pending:
            v=pending.pop(vid,{})
            if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
            save_pending(pending)
            tg_edit(mid,'❌ تم الرفض. أرسل موضوع جديد.')
        
        elif action=='regen' and vid in pending:
            topic=pending[vid].get('topic','معنى الحياة')
            v=pending.pop(vid,{})
            if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
            save_pending(pending)
            tg_edit(mid,f'🔄 <b>إعادة توليد: {topic}</b>\n⏳ دقيقة...')
            def rg():
                try:
                    sd=gen_script(topic);nid=str(uuid.uuid4())[:8];vp=make_video(sd,nid)
                    pending[nid]={'id':nid,'script_data':sd,'video_path':vp,'topic':topic,'status':'pending','created_at':time.time()}
                    save_pending(pending); notify(nid)
                except Exception as e: tg(f'❌ {str(e)[:150]}')
            threading.Thread(target=rg).start()
    
    elif 'message' in data:
        text=data['message'].get('text','')
        if text=='/start':
            tg('👋 <b>فلسفة ديزاد Bot 🎬</b>\n\nأرسل موضوع وأنا أصنع فيديو احترافي!\n\n🎙️ صوت عربي طبيعي\n🖼️ صور حقيقية\n📤 نشر تلقائي على YouTube\n\nمثال:\n<i>الموزة وسر السعادة</i>')
        elif text=='/status':
            count=len([v for v in pending.values() if v.get('status')=='pending'])
            tg(f'📊 <b>الحالة:</b>\nYouTube: {"✅" if os.path.exists(TOKENS_FILE) else "❌ /auth"}\nGroq: {"✅" if GROQ_KEY else "❌"}\nالصوت: ✅ Edge-TTS\nالصور: ✅ Unsplash\nانتظار موافقة: {count} فيديو')
        elif text and not text.startswith('/'):
            topic=text.strip()
            tg(f'🎬 <b>جاري التوليد:</b> {topic}\n\n⏳ 60-90 ثانية...\n🎙️ صوت + 🖼️ صورة + 📝 نص')
            def gn():
                try:
                    sd=gen_script(topic)
                    if not sd: tg('❌ فشل التوليد، جرب موضوع آخر.'); return
                    vid=str(uuid.uuid4())[:8]; vp=make_video(sd,vid)
                    pending[vid]={'id':vid,'script_data':sd,'video_path':vp,'topic':topic,'status':'pending','created_at':time.time()}
                    save_pending(pending); notify(vid)
                except Exception as e: tg(f'❌ خطأ: {str(e)[:150]}')
            threading.Thread(target=gn).start()
    
    return jsonify({'ok':True})

@app.route('/')
def home():
    return jsonify({'status':'✅ فلسفة ديزاد v3','youtube':'✅' if os.path.exists(TOKENS_FILE) else'❌ /auth','audio':'✅ Edge-TTS','images':'✅ Unsplash','upload':'✅ Resumable + Progress'})

@app.route('/auth')
def auth():
    flow=Flow.from_client_config({'web':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token','redirect_uris':[REDIRECT_URI]}},scopes=SCOPES,redirect_uri=REDIRECT_URI)
    url,state=flow.authorization_url(access_type='offline',prompt='consent'); session['state']=state; return redirect(url)

@app.route('/oauth/callback')
def cb():
    flow=Flow.from_client_config({'web':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token','redirect_uris':[REDIRECT_URI]}},scopes=SCOPES,redirect_uri=REDIRECT_URI,state=session.get('state'))
    flow.fetch_token(authorization_response=request.url); creds=flow.credentials
    with open(TOKENS_FILE,'w') as f: json.dump({'access_token':creds.token,'refresh_token':creds.refresh_token},f)
    tg('🎉 <b>تم ربط YouTube!</b>'); return'<h1 style="color:green;text-align:center;font-family:sans-serif;margin-top:100px">✅ تم ربط YouTube!</h1>'

@app.route('/setup_webhook')
def sw():
    r=requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return jsonify(r.json())

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

@app.route('/get_tokens')
def get_tokens():
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({'error': 'not connected'})

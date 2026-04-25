import os,json,time,uuid,subprocess,textwrap,threading,re,asyncio
from flask import Flask,request,jsonify,redirect,session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import requests,edge_tts
from gtts import gTTS

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

CHARACTERS={
'موزة':{'search':'banana yellow','emoji':'🍌','color':'#FFD700'},
'تفاحة':{'search':'red apple fruit','emoji':'🍎','color':'#FF4444'},
'ليمونة':{'search':'lemon yellow fruit','emoji':'🍋','color':'#FFFF00'},
'برتقالة':{'search':'orange fruit','emoji':'🍊','color':'#FF8C00'},
'بطيخة':{'search':'watermelon fruit','emoji':'🍉','color':'#FF6B6B'},
'طماطم':{'search':'tomato red','emoji':'🍅','color':'#FF6347'},
'خيارة':{'search':'cucumber green','emoji':'🥒','color':'#228B22'},
'بصلة':{'search':'onion vegetable','emoji':'🧅','color':'#DEB887'},
}

yt_env=os.environ.get('YOUTUBE_TOKENS')
if yt_env and not os.path.exists(TOKENS_FILE):
    with open(TOKENS_FILE,'w') as f: f.write(yt_env)

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

pending=load_pending()

def tg(text,kb=None):
    d={'chat_id':TG_CHAT_ID,'text':text,'parse_mode':'HTML'}
    if kb: d['reply_markup']=json.dumps(kb)
    try: r=requests.post(f'{TG_API}/sendMessage',json=d,timeout=10); return r.json()
    except: return{}

def tg_edit(mid,text,kb=None):
    d={'chat_id':TG_CHAT_ID,'message_id':mid,'text':text,'parse_mode':'HTML'}
    if kb: d['reply_markup']=json.dumps(kb)
    try: requests.post(f'{TG_API}/editMessageText',json=d,timeout=10)
    except: pass

def tg_send_video(path,caption):
    """يرسل الفيديو مباشرة على Telegram"""
    try:
        with open(path,'rb') as f:
            r=requests.post(f'{TG_API}/sendVideo',
                data={'chat_id':TG_CHAT_ID,'caption':caption,'parse_mode':'HTML','supports_streaming':True},
                files={'video':f},timeout=120)
        return r.json()
    except Exception as e:
        print(f'Send video error: {e}')
        return{}

def notify(vid):
    v=pending.get(vid)
    if not v: return
    sd=v['script_data']

    # أولاً أرسل الفيديو مباشرة
    caption=(f"🎬 <b>{sd.get('title','')}</b>\n"
             f"🎭 {sd.get('character','')} {sd.get('emoji','')}\n"
             f"📝 {v.get('topic','')}\n\n"
             f"{sd.get('hashtags','')}\n\n"
             f"👆 <b>حمّل الفيديو للتيك توك والانستغرام!</b>")

    tg_send_video(v['video_path'],caption)

    # ثم أرسل أزرار الإجراءات
    msg=(f"⚡ <b>إجراءات الفيديو:</b>\n\n"
         f"📌 {sd.get('title','')}\n"
         f"🎭 {sd.get('character','')} {sd.get('emoji','')}\n\n"
         f"<i>{sd.get('script','')[:200]}...</i>")

    kb={'inline_keyboard':[[
        {'text':'✅ نشر YouTube','callback_data':f'approve:{vid}'},
        {'text':'❌ رفض','callback_data':f'reject:{vid}'},
    ],[
        {'text':'🔄 أعد الكتابة','callback_data':f'regen:{vid}'},
        {'text':'📊 إحصائيات','callback_data':f'stats:{vid}'},
    ]]}
    res=tg(msg,kb)
    if res.get('ok'): pending[vid]['tg_mid']=res['result']['message_id']; save_pending(pending)

def gen_script(topic):
    import random
    char_name=random.choice(list(CHARACTERS.keys()))
    char_info=CHARACTERS[char_name]
    prompt=f'''اكتب سكريبت فيديو 25-35 ثانية لصفحة فلسفة ديزاد.
الشخصية: {char_name} {char_info["emoji"]} تتكلم بالدارجة الجزائرية
الموضوع: {topic}
الأسلوب: فلسفة خفيفة وطريفة، نهاية مضحكة ومفاجئة
استخدم: راني، واش، بصح، والو، كاش، يزي، ربي

JSON فقط:
{{"title":"عنوان جذاب للسوشيال ميديا","character":"{char_name}","emoji":"{char_info['emoji']}","fruit_search":"{char_info['search']}","color":"{char_info['color']}","script":"النص كامل بالدارجة","hashtags":"#فلسفة_ديزاد #الجزائر #ضحك #فلسفة","description":"وصف قصير"}}'''
    r=requests.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization':f'Bearer {GROQ_KEY}','Content-Type':'application/json'},
        json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}],'max_tokens':600,'temperature':0.9},timeout=30)
    text=r.json()['choices'][0]['message']['content']
    clean=text.replace('```json','').replace('```','').strip()
    m=re.search(r'\{[\s\S]*\}',clean)
    return json.loads(m.group(0)) if m else{}

async def make_audio_async(text,path):
    for voice in AR_VOICES:
        try:
            c=edge_tts.Communicate(text,voice)
            await c.save(path)
            if os.path.exists(path) and os.path.getsize(path)>1000: return True
        except Exception as e:
            if '403' in str(e) or 'forbidden' in str(e).lower():
                continue
    try:
        from gtts import gTTS
        tts=gTTS(text,lang='ar')
        tts.save(path)
        if os.path.exists(path) and os.path.getsize(path)>1000: return 'gtts'
    except: pass
    return False

def make_audio(text,path):
    try:
        loop=asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        r=loop.run_until_complete(make_audio_async(text,path))
        loop.close()
        return r
    except: return False

def get_image(search):
    keywords={
        'banana':'yellow fruit',
        'apple':'red apple',
        'lemon':'yellow lemon',
        'orange':'orange fruit',
        'watermelon':'red watermelon',
        'tomato':'red tomato',
        'cucumber':'green cucumber',
        'onion':'white onion',
    }
    query=keywords.get(search,search)
    urls=[
        f'https://pixel.cookingideas.cl/wp-content/uploads/2024/06/{query}.jpg',
        f'https://images.unsplash.com/photo-1518847875368-8fd91a860c8d?w=1080',
    ]
    if 'banana' in search:
        urls.append('https://images.unsplash.com/photo-1571771894821-9b5f6a4422e8?w=1080')
    elif 'apple' in search:
        urls.append('https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd7?w=1080')
    else:
        urls.append(f'https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=1080')
    
    for url in urls:
        try:
            r=requests.get(url,timeout=10,allow_redirects=True)
            ct=r.headers.get('Content-Type','')
            if r.status_code==200 and len(r.content)>5000 and ('image' in ct or len(r.content)<2000000):
                path=f'/tmp/{uuid.uuid4()}.jpg'
                with open(path,'wb') as f: f.write(r.content)
                return path
        except: continue
    return None

def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'
    audio=f'videos/{vid}.mp3'
    char=sd.get('character','الفيلسوف').replace("'",' ').replace(':',' ')
    emoji=sd.get('emoji','🍌')
    color=sd.get('color','#FFD700')
    script=sd.get('script','فلسفة ديزاد')
    fruit_search=sd.get('fruit_search','fruit')
    lines=' '.join(textwrap.wrap(script,width=24)[:6])
    
    has_audio=make_audio(script,audio)
    img=get_image(fruit_search)

    if img and os.path.exists(img):
        vf=(f"scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            f"zoompan=z='min(zoom+0.001,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=200:s=1080x1920:fps=30,"
            f"drawtext=text='{char}':fontsize=50:x=(w-text_w)/2:y=200:fontcolor={color}:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.5:boxborderw=8,"
            f"drawtext=text='{lines}':fontsize=38:x=40:y=h/2:fontcolor=white:line_spacing=12:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.6:boxborderw=12,"
            f"drawtext=text='فلسفة ديزاد 🎬':fontsize=32:x=(w-text_w)/2:y=h-80:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.4:boxborderw=8,"
            f"format=yuv420p")
        vi=['-loop','1','-i',img]
    else:
        vf=(f"color=c=black:size=1080x1920:duration=50:rate=30,"
            f"drawtext=text='{emoji}':fontsize=140:x=(w-text_w)/2:y=120:fontcolor=white:shadowcolor=black:shadowx=3:shadowy=3,"
            f"drawtext=text='{char}':fontsize=50:x=(w-text_w)/2:y=280:fontcolor={color}:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.5:boxborderw=8,"
            f"drawtext=text='{lines}':fontsize=38:x=40:y=h/2:fontcolor=white:line_spacing=12:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.6:boxborderw=12,"
            f"drawtext=text='فلسفة ديزاد 🎬':fontsize=32:x=(w-text_w)/2:y=h-80:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.4:boxborderw=8,"
            f"format=yuv420p")
        vi=['-f','lavfi','-i','color=c=black:size=1080x1920:duration=50:rate=30']

    if has_audio and os.path.exists(audio):
        cmd=['ffmpeg','-y']+vi+['-i',audio,'-vf',vf,'-c:v','libx264','-preset','ultrafast','-crf','24','-c:a','aac','-shortest','-t','50',out]
    else:
        cmd=['ffmpeg','-y']+vi+['-vf',vf,'-c:v','libx264','-preset','ultrafast','-crf','24','-t','45',out]

    r=subprocess.run(cmd,capture_output=True,timeout=240)
    if img and os.path.exists(img): os.remove(img)
    if os.path.exists(audio): os.remove(audio)
    if r.returncode!=0: raise Exception(f'FFmpeg:{r.stderr.decode()[:200]}')
    return out

def upload_yt(path,sd,prog_mid=None):
    if not os.path.exists(TOKENS_FILE): raise Exception('YouTube غير مربوط')
    with open(TOKENS_FILE) as f: t=json.load(f)
    creds=Credentials(token=t['access_token'],refresh_token=t['refresh_token'],
                      token_uri='https://oauth2.googleapis.com/token',
                      client_id=CLIENT_ID,client_secret=CLIENT_SECRET,scopes=SCOPES)
    yt=build('youtube','v3',credentials=creds)
    body={'snippet':{'title':sd.get('title','فلسفة ديزاد')[:100],
                     'description':f"{sd.get('description','')}\n{sd.get('hashtags','')}",
                     'tags':['فلسفة','الجزائر','فلسفة_ديزاد','ضحك','فواكه'],'categoryId':'22'},
          'status':{'privacyStatus':'public','selfDeclaredMadeForKids':False}}
    media=MediaFileUpload(path,mimetype='video/mp4',resumable=True,chunksize=1024*1024)
    req=yt.videos().insert(part='snippet,status',body=body,media_body=media)
    resp=None; last=-1; retry=0
    while resp is None:
        try:
            status,resp=req.next_chunk()
            if status:
                p=int(status.progress()*100)
                if p-last>=25 and prog_mid:
                    bar='▓'*(p//10)+'░'*(10-p//10)
                    tg_edit(prog_mid,f'📤 <b>جاري الرفع...</b>\n\n{bar} {p}%')
                    last=p
            retry=0
        except HttpError as e:
            if e.resp.status in [500,502,503,504] and retry<5: retry+=1; time.sleep(2**retry)
            else: raise
        except:
            if retry<3: retry+=1; time.sleep(5)
            else: raise
    return f"https://youtu.be/{resp['id']}"

@app.route('/telegram',methods=['POST'])
def webhook():
    data=request.json or{}
    if 'callback_query' in data:
        cb=data['callback_query']; cid=cb['id']
        parts=cb.get('data','').split(':',1); action=parts[0]; vid=parts[1] if len(parts)>1 else''
        mid=cb['message']['message_id']
        requests.post(f'{TG_API}/answerCallbackQuery',json={'callback_query_id':cid,'text':'⏳'},timeout=5)
        if action=='approve' and vid in pending:
            prog=tg('📤 <b>جاري الرفع على YouTube...</b>\n\n░░░░░░░░░░ 0%')
            prog_mid=prog.get('result',{}).get('message_id')
            tg_edit(mid,'✅ <b>تمت الموافقة!</b>')
            def up():
                v=pending.get(vid,{})
                try:
                    url=upload_yt(v['video_path'],v['script_data'],prog_mid)
                    if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
                    pending.pop(vid,None); save_pending(pending)
                    tg_edit(prog_mid,f'🎉 <b>تم النشر على YouTube!</b>\n\n🔗 {url}\n\n✅ {v["script_data"].get("title","")}')
                except Exception as e:
                    tg_edit(prog_mid,f'❌ فشل: {str(e)[:150]}\nاضغط ✅ مرة أخرى')
            threading.Thread(target=up).start()
        elif action=='reject' and vid in pending:
            v=pending.pop(vid,{}); save_pending(pending)
            if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
            tg_edit(mid,'❌ تم الرفض.')
        elif action=='regen' and vid in pending:
            topic=pending[vid].get('topic','معنى الحياة')
            v=pending.pop(vid,{}); save_pending(pending)
            if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
            tg_edit(mid,f'🔄 <b>إعادة توليد: {topic}</b>\n⏳ دقيقتين...')
            def rg():
                try:
                    sd=gen_script(topic); nid=str(uuid.uuid4())[:8]; vp=make_video(sd,nid)
                    pending[nid]={'id':nid,'script_data':sd,'video_path':vp,'topic':topic,'status':'pending','created_at':time.time()}
                    save_pending(pending); notify(nid)
                except Exception as e: tg(f'❌ {str(e)[:150]}')
            threading.Thread(target=rg).start()
    elif 'message' in data:
        text=data['message'].get('text','')
        if text=='/start':
            tg('👋 <b>فلسفة ديزاد Bot 🎬</b>\n\n🍌 فواكه تتكلم بالدارجة!\n\nأرسل موضوع:\n<i>معنى الحياة</i>\n<i>سر السعادة</i>\n<i>الحب والخيانة</i>\n\n/status - الحالة\n/token - توكن YouTube\n\n📱 الفيديو يجيك مباشرة للتحميل على TikTok وInstagram!')
        elif text=='/status':
            tg(f'📊 YouTube: {"✅" if os.path.exists(TOKENS_FILE) else "❌"}\nGroq: {"✅" if GROQ_KEY else "❌"}\nانتظار: {len(pending)} فيديو')
        elif text=='/token':
            if os.path.exists(TOKENS_FILE):
                with open(TOKENS_FILE) as f: dt=f.read()
                tg(f'🔑 <b>YouTube Token:</b>\n<code>{dt}</code>\n\nأضفه في Render كـ YOUTUBE_TOKENS')
            else:
                tg('❌ مش مربوط\nhttps://zeus-video-server.onrender.com/auth')
        elif text and not text.startswith('/'):
            topic=text.strip()
            tg(f'🎬 <b>جاري التوليد:</b> {topic}\n\n🎙️ صوت جزائري\n🍌 فاكهة بـ Ken Burns\n⏳ دقيقة...\n\n📱 راح يجيك الفيديو مباشرة للتحميل على TikTok وInstagram!')
            def gn():
                try:
                    sd=gen_script(topic)
                    if not sd: tg('❌ فشل التوليد.'); return
                    vid=str(uuid.uuid4())[:8]; vp=make_video(sd,vid)
                    pending[vid]={'id':vid,'script_data':sd,'video_path':vp,'topic':topic,'status':'pending','created_at':time.time()}
                    save_pending(pending); notify(vid)
                except Exception as e: tg(f'❌ {str(e)[:150]}')
            threading.Thread(target=gn).start()
    return jsonify({'ok':True})

@app.route('/')
def home():
    return jsonify({'status':'✅ فلسفة ديزاد v5','youtube':'✅' if os.path.exists(TOKENS_FILE) else'❌ /auth'})

@app.route('/auth')
def auth():
    flow=Flow.from_client_config({'web':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token','redirect_uris':[REDIRECT_URI]}},scopes=SCOPES,redirect_uri=REDIRECT_URI)
    url,state=flow.authorization_url(access_type='offline',prompt='consent'); session['state']=state; return redirect(url)

@app.route('/oauth/callback')
def cb():
    flow=Flow.from_client_config({'web':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token','redirect_uris':[REDIRECT_URI]}},scopes=SCOPES,redirect_uri=REDIRECT_URI,state=session.get('state'))
    flow.fetch_token(authorization_response=request.url); creds=flow.credentials
    tokens=json.dumps({'access_token':creds.token,'refresh_token':creds.refresh_token})
    with open(TOKENS_FILE,'w') as f: f.write(tokens)
    tg('🎉 <b>تم ربط YouTube!</b>\nابعث /token للتوكن الدائم')
    return'<h1 style="color:green;text-align:center;font-family:sans-serif;margin-top:100px">✅ تم ربط YouTube!</h1>'

@app.route('/setup_webhook')
def sw():
    r=requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return jsonify(r.json())

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

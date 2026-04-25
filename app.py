import os,json,time,uuid,subprocess,textwrap,threading,re,asyncio,base64
from flask import Flask,request,jsonify,redirect,session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import requests,edge_tts

app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY','fs2024')
CLIENT_ID=os.environ.get('GOOGLE_CLIENT_ID')
CLIENT_SECRET=os.environ.get('GOOGLE_CLIENT_SECRET')
GROQ_KEY=os.environ.get('GROQ_API_KEY')
TG_TOKEN=os.environ.get('TG_TOKEN')
TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DID_KEY=os.environ.get('DID_API_KEY','Ym91cmhhbmNob3VmaUBnbWFpbC5jb20:BgmoFVRTcYzG9maf1MwKh')
REDIRECT_URI='https://zeus-video-server.onrender.com/oauth/callback'
SCOPES=['https://www.googleapis.com/auth/youtube.upload']
TOKENS_FILE='youtube_tokens.json'
PENDING_FILE='pending.json'
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'
AR_VOICES=['ar-DZ-AminaNeural','ar-SA-ZariyahNeural','ar-EG-ShakirNeural']

# فواكه وخضر مع صورها
CHARACTERS={
'موزة':{'search':'banana yellow','emoji':'🍌','color':'#FFD700'},
'تفاحة':{'search':'red apple','emoji':'🍎','color':'#FF4444'},
'ليمونة':{'search':'lemon yellow','emoji':'🍋','color':'#FFFF00'},
'برتقالة':{'search':'orange fruit','emoji':'🍊','color':'#FF8C00'},
'بطيخة':{'search':'watermelon','emoji':'🍉','color':'#FF6B6B'},
'عنبة':{'search':'grapes purple','emoji':'🍇','color':'#8B008B'},
'طماطم':{'search':'tomato red','emoji':'🍅','color':'#FF6347'},
'خيارة':{'search':'cucumber green','emoji':'🥒','color':'#228B22'},
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
        {'text':'✅ نشر YouTube','callback_data':f'approve:{vid}'},
        {'text':'❌ رفض','callback_data':f'reject:{vid}'},
    ],[{'text':'🔄 أعد الكتابة','callback_data':f'regen:{vid}'}]]}
    res=tg(msg,kb)
    if res.get('ok'): pending[vid]['tg_mid']=res['result']['message_id']; save_pending(pending)

def gen_script(topic):
    # اختار شخصية عشوائية
    import random
    char_name=random.choice(list(CHARACTERS.keys()))
    char_info=CHARACTERS[char_name]
    
    prompt=f'''اكتب سكريبت فيديو 25-35 ثانية لصفحة فلسفة ديزاد.
الشخصية: {char_name} {char_info["emoji"]} تتكلم بالدارجة الجزائرية
الموضوع: {topic}
الأسلوب: فلسفة خفيفة وطريفة، نهاية مفاجئة ومضحكة
استخدم: راني، واش، بصح، والو، كاش، يزي، ربي

JSON فقط:
{{"title":"عنوان جذاب","character":"{char_name}","emoji":"{char_info['emoji']}","fruit_search":"{char_info['search']}","script":"النص كامل بالدارجة بدون وصف حركات","hashtags":"#فلسفة_ديزاد #الجزائر #ضحك","description":"وصف قصير"}}'''
    
    r=requests.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization':f'Bearer {GROQ_KEY}','Content-Type':'application/json'},
        json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}],'max_tokens':600,'temperature':0.9},
        timeout=30)
    text=r.json()['choices'][0]['message']['content']
    clean=text.replace('```json','').replace('```','').strip()
    m=re.search(r'\{[\s\S]*\}',clean)
    result=json.loads(m.group(0)) if m else{}
    # أضف معلومات الشخصية
    result['char_info']=char_info
    return result

async def make_audio_async(text,path):
    for voice in AR_VOICES:
        try:
            c=edge_tts.Communicate(text,voice)
            await c.save(path)
            if os.path.exists(path) and os.path.getsize(path)>1000: return True
        except: continue
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
    try:
        r=requests.get(f'https://source.unsplash.com/800x800/?{search}',timeout=15,allow_redirects=True)
        if r.status_code==200 and len(r.content)>5000:
            path=f'/tmp/{uuid.uuid4()}.jpg'
            with open(path,'wb') as f: f.write(r.content)
            return path
    except: pass
    return None

def create_did_video(image_path,audio_path,script):
    """D-ID: يحرك الصورة مع الصوت - الفاكهة تتكلم!"""
    try:
        # رفع الصورة لـ D-ID
        with open(image_path,'rb') as f:
            img_data=base64.b64encode(f.read()).decode()
        
        with open(audio_path,'rb') as f:
            audio_data=base64.b64encode(f.read()).decode()
        
        headers={
            'Authorization':f'Basic {DID_KEY}',
            'Content-Type':'application/json'
        }
        
        # إنشاء الفيديو المتحرك
        payload={
            'source_url':f'data:image/jpeg;base64,{img_data}',
            'script':{
                'type':'audio',
                'audio_url':f'data:audio/mpeg;base64,{audio_data}'
            },
            'config':{
                'fluent':True,
                'pad_audio':0.0,
                'stitch':True
            }
        }
        
        r=requests.post('https://api.d-id.com/talks',
            headers=headers,json=payload,timeout=30)
        
        if r.status_code not in [200,201]:
            return None
        
        talk_id=r.json().get('id')
        if not talk_id: return None
        
        # انتظر حتى يكتمل (max 3 دقائق)
        for _ in range(36):
            time.sleep(5)
            status_r=requests.get(f'https://api.d-id.com/talks/{talk_id}',headers=headers,timeout=10)
            status_data=status_r.json()
            if status_data.get('status')=='done':
                video_url=status_data.get('result_url')
                if video_url:
                    vr=requests.get(video_url,timeout=30)
                    out=f'/tmp/{uuid.uuid4()}.mp4'
                    with open(out,'wb') as f: f.write(vr.content)
                    return out
            elif status_data.get('status')=='error':
                return None
        return None
    except Exception as e:
        print(f'D-ID error: {e}')
        return None

def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'
    audio=f'videos/{vid}.mp3'
    
    char=sd.get('character','الفيلسوف')
    emoji=sd.get('emoji','🍌')
    script=sd.get('script','فلسفة ديزاد')
    fruit_search=sd.get('fruit_search','fruit')
    char_info=sd.get('char_info',{})
    color=char_info.get('color','#FFD700')
    
    # توليد الصوت
    has_audio=make_audio(script,audio)
    
    # جلب صورة الفاكهة
    img=get_image(fruit_search)
    
    # محاولة D-ID (فاكهة تتحرك وتتكلم)
    did_video=None
    if img and has_audio and os.path.exists(audio) and os.path.exists(img):
        did_video=create_did_video(img,audio,script)
    
    if did_video and os.path.exists(did_video):
        # ✅ D-ID نجح — فاكهة متحركة!
        # نضيف نص وشعار فوق الفيديو
        char_safe=char.replace("'",' ').replace(':',' ')
        lines='\n'.join(textwrap.wrap(script,width=30)[:4]).replace("'",' ').replace(':',' ').replace('%',' ')
        
        cmd=['ffmpeg','-y','-i',did_video,
             '-vf',
             f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
             f"drawtext=text='{emoji} {char_safe}':fontsize=60:x=(w-text_w)/2:y=50:fontcolor=white:shadowcolor=black:shadowx=3:shadowy=3:box=1:boxcolor=black@0.5:boxborderw=10,"
             f"drawtext=text='فلسفة ديزاد':fontsize=40:x=(w-text_w)/2:y=1840:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.4:boxborderw=8",
             '-c:v','libx264','-preset','ultrafast','-crf','24',
             '-c:a','aac','-shortest',out]
        r=subprocess.run(cmd,capture_output=True,timeout=120)
        if r.returncode==0:
            if os.path.exists(did_video): os.remove(did_video)
            if img and os.path.exists(img): os.remove(img)
            if os.path.exists(audio): os.remove(audio)
            return out
    
    # Fallback — Ken Burns Effect (بدون D-ID)
    char_safe=char.replace("'",' ').replace(':',' ')
    lines='\n'.join(textwrap.wrap(script,width=26)[:7]).replace("'",' ').replace(':',' ').replace('%',' ')
    
    if img and os.path.exists(img):
        # صورة فاكهة مع Ken Burns (zoom + pan)
        vf=(f"scale=1200:2100,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=250:s=1080x1920:fps=30,"
            f"drawtext=text='{emoji}':fontsize=120:x=(w-text_w)/2:y=80:fontcolor=white:shadowcolor=black:shadowx=4:shadowy=4,"
            f"drawtext=text='{char_safe}':fontsize=55:x=(w-text_w)/2:y=260:fontcolor={color}:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.4:boxborderw=8,"
            f"drawtext=text='{lines}':fontsize=42:x=50:y=520:fontcolor=white:line_spacing=15:shadowcolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.5:boxborderw=6,"
            f"drawtext=text='فلسفة ديزاد 🎬':fontsize=38:x=(w-text_w)/2:y=1830:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2")
        vi=['-loop','1','-i',img]
    else:
        # خلفية ملونة
        vf=(f"drawtext=text='{emoji}':fontsize=160:x=(w-text_w)/2:y=150:fontcolor=white:shadowcolor=black:shadowx=4:shadowy=4,"
            f"drawtext=text='{char_safe}':fontsize=55:x=(w-text_w)/2:y=380:fontcolor={color}:shadowcolor=black:shadowx=2:shadowy=2,"
            f"drawtext=text='{lines}':fontsize=42:x=60:y=580:fontcolor=white:line_spacing=14:shadowcolor=black:shadowx=2:shadowy=2,"
            f"drawtext=text='فلسفة ديزاد 🎬':fontsize=38:x=(w-text_w)/2:y=1820:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2")
        vi=['-f','lavfi','-i',f'color=c=#1a0a2e:size=1080x1920:duration=50:rate=30']
    
    if has_audio and os.path.exists(audio):
        cmd=['ffmpeg','-y']+vi+['-i',audio,'-vf',vf,
             '-c:v','libx264','-preset','ultrafast','-crf','26',
             '-c:a','aac','-shortest','-t','50',out]
    else:
        cmd=['ffmpeg','-y']+vi+['-vf',vf,
             '-c:v','libx264','-preset','ultrafast','-crf','26','-t','45',out]
    
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
                    tg_edit(prog_mid,f'📤 <b>جاري الرفع...</b>\n\n{bar} {p}%\n\n⚡ لا تغلق التطبيق')
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
            prog=tg('📤 <b>جاري الرفع على YouTube...</b>\n\n░░░░░░░░░░ 0%\n\n⚡ لا تغلق التطبيق')
            prog_mid=prog.get('result',{}).get('message_id')
            tg_edit(mid,'✅ <b>تمت الموافقة — جاري الرفع...</b>')
            def up():
                v=pending.get(vid,{})
                try:
                    url=upload_yt(v['video_path'],v['script_data'],prog_mid)
                    if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
                    pending.pop(vid,None); save_pending(pending)
                    tg_edit(prog_mid,f'🎉 <b>تم النشر!</b>\n\n🔗 {url}\n\n✅ {v["script_data"].get("title","")}')
                except Exception as e:
                    tg_edit(prog_mid,f'❌ فشل: {str(e)[:150]}\n\nاضغط ✅ مرة أخرى')
            threading.Thread(target=up).start()
        elif action=='reject' and vid in pending:
            v=pending.pop(vid,{}); save_pending(pending)
            if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
            tg_edit(mid,'❌ تم الرفض. أرسل موضوع جديد.')
        elif action=='regen' and vid in pending:
            topic=pending[vid].get('topic','معنى الحياة'); v=pending.pop(vid,{}); save_pending(pending)
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
            tg('👋 <b>فلسفة ديزاد Bot 🎬</b>\n\n🍌 فواكه تتكلم بالدارجة الجزائرية!\n\nأرسل موضوع:\n<i>معنى الحياة</i>\n<i>سر السعادة</i>\n<i>الحب والخيانة</i>\n\nأو:\n/status - حالة السيرفر\n/token - توكن YouTube')
        elif text=='/status':
            tg(f'📊 <b>الحالة:</b>\nYouTube: {"✅" if os.path.exists(TOKENS_FILE) else "❌"}\nD-ID: {"✅ متاح" if DID_KEY else "❌"}\nGroq: {"✅" if GROQ_KEY else "❌"}\nفيديوات انتظار: {len(pending)}')
        elif text=='/token':
            if os.path.exists(TOKENS_FILE):
                with open(TOKENS_FILE) as f: dt=f.read()
                tg(f'🔑 <b>YouTube Token:</b>\n<code>{dt}</code>\n\nأضفه في Render كـ YOUTUBE_TOKENS')
            else:
                tg('❌ مش مربوط\nhttps://zeus-video-server.onrender.com/auth')
        elif text and not text.startswith('/'):
            topic=text.strip()
            tg(f'🎬 <b>جاري التوليد:</b> {topic}\n\n🎙️ صوت جزائري\n🍌 فاكهة متحركة (D-ID)\n⏳ 2-3 دقائق...')
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
    return jsonify({'status':'✅ فلسفة ديزاد v4 — D-ID Edition','youtube':'✅' if os.path.exists(TOKENS_FILE) else'❌ /auth','did':'✅ متاح'})

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
    tg('🎉 <b>تم ربط YouTube!</b>\nابعث /token للحصول على التوكن الدائم')
    return'<h1 style="color:green;text-align:center;font-family:sans-serif;margin-top:100px">✅ تم ربط YouTube!<br>ابعث /token للبوت</h1>'

@app.route('/setup_webhook')
def sw():
    r=requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return jsonify(r.json())

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

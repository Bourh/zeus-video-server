import os,json,time,uuid,subprocess,textwrap,threading,re,asyncio
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
REDIRECT_URI='https://zeus-video-server.onrender.com/oauth/callback'
SCOPES=['https://www.googleapis.com/auth/youtube.upload']
TOKENS_FILE='youtube_tokens.json'
PENDING_FILE='pending.json'
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'
AR_VOICES=['ar-DZ-AminaNeural','ar-SA-ZariyahNeural','ar-EG-ShakirNeural']

CHARACTERS={
'موزة':{'search':'banana','emoji':'🍌','color':'Yellow'},
'تفاحة':{'search':'apple','emoji':'🍎','color':'Red'},
'ليمونة':{'search':'lemon','emoji':'🍋','color':'Yellow'},
'برتقالة':{'search':'orange','emoji':'🍊','color':'Orange'},
'بطيخة':{'search':'watermelon','emoji':'🍉','color':'Red'},
'طماطم':{'search':'tomato','emoji':'🍅','color':'Red'},
'خيارة':{'search':'cucumber','emoji':'🥒','color':'Green'},
'بصلة':{'search':'onion','emoji':'🧅','color':'Brown'},
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

    caption=(f"🎬 <b>{sd.get('title','')}</b>\n"
             f"🎭 {sd.get('character','')} {sd.get('emoji','')}\n"
             f"📝 {v.get('topic','')}\n\n"
             f"{sd.get('hashtags','')}\n\n"
             f"👆 <b>حمّل الفيديو للتيك توك والانستغرام!</b>")

    tg_send_video(v['video_path'],caption)

    msg=(f"⚡ <b>إجراءات الفيديو (YouTube):</b>\n\n"
         f"📌 {sd.get('title','')}\n"
         f"<i>{sd.get('script','')[:150]}...</i>")

    kb={'inline_keyboard':[[
        {'text':'✅ انشر في YouTube','callback_data':f'approve:{vid}'},
        {'text':'❌ احذف','callback_data':f'reject:{vid}'},
    ],[
        {'text':'🔄 أعد الكتابة','callback_data':f'regen:{vid}'}
    ]]}
    res=tg(msg,kb)
    if res.get('ok'): pending[vid]['tg_mid']=res['result']['message_id']; save_pending(pending)

def gen_script(topic):
    import random
    char_name=random.choice(list(CHARACTERS.keys()))
    char_info=CHARACTERS[char_name]
    prompt=f'''اكتب سكريبت فيديو 25-35 ثانية لصفحة "فلسفة ديزاد".
الشخصية: {char_name} {char_info["emoji"]} تتكلم بالدارجة الجزائرية العميقة والمضحكة.
الموضوع: {topic}
استخدم: راني، واش، بصح، والو، كاش، يزي، ياخويا.

JSON فقط:
{{"title":"عنوان جذاب","character":"{char_name}","emoji":"{char_info['emoji']}","color":"{char_info['color']}","script":"النص كامل هنا","hashtags":"#فلسفة_ديزاد #الجزائر","description":"وصف قصير"}}'''
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
            c=edge_tts.Communicate(text,voice,rate='+5%')
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

def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'
    audio=f'videos/{vid}.mp3'
    
    char=sd.get('character','الفيلسوف').replace("'",' ')
    emoji=sd.get('emoji','🍌')
    color=sd.get('color','Yellow')
    script=sd.get('script','فلسفة ديزاد')
    
    # تحسين النص باش يجي في الوسط وما يخرجش من الشاشة
    lines='\n'.join(textwrap.wrap(script,width=22))
    has_audio=make_audio(script,audio)

    # فلتر لصناعة خلفية متحركة واحترافية + دمج النص بطريقة شابة
    vf=(f"format=yuv420p,"
        f"drawtext=text='{emoji}':fontsize=180:x=(w-text_w)/2:y=200:fontcolor=white:shadowcolor=black:shadowx=5:shadowy=5,"
        f"drawtext=text='{char}':fontsize=60:x=(w-text_w)/2:y=420:fontcolor={color}:shadowcolor=black:shadowx=3:shadowy=3:box=1:boxcolor=black@0.6:boxborderw=15,"
        f"drawtext=text='{lines}':fontsize=48:x=(w-text_w)/2:y=650:fontcolor=white:line_spacing=20:shadowcolor=black:shadowx=3:shadowy=3:text_align=C:box=1:boxcolor=black@0.5:boxborderw=20,"
        f"drawtext=text='فلسفة ديزاد 🎬':fontsize=40:x=(w-text_w)/2:y=1750:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2")

    if has_audio and os.path.exists(audio):
        # يخدم خلفية متحركة بالألوان (Gradients) مع الصوت
        cmd=['ffmpeg','-y',
             '-f','lavfi','-i','color=c=#111111:size=1080x1920:duration=45:rate=30',
             '-i',audio,
             '-vf',vf,
             '-c:v','libx264','-preset','fast','-crf','22',
             '-c:a','aac','-b:a','192k',
             '-shortest',out]
    else:
        # إذا ماكانش صوت، يخدم فيديو ساكت بصح متحرك
        cmd=['ffmpeg','-y',
             '-f','lavfi','-i','color=c=#111111:size=1080x1920:duration=30:rate=30',
             '-vf',vf,
             '-c:v','libx264','-preset','fast','-crf','22',out]

    r=subprocess.run(cmd,capture_output=True,timeout=240)
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
                     'tags':['فلسفة','الجزائر','فلسفة_ديزاد','ضحك'],'categoryId':'22'},
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
            tg_edit(mid,'❌ تم الرفض وحذف الفيديو.')
        elif action=='regen' and vid in pending:
            topic=pending[vid].get('topic','معنى الحياة')
            v=pending.pop(vid,{}); save_pending(pending)
            if os.path.exists(v.get('video_path','')): os.remove(v['video_path'])
            tg_edit(mid,f'🔄 <b>إعادة توليد: {topic}</b>\n⏳ دقيقة...')
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
            tg('👋 <b>فلسفة ديزاد Bot v6 🎬</b>\n\n🍌 فواكه تتكلم بالدارجة بصوت وصورة مقلشة!\n\nأرسل موضوع:\n<i>الزلط والتفرعين</i>\n<i>القراية في دزاير</i>')
        elif text=='/status':
            tg(f'📊 YouTube: {"✅" if os.path.exists(TOKENS_FILE) else "❌"}\nانتظار: {len(pending)} فيديو')
        elif text and not text.startswith('/'):
            topic=text.strip()
            tg(f'🎬 <b>جاري الخدمة:</b> {topic}\n\n🎙️ صوت + 🎨 فيديو جديد...\n⏳ عس التيليجرام درك يوصلك الفيديو واجد!')
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
def home(): return jsonify({'status':'✅ فلسفة ديزاد v6'})

@app.route('/setup_webhook')
def sw():
    r=requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return jsonify(r.json())

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

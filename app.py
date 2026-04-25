import os,json,time,uuid,subprocess,textwrap,threading,re,asyncio
from flask import Flask,request,jsonify,redirect,session
import requests,edge_tts

app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY','fs2024')
GROQ_KEY=os.environ.get('GROQ_API_KEY')
TG_TOKEN=os.environ.get('TG_TOKEN')
TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
PENDING_FILE='pending.json'
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'

CHARACTERS={'موزة':'🍌','تفاحة':'🍎','ليمونة':'🍋','برتقالة':'🍊','بطيخة':'🍉','طماطم':'🍅','خيارة':'🥒','بصلة':'🧅'}

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

def gen_script(topic):
    import random
    char=random.choice(list(CHARACTERS.keys()))
    prompt=f'''اكتب سكريبت 30 ثانية لـ "فلسفة ديزاد". الشخصية: {char} {CHARACTERS[char]} بالدارجة الجزائرية. الموضوع: {topic}. JSON فقط: {{"title":"عنوان","script":"النص هنا","char":"{char}","emoji":"{CHARACTERS[char]}"}}'''
    r=requests.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization':f'Bearer {GROQ_KEY}'},
        json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}]},timeout=30)
    clean=r.json()['choices'][0]['message']['content'].strip()
    m=re.search(r'\{[\s\S]*\}',clean)
    return json.loads(m.group(0)) if m else{}

async def make_audio_async(text,path):
    c=edge_tts.Communicate(text,'ar-DZ-AminaNeural')
    await c.save(path)
    return os.path.exists(path)

def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'; audio=f'videos/{vid}.mp3'
    script=sd.get('script','فلسفة ديزاد')
    lines='\n'.join(textwrap.wrap(script,width=25))
    
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(make_audio_async(script,audio)); loop.close()

    # أمر FFmpeg مبسط جداً باش ما يغلطش السيرفر
    vf=(f"drawtext=text='{sd.get('emoji','🎬')}':fontsize=120:x=(w-text_w)/2:y=250:fontcolor=white,"
        f"drawtext=text='{lines}':fontsize=45:x=(w-text_w)/2:y=600:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=20")
    
    cmd=['ffmpeg','-y','-f','lavfi','-i','color=c=#1a1a1a:s=720x1280:d=30','-i',audio,
         '-vf',vf,'-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',out]
    
    subprocess.run(cmd,capture_output=True)
    if os.path.exists(audio): os.remove(audio)
    return out

@app.route('/telegram',methods=['POST'])
def webhook():
    data=request.json or{}
    if 'message' in data:
        topic=data['message'].get('text','')
        if topic and not topic.startswith('/'):
            tg(f'⏳ جاري التوليد: {topic}...')
            def worker():
                try:
                    sd=gen_script(topic); vid=str(uuid.uuid4())[:8]; vp=make_video(sd,vid)
                    with open(vp,'rb') as f:
                        requests.post(f'{TG_API}/sendVideo',data={'chat_id':TG_CHAT_ID,'caption':f'✅ {sd["title"]}'},files={'video':f})
                    os.remove(vp)
                except Exception as e: tg(f'❌ خطأ: {str(e)[:100]}')
            threading.Thread(target=worker).start()
    return jsonify({'ok':True})

@app.route('/setup_webhook')
def sw():
    requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return 'OK'

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

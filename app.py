import os,json,time,uuid,subprocess,threading,re,asyncio
from flask import Flask,request,jsonify
import requests,edge_tts

app=Flask(__name__)
GROQ_KEY=os.environ.get('GROQ_API_KEY')
TG_TOKEN=os.environ.get('TG_TOKEN')
TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'

CHARACTERS={'موزة':'🍌','تفاحة':'🍎','ليمونة':'🍋','برتقالة':'🍊','بطيخة':'🍉'}

def tg(text):
    requests.post(f'{TG_API}/sendMessage',json={'chat_id':TG_CHAT_ID,'text':text,'parse_mode':'HTML'})

def gen_script(topic):
    import random
    char=random.choice(list(CHARACTERS.keys()))
    prompt=f"اكتب سكريبت 20 ثانية لـ 'فلسفة ديزاد'. الشخصية: {char} {CHARACTERS[char]} بالدارجة الجزائرية. الموضوع: {topic}. ارجع JSON فقط: {{\"title\":\"عنوان\",\"script\":\"النص\",\"emoji\":\"{CHARACTERS[char]}\"}}"
    r=requests.post('https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization':f'Bearer {GROQ_KEY}'},
        json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}]},timeout=30)
    m=re.search(r'\{[\s\S]*\}',r.json()['choices'][0]['message']['content'])
    return json.loads(m.group(0)) if m else{}

async def make_audio(text,path):
    c=edge_tts.Communicate(text,'ar-DZ-AminaNeural')
    await c.save(path)

def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'; audio=f'videos/{vid}.mp3'
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(make_audio(sd['script'],audio)); loop.close()

    # أمر بسيط جداً بدون خطوط خارجية لتفادي الخطأ
    cmd=['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=720x1280:d=20','-i',audio,
         '-vf',f"drawtext=text='{sd['emoji']}':fontsize=100:x=(w-text_w)/2:y=400:fontcolor=white",
         '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',out]
    
    subprocess.run(cmd,capture_output=True)
    if os.path.exists(audio): os.remove(audio)
    return out

@app.route('/telegram',methods=['POST'])
def webhook():
    data=request.json or{}
    if 'message' in data and 'text' in data['message']:
        topic=data['message']['text']
        if not topic.startswith('/'):
            def worker():
                try:
                    sd=gen_script(topic); vid=str(uuid.uuid4())[:8]; vp=make_video(sd,vid)
                    with open(vp,'rb') as f:
                        requests.post(f'{TG_API}/sendVideo',data={'chat_id':TG_CHAT_ID,'caption':f"✅ {sd['title']}"},files={'video':f})
                    os.remove(vp)
                except Exception as e: tg(f"❌ Error: {str(e)[:50]}")
            threading.Thread(target=worker).start()
    return jsonify({'ok':True})

@app.route('/setup_webhook')
def sw():
    requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return 'OK'

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

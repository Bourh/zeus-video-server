import os,json,time,uuid,subprocess,threading,re
from flask import Flask,request,jsonify
import requests
from gtts import gTTS

app=Flask(__name__)
GROQ_KEY=os.environ.get('GROQ_API_KEY')
TG_TOKEN=os.environ.get('TG_TOKEN')
TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'

CHARACTERS={'موزة':'🍌','تفاحة':'🍎','ليمونة':'🍋','برتقالة':'🍊'}

def tg(text):
    requests.post(f'{TG_API}/sendMessage',json={'chat_id':TG_CHAT_ID,'text':text,'parse_mode':'HTML'})

def gen_script(topic):
    import random
    char=random.choice(list(CHARACTERS.keys()))
    prompt=f"اكتب سكريبت 15 ثانية لـ 'فلسفة ديزاد'. الشخصية: {char} {CHARACTERS[char]} بالدارجة الجزائرية. الموضوع: {topic}. ارجع JSON فقط: {{\"title\":\"عنوان\",\"script\":\"النص\",\"emoji\":\"{CHARACTERS[char]}\"}}"
    try:
        r=requests.post('https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization':f'Bearer {GROQ_KEY}'},
            json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}]},timeout=30)
        m=re.search(r'\{[\s\S]*\}',r.json()['choices'][0]['message']['content'])
        return json.loads(m.group(0)) if m else{}
    except: return {}

def make_video(sd,vid):
    os.makedirs('videos',exist_ok=True)
    out=f'videos/{vid}.mp4'; audio=f'videos/{vid}.mp3'
    
    # استعمال Google TTS بدل Edge TTS لتفادي بلوك 403
    tts = gTTS(text=sd['script'], lang='ar')
    tts.save(audio)

    # فيديو بسيط جدا: ايموجي فوق خلفية سوداء مع الصوت
    cmd=['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=640x1136:d=15','-i',audio,
         '-vf',f"drawtext=text='{sd['emoji']}':fontsize=100:x=(w-text_w)/2:y=(h-text_h)/2:fontcolor=white",
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
                    sd=gen_script(topic)
                    if not sd: raise Exception("Script failed")
                    vid=str(uuid.uuid4())[:8]; vp=make_video(sd,vid)
                    with open(vp,'rb') as f:
                        requests.post(f'{TG_API}/sendVideo',data={'chat_id':TG_CHAT_ID,'caption':f"✅ {sd['title']}\n\n{sd['script']}"},files={'video':f})
                    if os.path.exists(vp): os.remove(vp)
                except Exception as e: tg(f"❌ خطأ تقني: {str(e)[:50]}")
            threading.Thread(target=worker).start()
    return jsonify({'ok':True})

@app.route('/setup_webhook')
def sw():
    requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'})
    return 'OK'

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

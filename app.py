import os,json,time,uuid,subprocess,textwrap,threading,re,asyncio
from flask import Flask,request,jsonify,redirect,session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
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
TG_API=f'https://api.telegram.org/bot{TG_TOKEN}'
pending={}

# أصوات عربية طبيعية
AR_VOICES=['ar-DZ-AminaNeural','ar-SA-ZariyahNeural','ar-MA-MounaNeural','ar-EG-ShakirNeural']

def tg(text,kb=None):
 d={'chat_id':TG_CHAT_ID,'text':text,'parse_mode':'HTML'}
 if kb:d['reply_markup']=json.dumps(kb)
 try:r=requests.post(f'{TG_API}/sendMessage',json=d,timeout=10);return r.json()
 except:return{}

def tg_edit(mid,text):
 try:requests.post(f'{TG_API}/editMessageText',json={'chat_id':TG_CHAT_ID,'message_id':mid,'text':text,'parse_mode':'HTML'},timeout=10)
 except:pass

def notify(vid):
 v=pending.get(vid)
 if not v:return
 sd=v['script_data']
 msg=(f"🎬 <b>فيديو جاهز!</b>\n\n📌 <b>{sd.get('title','')}</b>\n🎭 {sd.get('character','')} {sd.get('emoji','')}\n📝 {v.get('topic','')}\n\n<i>{sd.get('script','')[:300]}...</i>\n\n{sd.get('hashtags','')}")
 kb={'inline_keyboard':[[{'text':'✅ نشر على YouTube','callback_data':f'approve:{vid}'},{'text':'❌ رفض','callback_data':f'reject:{vid}'}],[{'text':'🔄 أعد الكتابة','callback_data':f'regen:{vid}'}]]}
 res=tg(msg,kb)
 if res.get('ok'):pending[vid]['tg_mid']=res['result']['message_id']

def gen_script(topic):
 prompt=f'''اكتب سكريبت فيديو 30-45 ثانية لصفحة فلسفة ديزاد عن: {topic}
فاكهة تتكلم بالدارجة الجزائرية الحقيقية (راني،واش،بصح،والو،كاش،يزي)
الأسلوب: فلسفة خفيفة وطريفة، نهاية مفاجئة
JSON فقط:
{{"title":"عنوان جذاب","character":"اسم الشخصية","emoji":"🍌","script":"النص كامل بالدارجة","hashtags":"#فلسفة_ديزاد #الجزائر","description":"وصف","fruit_search":"كلمة انجليزية للبحث عن صورة الفاكهة مثل banana"}}'''
 r=requests.post('https://api.groq.com/openai/v1/chat/completions',
  headers={'Authorization':f'Bearer {GROQ_KEY}','Content-Type':'application/json'},
  json={'model':'llama-3.3-70b-versatile','messages':[{'role':'user','content':prompt}],'max_tokens':800,'temperature':0.9},
  timeout=30)
 text=r.json()['choices'][0]['message']['content']
 clean=text.replace('```json','').replace('```','').strip()
 m=re.search(r'\{[\s\S]*\}',clean)
 return json.loads(m.group(0)) if m else{}

async def make_audio_async(text,path):
 for voice in AR_VOICES:
  try:
   communicate=edge_tts.Communicate(text,voice)
   await communicate.save(path)
   if os.path.exists(path) and os.path.getsize(path)>1000:return True
  except:continue
 return False

def make_audio(text,path):
 try:
  loop=asyncio.new_event_loop()
  asyncio.set_event_loop(loop)
  result=loop.run_until_complete(make_audio_async(text,path))
  loop.close()
  return result
 except:return False

def get_fruit_image(search_term):
 try:
  # Unsplash free API
  r=requests.get(f'https://source.unsplash.com/1080x1920/?{search_term},fruit,cartoon',
   timeout=10,allow_redirects=True)
  if r.status_code==200 and len(r.content)>5000:
   path=f'/tmp/{uuid.uuid4()}.jpg'
   with open(path,'wb') as f:f.write(r.content)
   return path
 except:pass
 return None

def make_video(sd,vid):
 os.makedirs('videos',exist_ok=True)
 out=f'videos/{vid}.mp4'
 audio=f'videos/{vid}_audio.mp3'
 emoji=sd.get('emoji','🍌')
 char=sd.get('character','الفيلسوف').replace("'",' ').replace(':',' ')
 script=sd.get('script','فلسفة ديزاد')
 fruit_search=sd.get('fruit_search','fruit')
 lines='\n'.join(textwrap.wrap(script,width=26)[:7]).replace("'",' ').replace(':',' ').replace('%',' ')

 # توليد الصوت
 has_audio=make_audio(script,audio)

 # محاولة جلب صورة الفاكهة
 img_path=get_fruit_image(fruit_search)

 # بناء الـ vf filter
 if img_path and os.path.exists(img_path):
  # خلفية من صورة الفاكهة مع overlay نص
  vf=(f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
      f"colorize=hue=240:saturation=0.3:lightness=-0.4,"
      f"drawtext=text='{emoji}':fontsize=140:x=(w-text_w)/2:y=100:fontcolor=white:shadowcolor=black:shadowx=3:shadowy=3,"
      f"drawtext=text='{char}':fontsize=55:x=(w-text_w)/2:y=320:fontcolor=#FFD700:shadowcolor=black:shadowx=2:shadowy=2,"
      f"drawtext=text='{lines}':fontsize=42:x=60:y=560:fontcolor=white:line_spacing=14:shadowcolor=black:shadowx=2:shadowy=2,"
      f"drawtext=text='فلسفة ديزاد 🎬':fontsize=38:x=(w-text_w)/2:y=1820:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2")
  video_input=['-i',img_path]
  video_filter=['-vf',vf]
 else:
  # خلفية gradient
  vf=(f"drawtext=text='{emoji}':fontsize=160:x=(w-text_w)/2:y=180:fontcolor=white:shadowcolor=black:shadowx=3:shadowy=3,"
      f"drawtext=text='{char}':fontsize=52:x=(w-text_w)/2:y=400:fontcolor=#FFD700:shadowcolor=black:shadowx=2:shadowy=2,"
      f"drawtext=text='{lines}':fontsize=40:x=70:y=640:fontcolor=white:line_spacing=12:shadowcolor=black:shadowx=2:shadowy=2,"
      f"drawtext=text='فلسفة ديزاد 🎬':fontsize=36:x=(w-text_w)/2:y=1820:fontcolor=#AAAAAA")
  video_input=['-f','lavfi','-i','color=c=#1a0a2e:size=1080x1920:duration=50:rate=30']
  video_filter=['-vf',vf]

 if has_audio and os.path.exists(audio):
  if img_path:
   cmd=['ffmpeg','-y','-loop','1']+video_input+['-i',audio]+video_filter+['-c:v','libx264','-preset','ultrafast','-crf','26','-c:a','aac','-shortest','-t','50',out]
  else:
   cmd=['ffmpeg','-y']+video_input+['-i',audio]+video_filter+['-c:v','libx264','-preset','ultrafast','-crf','26','-c:a','aac','-shortest',out]
 else:
  if img_path:
   cmd=['ffmpeg','-y','-loop','1']+video_input+video_filter+['-c:v','libx264','-preset','ultrafast','-crf','26','-t','45',out]
  else:
   cmd=['ffmpeg','-y']+video_input+video_filter+['-c:v','libx264','-preset','ultrafast','-crf','26',out]

 r=subprocess.run(cmd,capture_output=True,timeout=240)
 if audio and os.path.exists(audio):os.remove(audio)
 if img_path and os.path.exists(img_path):os.remove(img_path)
 if r.returncode!=0:raise Exception(f'FFmpeg:{r.stderr.decode()[:300]}')
 return out

def upload_yt(path,sd):
 if not os.path.exists(TOKENS_FILE):raise Exception('YouTube غير مربوط')
 with open(TOKENS_FILE) as f:t=json.load(f)
 c=Credentials(token=t['access_token'],refresh_token=t['refresh_token'],token_uri='https://oauth2.googleapis.com/token',client_id=CLIENT_ID,client_secret=CLIENT_SECRET,scopes=SCOPES)
 yt=build('youtube','v3',credentials=c)
 body={'snippet':{'title':sd.get('title','فلسفة ديزاد')[:100],'description':f"{sd.get('description','')}\n{sd.get('hashtags','')}","tags":['فلسفة','الجزائر','فلسفة_ديزاد','ضحك'],'categoryId':'22'},'status':{'privacyStatus':'public','selfDeclaredMadeForKids':False}}
 media=MediaFileUpload(path,mimetype='video/mp4',resumable=True)
 req=yt.videos().insert(part='snippet,status',body=body,media_body=media)
 resp=None
 while resp is None:_,resp=req.next_chunk()
 return f"https://youtu.be/{resp['id']}"

@app.route('/telegram',methods=['POST'])
def webhook():
 data=request.json or{}
 if 'callback_query' in data:
  cb=data['callback_query'];cid=cb['id'];parts=cb.get('data','').split(':',1);action=parts[0];vid=parts[1] if len(parts)>1 else''
  mid=cb['message']['message_id']
  requests.post(f'{TG_API}/answerCallbackQuery',json={'callback_query_id':cid,'text':'⏳'},timeout=5)
  if action=='approve' and vid in pending:
   tg_edit(mid,'⏳ <b>جاري الرفع على YouTube...</b>')
   def up():
    v=pending[vid]
    try:
     url=upload_yt(v['video_path'],v['script_data'])
     if os.path.exists(v['video_path']):os.remove(v['video_path'])
     tg(f"🎉 <b>تم النشر!</b>\n🔗 {url}")
    except Exception as e:tg(f'❌ {str(e)[:150]}')
   threading.Thread(target=up).start()
  elif action=='reject' and vid in pending:
   v=pending.pop(vid,{})
   if os.path.exists(v.get('video_path','')):os.remove(v['video_path'])
   tg_edit(mid,'❌ تم الرفض. أرسل موضوع جديد.')
  elif action=='regen' and vid in pending:
   topic=pending[vid].get('topic','معنى الحياة');v=pending.pop(vid,{})
   if os.path.exists(v.get('video_path','')):os.remove(v['video_path'])
   tg_edit(mid,f'🔄 إعادة توليد: {topic}')
   def rg():
    try:
     sd=gen_script(topic);nid=str(uuid.uuid4())[:8];vp=make_video(sd,nid)
     pending[nid]={'id':nid,'script_data':sd,'video_path':vp,'topic':topic,'status':'pending','created_at':time.time()}
     notify(nid)
    except Exception as e:tg(f'❌ {str(e)[:150]}')
   threading.Thread(target=rg).start()
 elif 'message' in data:
  text=data['message'].get('text','')
  if text=='/start':tg('👋 <b>فلسفة ديزاد Bot</b>\n\nأرسل موضوع وأنا أصنع فيديو مع صوت وصورة!\n\nمثال:\n<i>الموزة وسر السعادة</i>\n<i>الطماطم وفلسفة الحياة</i>')
  elif text=='/status':tg(f'📊 YouTube:{"✅" if os.path.exists(TOKENS_FILE) else "❌"}\nGroq:{"✅" if GROQ_KEY else "❌"}\nالصوت: ✅ Edge-TTS\nالصور: ✅ Unsplash')
  elif text and not text.startswith('/'):
   topic=text.strip();tg(f'🎬 جاري التوليد: <b>{topic}</b>\n⏳ 60-90 ثانية...')
   def gn():
    try:
     sd=gen_script(topic)
     if not sd:tg('❌ فشل التوليد.');return
     vid=str(uuid.uuid4())[:8];vp=make_video(sd,vid)
     pending[vid]={'id':vid,'script_data':sd,'video_path':vp,'topic':topic,'status':'pending','created_at':time.time()}
     notify(vid)
    except Exception as e:tg(f'❌ {str(e)[:150]}')
   threading.Thread(target=gn).start()
 return jsonify({'ok':True})

@app.route('/')
def home():return jsonify({'status':'✅ فلسفة ديزاد v2','youtube':'✅' if os.path.exists(TOKENS_FILE) else'❌ /auth','audio':'✅ Edge-TTS','images':'✅ Unsplash'})

@app.route('/auth')
def auth():
 flow=Flow.from_client_config({'web':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token','redirect_uris':[REDIRECT_URI]}},scopes=SCOPES,redirect_uri=REDIRECT_URI)
 url,state=flow.authorization_url(access_type='offline',prompt='consent');session['state']=state;return redirect(url)

@app.route('/oauth/callback')
def cb():
 flow=Flow.from_client_config({'web':{'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token','redirect_uris':[REDIRECT_URI]}},scopes=SCOPES,redirect_uri=REDIRECT_URI,state=session.get('state'))
 flow.fetch_token(authorization_response=request.url);creds=flow.credentials
 with open(TOKENS_FILE,'w') as f:json.dump({'access_token':creds.token,'refresh_token':creds.refresh_token},f)
 tg('🎉 <b>تم ربط YouTube!</b>');return'<h1 style="color:green;text-align:center;font-family:sans-serif;margin-top:100px">✅ تم ربط YouTube!</h1>'

@app.route('/setup_webhook')
def sw():r=requests.post(f'{TG_API}/setWebhook',json={'url':'https://zeus-video-server.onrender.com/telegram'});return jsonify(r.json())

if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))

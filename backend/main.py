import os,json,uuid,subprocess
from pathlib import Path
import psycopg
from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
DB=os.environ['DATABASE_URL']; DATA=Path('/app/data'); DATA.mkdir(exist_ok=True)
app=FastAPI(title='AI TikTok Shop Autopilot'); app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
def conn(): return psycopg.connect(DB)
@app.on_event('startup')
def init():
 with conn() as c:
  c.execute('''CREATE TABLE IF NOT EXISTS products(id UUID PRIMARY KEY,name TEXT NOT NULL,price NUMERIC,commission NUMERIC,url TEXT,notes TEXT,created_at TIMESTAMPTZ DEFAULT now());''')
  c.execute('''CREATE TABLE IF NOT EXISTS trends(id UUID PRIMARY KEY,product_id UUID REFERENCES products(id),score NUMERIC,momentum NUMERIC,competition NUMERIC,data JSONB,created_at TIMESTAMPTZ DEFAULT now());''')
  c.execute('''CREATE TABLE IF NOT EXISTS videos(id UUID PRIMARY KEY,product_id UUID REFERENCES products(id),variant TEXT,script JSONB,file_path TEXT,status TEXT DEFAULT 'draft',created_at TIMESTAMPTZ DEFAULT now());''')
  c.execute('''CREATE TABLE IF NOT EXISTS queue(id UUID PRIMARY KEY,video_id UUID REFERENCES videos(id),scheduled_at TIMESTAMPTZ,status TEXT DEFAULT 'queued',external_post_id TEXT);''')
class Product(BaseModel): name:str; price:float|None=None; commission:float|None=None; url:str|None=None; notes:str|None=None
class Evidence(BaseModel): evidence:dict={}
def ai(prompt):
 key=os.getenv('GEMINI_API_KEY')
 if not key: raise HTTPException(500,'GEMINI_API_KEY is not configured')
 return genai.Client(api_key=key).models.generate_content(model=os.getenv('GEMINI_MODEL','gemini-2.5-flash'),contents=prompt).text
@app.get('/health')
def health(): return {'ok':True}
@app.get('/products')
def products():
 with conn() as c: rows=c.execute('SELECT id,name,price,commission,url,notes FROM products ORDER BY created_at DESC').fetchall()
 return [dict(zip(['id','name','price','commission','url','notes'],map(str,r))) for r in rows]
@app.post('/products')
def add(p:Product):
 i=uuid.uuid4()
 with conn() as c: c.execute('INSERT INTO products VALUES(%s,%s,%s,%s,%s,%s,now())',(i,p.name,p.price,p.commission,p.url,p.notes))
 return {'id':str(i)}
@app.post('/products/{pid}/trend')
def trend(pid:str,e:Evidence):
 with conn() as c: p=c.execute('SELECT name,price,commission,url,notes FROM products WHERE id=%s',(pid,)).fetchone()
 if not p: raise HTTPException(404,'Product not found')
 prompt=f'''Analyze this TikTok Shop product using ONLY supplied evidence. Do not invent live metrics. Return JSON only with score,momentum,competition,decision,reasons,content_angles. Product={p}; evidence={json.dumps(e.evidence,ensure_ascii=False)}'''
 raw=ai(prompt); raw=raw[raw.find('{'):raw.rfind('}')+1]; d=json.loads(raw); tid=uuid.uuid4()
 with conn() as c: c.execute('INSERT INTO trends VALUES(%s,%s,%s,%s,%s,%s,now())',(tid,pid,d['score'],d['momentum'],d['competition'],json.dumps(d,ensure_ascii=False)))
 return d
@app.post('/products/{pid}/scripts')
def scripts(pid:str):
 with conn() as c: p=c.execute('SELECT name,price,commission,notes FROM products WHERE id=%s',(pid,)).fetchone()
 if not p: raise HTTPException(404,'Product not found')
 raw=ai(f'''Create 3 ORIGINAL Thai TikTok Shop video concepts for {p}. Do not make unsupported medical/financial/guaranteed claims. JSON array only. Each item: variant,hook,scenes[{"seconds":3,"visual":"...","voiceover":"..."}],caption,hashtags,cta.'''); raw=raw[raw.find('['):raw.rfind(']')+1]; arr=json.loads(raw); ids=[]
 with conn() as c:
  for x in arr:
   i=uuid.uuid4(); c.execute('INSERT INTO videos(id,product_id,variant,script) VALUES(%s,%s,%s,%s)',(i,pid,x['variant'],json.dumps(x,ensure_ascii=False))); ids.append(str(i))
 return {'video_ids':ids,'scripts':arr}
@app.get('/videos')
def videos():
 with conn() as c: rows=c.execute('SELECT v.id,v.product_id,p.name,v.variant,v.status FROM videos v JOIN products p ON p.id=v.product_id ORDER BY v.created_at DESC').fetchall()
 return [dict(zip(['id','product_id','product','variant','status'],map(str,r))) for r in rows]
@app.post('/videos/{vid}/render')
def render(vid:str):
 with conn() as c: r=c.execute('SELECT script FROM videos WHERE id=%s',(vid,)).fetchone()
 if not r: raise HTTPException(404,'Video not found')
 try: duration=max(5,min(60,sum(float(s.get('seconds',3)) for s in r[0]['scenes'])))
 except: duration=15
 out=DATA/f'{vid}.mp4'; subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=1080x1920:r=30','-t',str(duration),'-c:v','libx264','-pix_fmt','yuv420p',str(out)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 with conn() as c: c.execute("UPDATE videos SET file_path=%s,status='ready' WHERE id=%s",(str(out),vid))
 return {'file':str(out),'status':'ready'}
@app.post('/queue/{vid}')
def enqueue(vid:str):
 q=uuid.uuid4()
 with conn() as c: c.execute("INSERT INTO queue(id,video_id) VALUES(%s,%s)",(q,vid))
 return {'id':str(q),'status':'queued'}
@app.get('/queue')
def queue():
 with conn() as c: rows=c.execute('SELECT q.id,q.video_id,q.status,p.name,v.variant FROM queue q JOIN videos v ON v.id=q.video_id JOIN products p ON p.id=v.product_id ORDER BY q.scheduled_at NULLS LAST').fetchall()
 return [dict(zip(['id','video_id','status','product','variant'],map(str,r))) for r in rows]

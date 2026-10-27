from __future__ import annotations
import argparse, hashlib, json, logging, re, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'events.sqlite3'
TZ = timezone(timedelta(hours=8))
CITIES = '广州 深圳 珠海 佛山 东莞 中山 惠州 江门 肇庆 汕头 潮州 揭阳 汕尾 湛江 茂名 阳江 清远 韶关 河源 梅州 云浮'.split()
SOURCES = [('演唱会','https://www.dahepiao.com/yanchupiaowu1/ych/'),('音乐节','https://www.dahepiao.com/yanchupiaowu1_4300_0')]
lock = threading.Lock()
app = Flask(__name__, static_folder='static')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
def now(): return datetime.now(TZ).isoformat(timespec='seconds')
def conn():
 c=sqlite3.connect(DB, timeout=30); c.row_factory=sqlite3.Row; return c
def init():
 with conn() as c:
  c.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started TEXT, finished TEXT, status TEXT, count INTEGER, errors TEXT)')
def fetch(url):
 r=requests.get(url,timeout=(10,25),headers={'User-Agent':'GuangdongLiveMonitor/1.0 (public event index; daily refresh)'})
 r.raise_for_status(); r.encoding=r.apparent_encoding
 s=BeautifulSoup(r.text,'html.parser')
 if not s.title: raise ValueError('页面结构异常')
 return s

def listing(s,kind):
 out=[]
 for a in s.select('.searchR a.title'):
  title=a.get_text(' ',strip=True)
  city=next((c for c in CITIES if '【'+c+'】' in title),None)
  if not city: continue
  box=a.parent
  out.append(dict(title=title,city=city,kind='音乐节' if '音乐节' in title else kind,url=urljoin('https://www.dahepiao.com',a['href']),list_time=box.select_one('.time').get_text(' ',strip=True) if box.select_one('.time') else ''))
 return out

def detail(item):
 s=fetch(item['url']); values={}
 for td in s.select('td'):
  label=td.get_text(' ',strip=True)
  if label in ['演出时间','演出场馆','开票时间','开售时间','预售时间']:
   sibling=td.find_next_sibling('td')
   if sibling: values[label]=sibling.get_text(' ',strip=True)
 date=values.get('演出时间','')
 venue=values.get('演出场馆','')
 text=s.get_text(' ',strip=True)
 if not date:
  m=re.search(r'演出时间[：:]\s*(.+?)(?:演出场馆|演出地点)',text)
  if m:date=m[1].strip()
 if not venue:
  m=re.search(r'演出场馆[：:]\s*(.+?)(?:演出票价|演出价格|温馨提示)',text)
  if m:venue=m[1].strip()
 if not date or not venue:raise ValueError('详情缺少演出时间或场馆: '+item['title'])
 location=s.select_one('meta[name="location"]')
 if location and '广东' not in location.get('content',''):raise ValueError('详情省份不匹配')
 dates=re.findall(r'20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}',date)
 def iso(d):return datetime.strptime(re.sub(r'[/.]','-',d),'%Y-%m-%d').date().isoformat()
 start=iso(dates[0]) if dates else None
 end=iso(dates[-1]) if dates else None
 # Retain date ranges such as 2026-10-30/31.
 m=re.search(r'(20\d{2})-(\d{1,2})-(\d{1,2})\s*[~/至—-]+\s*(?:(\d{1,2})-)?(\d{1,2})(?![\d-])',date)
 if m and len(dates)==1:
  try:
   month=int(m[4] or m[2]); year=int(m[1])+(1 if month<int(m[2]) else 0)
   end=datetime(year,month,int(m[5])).date().isoformat()
  except ValueError:pass
 sale=None
 for key in ['开票时间','开售时间','预售时间']:
  if re.search(r'20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}',values.get(key,'')):sale=values[key]
 # Only explicit labelled dates from event information, never generic sales instructions.
 info=s.select_one('.m-table2')
 for node in s.select('p'):
  t=node.get_text(' ',strip=True)
  m=re.search(r'(?:开票时间|开售时间|预售时间)\s*[：:]\s*(20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}日?(?:\s*\d{1,2}[:：]\d{2})?)',t)
  if m and len(t)<350:sale=m[1];break
 image=next((i.get('src') for i in s.select('img[src]') if 'img.dahepiao.com/uploads/' in i.get('src','')),None)
 description=s.select_one('meta[name="description"]')
 return {**item,'id':hashlib.sha256(item['url'].encode()).hexdigest()[:16],'date_text':date,'start_date':start,'end_date':end,'venue':venue,'sale_time':sale,'image':image,'description':description.get('content','') if description else '', 'source':'大河票务（第三方票务信息）','verified_at':now()}

def collect():
 if not lock.acquire(False):return False
 started=now(); errors=[]; items={}; verified=0
 try:
  with conn() as c:
   rid=c.execute('INSERT INTO runs(started,status,count,errors) VALUES(?,?,?,?)',(started,'running',0,'[]')).lastrowid
  for kind,base in SOURCES:
   try:
    s=fetch(base)
    if not s.select('.searchR a.title'):raise ValueError('列表结构变化或返回空白')
    for x in listing(s,kind):items[x['url']]=x
    pages={urljoin(base,a['href']).replace('http://','https://') for a in s.select('a[href]') if re.search(r'[?&]page=\d+',a['href'])}
    maximum=max([int(re.search(r'page=(\d+)',p)[1]) for p in pages] or [1])
    if maximum>80:raise ValueError('分页异常，需检查采集器')
    template=next(iter(pages),None)
    if template:
     for page in range(2,maximum+1):
      try:
       p=fetch(re.sub(r'page=\d+','page='+str(page),template))
       if not p.select('.searchR a.title'):raise ValueError('分页结构异常')
       for x in listing(p,kind):items[x['url']]=x
      except Exception as e:errors.append(f'{kind}第{page}页: {e}')
      time.sleep(.25)
   except Exception as e:errors.append(f'{kind}: {e}')
  with ThreadPoolExecutor(max_workers=3) as pool:
   jobs={pool.submit(detail,x):x for x in items.values()}
   for future in as_completed(jobs):
    try:
     item=future.result()
     with conn() as c:
      old=c.execute('SELECT payload FROM events WHERE id=?',(item['id'],)).fetchone()
      previous=json.loads(old[0]) if old else {}
      item['first_seen']=previous.get('first_seen',now())
      item['changed_at']=previous.get('changed_at',now())
      if any(item.get(k)!=previous.get(k) for k in ['date_text','venue','sale_time']):item['changed_at']=now()
      c.execute('INSERT OR REPLACE INTO events VALUES(?,?)',(item['id'],json.dumps(item,ensure_ascii=False)))
     verified+=1
    except Exception as e:errors.append(str(e))
  status='success' if not errors else ('partial' if verified else 'failed')
  with conn() as c:c.execute('UPDATE runs SET finished=?,status=?,count=?,errors=? WHERE id=?',(now(),status,verified,json.dumps(errors,ensure_ascii=False),rid))
  logging.info('Refresh %s: %s events, %s errors',status,verified,len(errors))
  return True
 finally:lock.release()

def next_run():
 t=datetime.now(TZ); target=t.replace(hour=8,minute=0,second=0,microsecond=0)
 if t>=target:target+=timedelta(days=1)
 return target.isoformat()
def scheduler():
 while True:
  try:
   with conn() as c:r=c.execute('SELECT started FROM runs ORDER BY id DESC LIMIT 1').fetchone()
   t=datetime.now(TZ)
   due=t.replace(hour=8,minute=0,second=0,microsecond=0)
   if t<due:due-=timedelta(days=1)
   if not r or datetime.fromisoformat(r[0])<due:collect()
  except Exception:logging.exception('Scheduled refresh failed')
  time.sleep(30)
@app.get('/')
def index():return send_from_directory(ROOT/'static','index.html')
@app.get('/api/events')
def events():
 with conn() as c:
  rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM events')]
  runs=[dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 10')]
 for r in runs:r['errors']=json.loads(r['errors'])
 today=datetime.now(TZ).date().isoformat()
 for e in rows:e['past']=bool(e['end_date'] and e['end_date']<today)
 return jsonify(events=sorted(rows,key=lambda e:e['start_date'] or '9999'),runs=runs,cities=CITIES,next_run=next_run(),refreshing=lock.locked(),server_time=now())
@app.post('/api/refresh')
def refresh():
 # Local dashboard only; reject cross-origin browser requests.
 origin=request.headers.get('Origin')
 if origin and origin!=request.host_url.rstrip('/'):return jsonify(error='不允许跨站请求'),403
 with conn() as c:r=c.execute('SELECT started FROM runs ORDER BY id DESC LIMIT 1').fetchone()
 if lock.locked():return jsonify(message='更新正在进行'),202
 if r and (datetime.now(TZ)-datetime.fromisoformat(r[0])).total_seconds()<300:return jsonify(message='刚刚检查过，请五分钟后重试'),429
 threading.Thread(target=collect,daemon=True).start()
 return jsonify(message='已开始检查公开来源'),202
init()
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--refresh',action='store_true');parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
 if args.refresh:collect()
 else:
  with conn() as c:c.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'",(now(),))
  threading.Thread(target=scheduler,daemon=True).start()
  app.run(host='127.0.0.1',port=args.port,threaded=True,use_reloader=False)

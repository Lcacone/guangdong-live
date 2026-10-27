import subprocess, sys, pathlib, urllib.request, webbrowser
ROOT=pathlib.Path(__file__).resolve().parent
URL='http://127.0.0.1:8765'
def alive():
 try:
  with urllib.request.urlopen(URL+'/api/events',timeout=2) as r:return r.status==200
 except Exception:return False
if not alive():
 log=open(ROOT/'server.log','a',encoding='utf-8')
 p=subprocess.Popen([sys.executable,str(ROOT/'app.py')],cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 (ROOT/'server.pid').write_text(str(p.pid))
if '--silent' not in sys.argv:
 import time
 for _ in range(30):
  if alive():break
  time.sleep(.3)
 webbrowser.open(URL)

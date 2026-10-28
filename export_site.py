"""Export the dashboard for GitHub Pages, including project-subpath support."""
import json, shutil
from pathlib import Path
import app

root=Path(__file__).resolve().parent
out=root/'public'
out.mkdir(exist_ok=True)
shutil.copytree(root/'static',out/'static',dirs_exist_ok=True)
with app.app.test_client() as client:
 data=client.get('/api/events').get_json()
if not data['events']:
 raise SystemExit('No verified events; refusing to publish an empty site')
(out/'events.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
html=(root/'static/index.html').read_text(encoding='utf-8')
html=html.replace('href="/"','href="./"').replace('="/static/','="./static/')
html=html.replace('本机开机且网站服务运行时自动更新；断网或来源异常会保留上次数据。收藏保存在当前浏览器。','云端每日北京时间08:00计划采集，平台可能延迟执行；来源异常时保留已核验数据。收藏保存在当前浏览器。')
(out/'index.html').write_text(html,encoding='utf-8')
js=(root/'static/app.js').read_text(encoding='utf-8')
js=js.replace("fetch('/api/events')","fetch('./events.json?t='+Date.now())")
js=js.replace("'↻ 立即检查'","'↻ 刷新数据'").replace("'● 每日监控已开启'","'● 云端每日定时更新'")
js=js.replace("$('#refresh').onclick=async()=>{", "$('#refresh').onclick=async()=>{")
start=js.index("$('#refresh').onclick=async()=>{")
js=js[:start]+"$('#refresh').onclick=async()=>{await load();toast('已读取最新发布数据；重新采集由云端定时任务执行')};load();setInterval(load,60000);\n"
js=js.replace('无法连接本地服务，请确认网站程序正在运行。已有页面数据会保留。','暂时无法读取云端数据，请稍后刷新。已有页面数据会保留。')
(out/'static/app.js').write_text(js,encoding='utf-8')
(out/'.nojekyll').touch()
print(f'Exported {len(data["events"])} events to {out}')

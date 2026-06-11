#!/usr/bin/env python3
"""
ImageGen Studio — a tiny local web UI for generating images with ComfyUI.
A text box and a button. No node graph, no cloud, no account.

Run:  python3 ui_server.py   ->  http://localhost:7866
"""
import json
import os
import random
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imagegen import api, pick_checkpoint, build_txt2img, strip_metadata, HOST, OUTDIR  # noqa: E402

PORT = int(os.environ.get("IMAGEGEN_UI_PORT", "7866"))
COMFY_WS_PORT = urllib.parse.urlsplit(HOST).port or 8188

FORMATS = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "tall": (768, 1344),
    "wide": (1344, 768),
}


def resolve_size(fmt, width, height):
    """Return (w, h). For 'custom', snap inputs to a multiple of 8 within a sane range."""
    if fmt == "custom":
        def snap(v, d):
            try:
                v = int(v)
            except (TypeError, ValueError):
                return d
            v = max(256, min(2048, v))
            return v - (v % 8)
        return snap(width, 1024), snap(height, 1024)
    return FORMATS.get(fmt, FORMATS["square"])

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>ImageGen Studio</title>
<style>
:root{--bg:#0e0f13;--panel:#16181f;--line:#262a35;--ink:#e8eaf0;--mut:#8a90a2;--accent:#00d4aa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:22px;font-weight:650;letter-spacing:-.01em;margin:0 0 4px}
.sub{color:var(--mut);font-size:14px;margin:0 0 24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px}
textarea{width:100%;min-height:88px;resize:vertical;background:#0b0c10;color:var(--ink);
 border:1px solid var(--line);border-radius:12px;padding:14px;font:inherit;outline:none}
textarea:focus{border-color:var(--accent)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}
select,button{font:inherit;border-radius:10px;border:1px solid var(--line);padding:10px 14px}
select{background:#0b0c10;color:var(--ink)}
button.go{margin-left:auto;background:var(--accent);color:#04211b;border:none;font-weight:650;
 padding:12px 22px;border-radius:12px;cursor:pointer}
button.go:disabled{opacity:.5;cursor:default}
.adv{display:inline-flex;align-items:center;gap:7px;margin-top:14px;color:var(--mut);font-size:13px;
 cursor:pointer;user-select:none;padding:8px 13px;border:1px solid var(--line);border-radius:10px;
 background:#0b0c10;transition:border-color .15s,color .15s,background .15s}
.adv:hover{border-color:var(--accent);color:var(--ink)}
.adv.open{border-color:var(--accent);color:var(--ink);background:#0d1714}
.adv .chev{transition:transform .2s}
.adv.open .chev{transform:rotate(180deg)}
.advbox{display:none;margin-top:10px;gap:14px;flex-wrap:wrap}
.advbox.open{display:flex}
.advbox label{font-size:13px;color:var(--mut);display:flex;flex-direction:column;gap:4px}
.advbox input{background:#0b0c10;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;width:120px}
.custombox{display:none;align-items:flex-end;gap:8px;flex-wrap:wrap;margin-top:12px}
.custombox.open{display:flex}
.custombox label{font-size:13px;color:var(--mut);display:flex;flex-direction:column;gap:4px}
.custombox input{background:#0b0c10;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;width:96px}
.custombox .x{color:var(--mut);padding-bottom:9px}
.custombox .hint{color:var(--mut);font-size:12px;padding-bottom:9px}
.bar{height:8px;background:#0b0c10;border:1px solid var(--line);border-radius:99px;overflow:hidden;margin:10px 0;display:none}
.bar.on{display:block}
.bar>i{display:block;height:100%;width:0;background:var(--accent);transition:width .3s ease}
#status{margin:22px 0 8px;color:var(--mut);min-height:22px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;margin-top:14px}
.gallery a{display:block;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#000}
.gallery img{width:100%;display:block}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);
 border-top-color:var(--accent);border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:8px}
@keyframes sp{to{transform:rotate(360deg)}}
</style></head><body><div class=wrap>
<h1>ImageGen Studio</h1>
<p class=sub>Describe your image, hit Create. Runs locally on your machine.</p>
<div class=card>
 <textarea id=prompt placeholder="e.g. a red fox in a snowy forest, cinematic photo, soft light"></textarea>
 <div class=row>
  <select id=format>
   <option value=square>Square &middot; 1024&times;1024</option>
   <option value=portrait>Portrait &middot; 832&times;1216</option>
   <option value=landscape>Landscape &middot; 1216&times;832</option>
   <option value=tall>Tall &middot; 768&times;1344</option>
   <option value=wide>Wide &middot; 1344&times;768</option>
   <option value=custom>Custom&hellip;</option>
  </select>
  <button class=go id=go>Create</button>
 </div>
 <div class=custombox id=custombox>
  <label>Width<input type=number id=cw value=1024 min=256 max=2048 step=8></label>
  <span class=x>&times;</span>
  <label>Height<input type=number id=ch value=1024 min=256 max=2048 step=8></label>
  <span class=hint>px &middot; snapped to multiples of 8</span>
 </div>
 <div class=adv id=advtoggle><span>&#9881;</span> Options <span class=chev>&#9662;</span></div>
 <div class=advbox id=advbox>
  <label>Quality (steps)<input type=number id=steps value=30 min=8 max=60></label>
  <label>Negative prompt (avoid)<input type=text id=neg value="" placeholder="e.g. blurry, text, extra fingers"></label>
 </div>
</div>
<div class=bar id=bar><i id=barfill></i></div>
<div id=status></div>
<div class=gallery id=gallery></div>
</div>
<script>
const $=s=>document.querySelector(s);
const go=$('#go'),status=$('#status'),gallery=$('#gallery'),bar=$('#bar'),barfill=$('#barfill');
const fmt=$('#format'),custombox=$('#custombox');
const WSPORT=%%WSPORT%%;
$('#advtoggle').onclick=function(){this.classList.toggle('open');$('#advbox').classList.toggle('open')};
fmt.onchange=()=>custombox.classList.toggle('open',fmt.value==='custom');
$('#prompt').addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')run()});
go.onclick=run;

// Unique id for this session + live connection to ComfyUI for progress
const clientId='studio-'+Math.random().toString(36).slice(2);
let ws;
function connectWs(){
 try{
  ws=new WebSocket('ws://'+location.hostname+':'+WSPORT+'/ws?clientId='+clientId);
  ws.onmessage=ev=>{
   let m; try{m=JSON.parse(ev.data)}catch(e){return}
   if(m.type==='progress'&&m.data){
    const v=m.data.value,mx=m.data.max||1,p=Math.round(v/mx*100);
    bar.classList.add('on');barfill.style.width=p+'%';
    status.innerHTML='<span class=spin></span>Rendering &mdash; step '+v+' of '+mx+' ('+p+'%)';
   }
  };
  ws.onclose=()=>setTimeout(connectWs,2000);
 }catch(e){}
}
connectWs();

async function run(){
 const prompt=$('#prompt').value.trim();
 if(!prompt){$('#prompt').focus();return}
 go.disabled=true;
 bar.classList.remove('on');barfill.style.width='0';
 status.innerHTML='<span class=spin></span>Rendering &mdash; starting';
 try{
  const r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({prompt,format:fmt.value,width:+$('#cw').value,height:+$('#ch').value,steps:+$('#steps').value,neg:$('#neg').value,clientId})});
  const d=await r.json();
  bar.classList.remove('on');
  if(!r.ok||d.error){status.textContent='Error: '+(d.error||r.status);go.disabled=false;return}
  status.textContent='Done: '+d.file.split('/').pop();
  const a=document.createElement('a');a.href=d.url+'?t='+Date.now();a.target='_blank';
  const im=document.createElement('img');im.src=a.href;a.appendChild(im);
  gallery.prepend(a);
 }catch(e){status.textContent='Error: '+e.message}
 go.disabled=false;
}
</script></body></html>"""


def generate(prompt, fmt, width, height, steps, neg, client_id):
    w, h = resolve_size(fmt, width, height)
    seed = random.randint(0, 2**32 - 1)
    neg = neg or "lowres, bad anatomy, worst quality, low quality, blurry, watermark, text"
    ckpt = pick_checkpoint()
    wf = build_txt2img(ckpt, prompt, neg, w, h, int(steps), 5.5, seed, "dpmpp_2m", "karras", 1)
    pid = api("/prompt", {"prompt": wf, "client_id": client_id or f"ui-{seed}"})["prompt_id"]
    while True:
        hist = api(f"/history/{pid}")
        if pid in hist:
            st = hist[pid].get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError("ComfyUI error while rendering")
            imgs = [im for n in hist[pid].get("outputs", {}).values() for im in n.get("images", [])]
            if imgs:
                im = imgs[0]
                q = urllib.parse.urlencode({"filename": im["filename"],
                                            "subfolder": im.get("subfolder", ""),
                                            "type": im.get("type", "output")})
                with urllib.request.urlopen(f"{HOST}/view?{q}", timeout=60) as r:
                    data, _ = strip_metadata(r.read())
                os.makedirs(OUTDIR, exist_ok=True)
                dest = os.path.join(OUTDIR, im["filename"])
                with open(dest, "wb") as f:
                    f.write(data)
                return dest
        time.sleep(1.0)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self._send(200, PAGE.replace("%%WSPORT%%", str(COMFY_WS_PORT)),
                              "text/html; charset=utf-8")
        if self.path.startswith("/img/"):
            name = os.path.basename(urllib.parse.unquote(self.path[5:].split("?")[0]))
            p = os.path.join(OUTDIR, name)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            return self._send(404, b"not found", "text/plain")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/generate":
            return self._send(404, json.dumps({"error": "unknown endpoint"}))
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad request"}))
        prompt = (req.get("prompt") or "").strip()
        if not prompt:
            return self._send(400, json.dumps({"error": "empty prompt"}))
        try:
            api("/system_stats")
        except urllib.error.URLError:
            return self._send(503, json.dumps({"error": "ComfyUI is not running. Start it and try again."}))
        try:
            dest = generate(prompt, req.get("format", "square"),
                            req.get("width"), req.get("height"),
                            req.get("steps", 30), req.get("neg", ""), req.get("clientId", ""))
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}))
        return self._send(200, json.dumps({"file": dest, "url": "/img/" + os.path.basename(dest)}))


if __name__ == "__main__":
    print(f"ImageGen Studio running:  http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

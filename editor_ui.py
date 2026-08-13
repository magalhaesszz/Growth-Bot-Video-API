def editor_html(token: str) -> str:
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Growth Bot Editor</title><style>
*{{box-sizing:border-box}} body{{margin:0;background:#07111f;color:#eef7ff;font:15px system-ui;padding:18px;display:grid;place-items:center}}
.app{{width:min(100%,460px)}} h1{{font-size:20px;margin:0 0 4px}} p{{color:#9fb3c8;margin:0 0 14px}}
.canvas{{position:relative;width:min(82vw,360px);aspect-ratio:9/16;margin:auto;background:#000 center/cover no-repeat;border:1px solid #29445f;border-radius:14px;overflow:hidden;touch-action:none;box-shadow:0 18px 60px #0008}}
.video{{position:absolute;height:auto;user-select:none;-webkit-user-drag:none;touch-action:none;cursor:grab;outline:2px solid #22d3ee;box-shadow:0 0 0 1px #000}}
.video:active{{cursor:grabbing}} .panel{{margin-top:16px;background:#101e2e;padding:15px;border-radius:14px}}
label{{display:block;margin-bottom:12px}} input{{width:100%;accent-color:#22d3ee}} .row{{display:flex;justify-content:space-between;color:#9fb3c8;font-size:13px}}
button{{width:100%;border:0;border-radius:10px;padding:13px;background:linear-gradient(90deg,#06b6d4,#22c55e);font-weight:800;color:#03131a;font-size:15px}}
#status{{min-height:22px;text-align:center;margin-top:10px;color:#7ee7a8}}
</style></head><body><main class="app"><h1>Editor livre</h1><p>Arraste o vídeo com o mouse ou dedo. Use o controle para redimensionar.</p>
<div class="canvas" id="canvas"><img class="video" id="video" draggable="false"></div>
<div class="panel"><label>Tamanho<div class="row"><span>Pequeno</span><span id="size"></span><span>Grande</span></div><input id="width" type="range" min="100" max="1080" step="10"></label>
<button id="save">Salvar posicionamento</button><div id="status"></div></div></main>
<script>
const token={token!r}, base='/api/v1/editor/'+token, canvas=document.querySelector('#canvas'), video=document.querySelector('#video'), slider=document.querySelector('#width'), size=document.querySelector('#size'), status=document.querySelector('#status');
let cfg={{}}, dragging=false, dx=0,dy=0;
async function init(){{cfg=await (await fetch(base+'/config')).json();canvas.style.backgroundImage=`url(${{base}}/background)`;video.src=base+'/frame';slider.value=cfg.video_width;draw()}}
function draw(){{const width=cfg.video_width/1080*100;video.style.width=width+'%';const maxX=canvas.clientWidth-video.offsetWidth,maxY=canvas.clientHeight-video.offsetHeight;video.style.left=(Math.max(0,maxX)*cfg.position_x)+'px';video.style.top=(Math.max(0,maxY)*cfg.position_y)+'px';size.textContent=cfg.video_width+'px'}}
slider.oninput=()=>{{cfg.video_width=+slider.value;draw()}};
video.onpointerdown=e=>{{dragging=true;video.setPointerCapture(e.pointerId);const r=video.getBoundingClientRect();dx=e.clientX-r.left;dy=e.clientY-r.top}};
video.onpointermove=e=>{{if(!dragging)return;const maxX=Math.max(0,canvas.clientWidth-video.offsetWidth),maxY=Math.max(0,canvas.clientHeight-video.offsetHeight);const x=Math.max(0,Math.min(maxX,e.clientX-canvas.getBoundingClientRect().left-dx)),y=Math.max(0,Math.min(maxY,e.clientY-canvas.getBoundingClientRect().top-dy));cfg.position_x=maxX?x/maxX:0.5;cfg.position_y=maxY?y/maxY:0.5;draw()}};
video.onpointerup=()=>dragging=false; window.onresize=draw;
document.querySelector('#save').onclick=async()=>{{status.textContent='Salvando...';const r=await fetch(base+'/config',{{method:'PUT',headers:{{'content-type':'application/json'}},body:JSON.stringify(cfg)}});status.textContent=r.ok?'Salvo! Volte ao Telegram e toque em Aplicar editor.':'Erro ao salvar.'}};
init();
</script></body></html>"""

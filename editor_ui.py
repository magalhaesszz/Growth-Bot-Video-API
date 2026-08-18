def editor_html(token: str) -> str:
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Growth Bot Editor</title><style>
*{{box-sizing:border-box}} body{{margin:0;background:#07111f;color:#eef7ff;font:15px system-ui;padding:18px;display:grid;place-items:center}}
.app{{width:min(100%,460px)}} h1{{font-size:20px;margin:0 0 4px}} p{{color:#9fb3c8;margin:0 0 14px;font-size:13px}}
.tabs{{display:flex;gap:8px;margin-bottom:14px;background:#101e2e;padding:5px;border-radius:12px}}
.tab{{flex:1;text-align:center;padding:10px;border-radius:9px;cursor:pointer;font-weight:700;color:#9fb3c8;transition:.15s}}
.tab.active{{background:linear-gradient(90deg,#06b6d4,#22c55e);color:#03131a}}
.view{{display:none}} .view.active{{display:block}}
.canvas{{position:relative;width:min(82vw,360px);aspect-ratio:9/16;margin:auto;background:#000 center/cover no-repeat;border:1px solid #29445f;border-radius:14px;overflow:hidden;touch-action:none;box-shadow:0 18px 60px #0008}}
.video{{position:absolute;height:auto;user-select:none;-webkit-user-drag:none;touch-action:none;cursor:grab;outline:2px solid #22d3ee;box-shadow:0 0 0 1px #000}}
.video:active{{cursor:grabbing}} .panel{{margin-top:16px;background:#101e2e;padding:15px;border-radius:14px}}
label{{display:block;margin-bottom:12px}} input[type=range]{{width:100%;accent-color:#22d3ee}}
.row{{display:flex;justify-content:space-between;color:#9fb3c8;font-size:13px}}
button{{width:100%;border:0;border-radius:10px;padding:13px;background:linear-gradient(90deg,#06b6d4,#22c55e);font-weight:800;color:#03131a;font-size:15px;margin-top:8px}}
button.secondary{{background:#1c2f43;color:#9fb3c8}}
#status,#cropStatus{{min-height:22px;text-align:center;margin-top:10px;color:#7ee7a8;font-size:13px}}
.cropCanvas{{position:relative;width:min(82vw,360px);aspect-ratio:9/16;margin:auto;background:#000 center/contain no-repeat;border:1px solid #29445f;border-radius:14px;overflow:hidden;touch-action:none;box-shadow:0 18px 60px #0008}}
.cropBox{{position:absolute;border:2px dashed #f43f5e;background:#f43f5e22;touch-action:none}}
.cropBox .handle{{position:absolute;width:18px;height:18px;background:#f43f5e;border-radius:4px;right:-9px;bottom:-9px;cursor:nwse-resize}}
.hint{{color:#9fb3c8;font-size:12px;text-align:center;margin-top:8px}}
</style></head><body><main class="app">
<h1>Editor livre</h1>
<div class="tabs">
  <div class="tab active" id="tabPos">Posicionar</div>
  <div class="tab" id="tabCrop">Recortar</div>
</div>

<div class="view active" id="viewPos">
  <p>Arraste o vídeo com o mouse ou dedo. Use o controle para redimensionar.</p>
  <div class="canvas" id="canvas"><img class="video" id="video" draggable="false"></div>
  <div class="panel"><label>Tamanho<div class="row"><span>Pequeno</span><span id="size"></span><span>Grande</span></div>
  <input id="width" type="range" min="100" max="1080" step="10"></label>
  <button id="save">Salvar posicionamento</button><div id="status"></div></div>
</div>

<div class="view" id="viewCrop">
  <p>Desenhe um retângulo sobre a parte que deseja <b>manter</b>. Use para remover faixas brancas, títulos ou marcas d'água nas bordas.</p>
  <div class="cropCanvas" id="cropCanvas">
    <img id="cropFrame" style="width:100%;height:100%;object-fit:contain;user-select:none;-webkit-user-drag:none;">
    <div class="cropBox" id="cropBox" style="display:none"><div class="handle" id="cropHandle"></div></div>
  </div>
  <div class="hint">Toque e arraste para criar a área. Arraste o canto para redimensionar.</div>
  <div class="panel">
    <button id="saveCrop">Salvar recorte</button>
    <button class="secondary" id="clearCrop">Remover recorte (usar vídeo inteiro)</button>
    <div id="cropStatus"></div>
  </div>
</div>

</main>
<script>
const token={token!r}, base='/api/v1/editor/'+token;
const canvas=document.querySelector('#canvas'), video=document.querySelector('#video');
const slider=document.querySelector('#width'), size=document.querySelector('#size'), status=document.querySelector('#status');
const tabPos=document.querySelector('#tabPos'), tabCrop=document.querySelector('#tabCrop');
const viewPos=document.querySelector('#viewPos'), viewCrop=document.querySelector('#viewCrop');
const cropCanvas=document.querySelector('#cropCanvas'), cropFrame=document.querySelector('#cropFrame');
const cropBox=document.querySelector('#cropBox'), cropHandle=document.querySelector('#cropHandle');
const cropStatus=document.querySelector('#cropStatus');

let cfg={{}}, dragging=false, dx=0,dy=0;
let frameNatW=0, frameNatH=0;
let crop=null, cropDragging=false, cropResizing=false, cropStartX=0, cropStartY=0;

tabPos.onclick=()=>{{tabPos.classList.add('active');tabCrop.classList.remove('active');viewPos.classList.add('active');viewCrop.classList.remove('active')}};
tabCrop.onclick=()=>{{tabCrop.classList.add('active');tabPos.classList.remove('active');viewCrop.classList.add('active');viewPos.classList.remove('active');drawCrop()}};

async function init(){{
  cfg=await (await fetch(base+'/config')).json();
  canvas.style.backgroundImage=`url(${{base}}/background)`;
  video.src=base+'/frame';
  cropFrame.src=base+'/frame';
  cropFrame.onload=()=>{{frameNatW=cropFrame.naturalWidth;frameNatH=cropFrame.naturalHeight;
    if(cfg.manual_crop){{
      crop={{...cfg.manual_crop}};
      drawCrop();
    }}
  }};
  slider.value=cfg.video_width;
  draw();
}}

function draw(){{
  const width=cfg.video_width/1080*100;
  video.style.width=width+'%';
  const maxX=canvas.clientWidth-video.offsetWidth, maxY=canvas.clientHeight-video.offsetHeight;
  video.style.left=(Math.max(0,maxX)*cfg.position_x)+'px';
  video.style.top=(Math.max(0,maxY)*cfg.position_y)+'px';
  size.textContent=cfg.video_width+'px';
}}

slider.oninput=()=>{{cfg.video_width=+slider.value;draw()}};
video.onpointerdown=e=>{{dragging=true;video.setPointerCapture(e.pointerId);const r=video.getBoundingClientRect();dx=e.clientX-r.left;dy=e.clientY-r.top}};
video.onpointermove=e=>{{if(!dragging)return;const maxX=Math.max(0,canvas.clientWidth-video.offsetWidth),maxY=Math.max(0,canvas.clientHeight-video.offsetHeight);const x=Math.max(0,Math.min(maxX,e.clientX-canvas.getBoundingClientRect().left-dx)),y=Math.max(0,Math.min(maxY,e.clientY-canvas.getBoundingClientRect().top-dy));cfg.position_x=maxX?x/maxX:0.5;cfg.position_y=maxY?y/maxY:0.5;draw()}};
video.onpointerup=()=>dragging=false;
window.onresize=()=>{{draw();drawCrop()}};

document.querySelector('#save').onclick=async()=>{{
  status.textContent='Salvando...';
  const r=await fetch(base+'/config',{{method:'PUT',headers:{{'content-type':'application/json'}},body:JSON.stringify(cfg)}});
  status.textContent=r.ok?'Salvo! Volte ao Telegram e toque em Aplicar.':'Erro ao salvar.';
}};

// ─── Recorte (crop) ──────────────────────────────────────────

function frameRect(){{
  // Retangulo real da imagem dentro do container (object-fit: contain)
  const cw=cropCanvas.clientWidth, ch=cropCanvas.clientHeight;
  const scale=Math.min(cw/frameNatW, ch/frameNatH);
  const w=frameNatW*scale, h=frameNatH*scale;
  const x=(cw-w)/2, y=(ch-h)/2;
  return {{x,y,w,h,scale}};
}}

function drawCrop(){{
  if(!crop||!frameNatW) {{ cropBox.style.display='none'; return; }}
  const fr=frameRect();
  cropBox.style.display='block';
  cropBox.style.left=(fr.x+crop.x*fr.scale)+'px';
  cropBox.style.top=(fr.y+crop.y*fr.scale)+'px';
  cropBox.style.width=(crop.w*fr.scale)+'px';
  cropBox.style.height=(crop.h*fr.scale)+'px';
}}

cropCanvas.onpointerdown=e=>{{
  if(e.target===cropHandle) return;
  const fr=frameRect();
  const r=cropCanvas.getBoundingClientRect();
  const px=e.clientX-r.left, py=e.clientY-r.top;
  if(px<fr.x||px>fr.x+fr.w||py<fr.y||py>fr.y+fr.h) return;
  cropStartX=(px-fr.x)/fr.scale; cropStartY=(py-fr.y)/fr.scale;
  crop={{x:cropStartX,y:cropStartY,w:1,h:1}};
  cropDragging=true;
  cropCanvas.setPointerCapture(e.pointerId);
}};
cropCanvas.onpointermove=e=>{{
  if(!cropDragging) return;
  const fr=frameRect();
  const r=cropCanvas.getBoundingClientRect();
  const px=e.clientX-r.left, py=e.clientY-r.top;
  const cx=Math.max(0,Math.min(frameNatW,(px-fr.x)/fr.scale));
  const cy=Math.max(0,Math.min(frameNatH,(py-fr.y)/fr.scale));
  crop.x=Math.min(cropStartX,cx); crop.y=Math.min(cropStartY,cy);
  crop.w=Math.max(10,Math.abs(cx-cropStartX)); crop.h=Math.max(10,Math.abs(cy-cropStartY));
  drawCrop();
}};
cropCanvas.onpointerup=()=>{{cropDragging=false}};

cropHandle.onpointerdown=e=>{{
  e.stopPropagation(); cropResizing=true; cropHandle.setPointerCapture(e.pointerId);
}};
cropHandle.onpointermove=e=>{{
  if(!cropResizing||!crop) return;
  const fr=frameRect();
  const r=cropCanvas.getBoundingClientRect();
  const px=e.clientX-r.left, py=e.clientY-r.top;
  const cx=Math.max(crop.x+10,Math.min(frameNatW,(px-fr.x)/fr.scale));
  const cy=Math.max(crop.y+10,Math.min(frameNatH,(py-fr.y)/fr.scale));
  crop.w=cx-crop.x; crop.h=cy-crop.y;
  drawCrop();
}};
cropHandle.onpointerup=()=>{{cropResizing=false}};

document.querySelector('#saveCrop').onclick=async()=>{{
  if(!crop){{cropStatus.textContent='Desenhe uma área primeiro.';return}}
  cropStatus.textContent='Salvando...';
  const r=await fetch(base+'/config',{{method:'PUT',headers:{{'content-type':'application/json'}},
    body:JSON.stringify({{...cfg, manual_crop:{{w:Math.round(crop.w),h:Math.round(crop.h),x:Math.round(crop.x),y:Math.round(crop.y)}}}})}});
  cropStatus.textContent=r.ok?'Recorte salvo! Volte ao Telegram e toque em Aplicar.':'Erro ao salvar.';
}};
document.querySelector('#clearCrop').onclick=async()=>{{
  crop=null; drawCrop();
  cropStatus.textContent='Salvando...';
  const r=await fetch(base+'/config',{{method:'PUT',headers:{{'content-type':'application/json'}},
    body:JSON.stringify({{...cfg, manual_crop:null}})}});
  cropStatus.textContent=r.ok?'Recorte removido — vídeo inteiro sera usado.':'Erro ao salvar.';
}};

init();
</script></body></html>"""

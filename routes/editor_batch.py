import json
import os
import subprocess
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from core.config import DEFAULT_CONFIG, INPUT_DIR, MAX_VIDEO_MB, OUTPUT_DIR
from routes.video import _auth, _find_fundo, _safe_account_id, _save_upload
from video.processor import detect_auto_crop, probe_video
from video.validator import validate_video

router = APIRouter()
_sessions: dict[str, dict] = {}


def _remove(path: str | None):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup(max_age: int = 3600):
    now = time.time()
    for token in list(_sessions):
        session = _sessions[token]
        if now - session["created_at"] <= max_age:
            continue
        _sessions.pop(token, None)
        for item in session.get("items", []):
            _remove(item.get("frame_path"))


def _session(token: str) -> dict:
    _cleanup()
    session = _sessions.get(token)
    if not session:
        raise HTTPException(404, "Sessão do editor expirada.")
    return session


def _item(session: dict, index: int) -> dict:
    if index < 0 or index >= len(session["items"]):
        raise HTTPException(404, "Vídeo não encontrado na sessão.")
    return session["items"][index]


def _crop(value, item: dict):
    if value is None:
        return None
    try:
        x, y = max(0, int(value["x"])), max(0, int(value["y"]))
        w, h = max(1, int(value["w"])), max(1, int(value["h"]))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Recorte inválido.")
    fw, fh = item["frame_width"], item["frame_height"]
    x, y = min(x, fw - 1), min(y, fh - 1)
    w, h = min(w, fw - x), min(h, fh - y)
    if w < 10 or h < 10:
        raise HTTPException(400, "Recorte muito pequeno.")
    return {"x": x, "y": y, "w": w, "h": h}


def _public(session: dict) -> dict:
    return {
        "config": session["config"],
        "items": [{
            "index": x["index"], "filename": x["filename"],
            "frame_width": x["frame_width"], "frame_height": x["frame_height"],
            "video_width": x.get("video_width", session["config"]["video_width"]),
            "position_x": x.get("position_x", session["config"]["position_x"]),
            "position_y": x.get("position_y", session["config"]["position_y"]),
            "manual_crop": x.get("manual_crop"),
        } for x in session["items"]],
    }


HTML = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><title>Growth Bot — Editor em massa</title><style>
*{box-sizing:border-box}body{margin:0;background:#07111f;color:#eef7ff;font:14px system-ui;padding:14px}.app{width:min(100%,470px);margin:auto}h1{font-size:20px;margin:0 0 4px}.muted{color:#9fb3c8;margin:0 0 12px}.clips{display:flex;gap:7px;overflow:auto;padding:4px 0 12px}.clip{white-space:nowrap;border:1px solid #29445f;background:#101e2e;color:#9fb3c8;border-radius:999px;padding:8px 11px;cursor:pointer}.clip.active{border-color:#22d3ee;color:#fff}.tabs{display:flex;gap:6px;background:#101e2e;padding:5px;border-radius:12px;margin-bottom:12px}.tab{flex:1;padding:9px;text-align:center;border-radius:9px;font-weight:800;color:#9fb3c8}.tab.active{background:#22d3ee;color:#03131a}.view{display:none}.view.active{display:block}.stage{position:relative;width:min(82vw,360px);aspect-ratio:9/16;margin:auto;background:#000 center/cover no-repeat;border:1px solid #29445f;border-radius:14px;overflow:hidden;touch-action:none}.videoBox{position:absolute;overflow:hidden;outline:2px solid #22d3ee;touch-action:none;cursor:grab}.videoBox img{position:absolute;max-width:none;user-select:none;-webkit-user-drag:none}.panel{margin-top:13px;background:#101e2e;border-radius:14px;padding:13px}input[type=range]{width:100%}.row{display:flex;justify-content:space-between;color:#9fb3c8;font-size:12px}.cropStage{position:relative;width:min(82vw,360px);aspect-ratio:9/16;margin:auto;background:#000;border:1px solid #29445f;border-radius:14px;overflow:hidden;touch-action:none}.cropFrame{width:100%;height:100%;object-fit:contain;user-select:none;-webkit-user-drag:none}.cropBox{position:absolute;border:2px dashed #fb7185;background:#fb718522;display:none}.handle{position:absolute;right:-9px;bottom:-9px;width:18px;height:18px;border-radius:4px;background:#fb7185}.status{text-align:center;color:#7ee7a8;min-height:20px;margin-top:8px}.buttons{display:grid;gap:7px}button{border:0;border-radius:10px;padding:11px;font-weight:800;background:#22d3ee;color:#03131a}button.secondary{background:#1c2f43;color:#c7d5e4}.note{font-size:12px;color:#9fb3c8;margin:8px 0 0;text-align:center}
</style></head><body><main class="app"><h1>Editor em massa</h1><p class="muted">Tamanho, posição e recorte são independentes por vídeo.</p><div class="clips" id="clips"></div><div class="tabs"><div class="tab active" id="tabPos">Posicionar</div><div class="tab" id="tabCrop">Recortar</div></div>
<div class="view active" id="pos"><div class="stage" id="stage"><div class="videoBox" id="box"><img id="posFrame"></div></div><div class="panel"><div class="row"><span>Tamanho</span><span id="size"></span></div><input id="width" type="range" min="100" max="1080" step="10"><div class="status" id="posStatus"></div></div></div>
<div class="view" id="crop"><div class="cropStage" id="cropStage"><img class="cropFrame" id="cropFrame"><div class="cropBox" id="cropBox"><div class="handle" id="handle"></div></div></div><p class="note">Arraste para marcar a área que deve permanecer. Ao mudar o recorte, volte em Posicionar: a visualização já estará recortada.</p><div class="panel buttons"><button id="copy">Aplicar este recorte a todos</button><button class="secondary" id="clear">Remover recorte deste vídeo</button><div class="status" id="cropStatus"></div></div></div></main>
<script>
const token='__TOKEN__',base='/api/v1/editor/batch/'+token;let state,active=0,drag=false,dx=0,dy=0,cropDrag=false,resize=false,sx=0,sy=0,timer;
const $=s=>document.querySelector(s),stage=$('#stage'),box=$('#box'),posFrame=$('#posFrame'),width=$('#width'),size=$('#size'),cropStage=$('#cropStage'),cropFrame=$('#cropFrame'),cropBox=$('#cropBox');
function item(){return state.items[active]}function layout(){return item()}function crop(){return item().manual_crop}function imageRect(){const i=item(),cw=cropStage.clientWidth,ch=cropStage.clientHeight,s=Math.min(cw/i.frame_width,ch/i.frame_height),w=i.frame_width*s,h=i.frame_height*s;return{x:(cw-w)/2,y:(ch-h)/2,w,h,s}}
function chips(){const root=$('#clips');root.innerHTML='';state.items.forEach((it,i)=>{let b=document.createElement('button');b.className='clip'+(i===active?' active':'');b.textContent=(i+1)+' · '+it.filename.slice(0,24);b.onclick=()=>{active=i;chips();loadFrame()};root.appendChild(b)})}
function loadFrame(){const u=base+'/frame/'+active+'?t='+Date.now();posFrame.src=u;cropFrame.src=u;cropFrame.onload=()=>{drawCrop()};drawPos()}
function drawPos(){if(!state)return;const it=item(),l=layout(),c=crop()||{x:0,y:0,w:it.frame_width,h:it.frame_height},bw=stage.clientWidth*l.video_width/1080,bh=bw*c.h/c.w;box.style.width=bw+'px';box.style.height=bh+'px';const scale=bw/c.w;posFrame.style.width=(it.frame_width*scale)+'px';posFrame.style.height=(it.frame_height*scale)+'px';posFrame.style.left=(-c.x*scale)+'px';posFrame.style.top=(-c.y*scale)+'px';const mx=Math.max(0,stage.clientWidth-bw),my=Math.max(0,stage.clientHeight-bh);box.style.left=(mx*l.position_x)+'px';box.style.top=(my*l.position_y)+'px';size.textContent=l.video_width+'px';width.value=l.video_width}
function drawCrop(){const c=crop();if(!c){cropBox.style.display='none';return}const r=imageRect();cropBox.style.display='block';cropBox.style.left=(r.x+c.x*r.s)+'px';cropBox.style.top=(r.y+c.y*r.s)+'px';cropBox.style.width=(c.w*r.s)+'px';cropBox.style.height=(c.h*r.s)+'px'}
function save(msg){clearTimeout(timer);timer=setTimeout(async()=>{let r=await fetch(base+'/config',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(state)});$('#posStatus').textContent=$('#cropStatus').textContent=r.ok?(msg||'Salvo automaticamente.'):'Erro ao salvar.'},180)}
width.oninput=()=>{layout().video_width=+width.value;drawPos();save()};box.onpointerdown=e=>{drag=true;box.setPointerCapture(e.pointerId);let r=box.getBoundingClientRect();dx=e.clientX-r.left;dy=e.clientY-r.top};box.onpointermove=e=>{if(!drag)return;let r=stage.getBoundingClientRect(),mx=Math.max(0,stage.clientWidth-box.clientWidth),my=Math.max(0,stage.clientHeight-box.clientHeight),x=Math.max(0,Math.min(mx,e.clientX-r.left-dx)),y=Math.max(0,Math.min(my,e.clientY-r.top-dy)),l=layout();l.position_x=mx?x/mx:.5;l.position_y=my?y/my:.5;drawPos()};box.onpointerup=()=>{drag=false;save()};
cropStage.onpointerdown=e=>{if(e.target.id==='handle')return;let r=imageRect(),b=cropStage.getBoundingClientRect(),px=e.clientX-b.left,py=e.clientY-b.top;if(px<r.x||px>r.x+r.w||py<r.y||py>r.y+r.h)return;sx=(px-r.x)/r.s;sy=(py-r.y)/r.s;item().manual_crop={x:sx,y:sy,w:10,h:10};cropDrag=true;cropStage.setPointerCapture(e.pointerId)};cropStage.onpointermove=e=>{if(!cropDrag)return;let r=imageRect(),b=cropStage.getBoundingClientRect(),it=item(),cx=Math.max(0,Math.min(it.frame_width,(e.clientX-b.left-r.x)/r.s)),cy=Math.max(0,Math.min(it.frame_height,(e.clientY-b.top-r.y)/r.s)),c=item().manual_crop;c.x=Math.min(sx,cx);c.y=Math.min(sy,cy);c.w=Math.max(10,Math.abs(cx-sx));c.h=Math.max(10,Math.abs(cy-sy));drawCrop();drawPos();save('Recorte atualizado em tempo real.')};cropStage.onpointerup=()=>cropDrag=false;
$('#handle').onpointerdown=e=>{e.stopPropagation();resize=true;$('#handle').setPointerCapture(e.pointerId)};$('#handle').onpointermove=e=>{if(!resize||!crop())return;let r=imageRect(),b=cropStage.getBoundingClientRect(),it=item(),c=crop(),cx=Math.max(c.x+10,Math.min(it.frame_width,(e.clientX-b.left-r.x)/r.s)),cy=Math.max(c.y+10,Math.min(it.frame_height,(e.clientY-b.top-r.y)/r.s));c.w=cx-c.x;c.h=cy-c.y;drawCrop();drawPos();save('Recorte atualizado em tempo real.')};$('#handle').onpointerup=()=>resize=false;
$('#clear').onclick=()=>{item().manual_crop=null;drawCrop();drawPos();save('Recorte removido.')};$('#copy').onclick=()=>{let src=item(),c=src.manual_crop;if(!c){$('#cropStatus').textContent='Crie um recorte primeiro.';return}state.items.forEach(it=>{it.manual_crop={x:Math.round(c.x/src.frame_width*it.frame_width),y:Math.round(c.y/src.frame_height*it.frame_height),w:Math.round(c.w/src.frame_width*it.frame_width),h:Math.round(c.h/src.frame_height*it.frame_height)}});drawCrop();drawPos();save('Recorte proporcional aplicado a todos.')};
function tab(which){$('#tabPos').classList.toggle('active',which==='pos');$('#tabCrop').classList.toggle('active',which==='crop');$('#pos').classList.toggle('active',which==='pos');$('#crop').classList.toggle('active',which==='crop');if(which==='pos')drawPos();else drawCrop()}$('#tabPos').onclick=()=>tab('pos');$('#tabCrop').onclick=()=>tab('crop');window.onresize=()=>{drawPos();drawCrop()};
(async()=>{state=await(await fetch(base+'/config')).json();stage.style.backgroundImage=`url(${base}/background)`;chips();loadFrame()})()
</script></body></html>'''


def editor_html(token: str) -> str:
    return HTML.replace("__TOKEN__", token)


@router.post("/editor/batch/session")
async def create_session(
    request: Request,
    videos: list[UploadFile] = File(...),
    account_id: str = Form("default"),
    config_json: str = Form("{}"),
    x_api_secret: Optional[str] = Header(None),
):
    _auth(x_api_secret)
    _cleanup()
    account_id = _safe_account_id(account_id)
    if not 1 <= len(videos) <= 10:
        raise HTTPException(400, "Envie de 1 a 10 vídeos.")
    fundo = _find_fundo(account_id)
    if not fundo or not os.path.exists(fundo):
        raise HTTPException(400, "Envie o fundo antes de abrir o editor.")
    try:
        incoming = json.loads(config_json)
        if not isinstance(incoming, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "config_json inválido.")

    cfg = {**DEFAULT_CONFIG, **incoming}
    cfg.pop("manual_crop", None)
    token = uuid.uuid4().hex
    items = []
    try:
        for index, upload in enumerate(videos):
            filename = upload.filename or f"video_{index + 1}.mp4"
            if not filename.lower().endswith(".mp4"):
                raise HTTPException(400, f"{filename}: apenas .mp4.")
            input_path = os.path.join(INPUT_DIR, f"{token}_{index}_batch.mp4")
            frame_path = os.path.join(OUTPUT_DIR, f"{token}_{index}_batch.jpg")
            await run_in_threadpool(_save_upload, upload, input_path)
            try:
                valid, msg = await run_in_threadpool(validate_video, input_path, MAX_VIDEO_MB)
                if not valid:
                    raise HTTPException(400, f"{filename}: {msg}")
                info = await run_in_threadpool(probe_video, input_path)
                auto = await run_in_threadpool(detect_auto_crop, input_path, info, cfg)
                if auto:
                    fw, fh, bx, by = [int(x) for x in auto]
                else:
                    fw, fh, bx, by = int(info["width"]), int(info["height"]), 0, 0
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-threads", "1", "-ss", "0.1", "-i", input_path]
                if auto:
                    cmd += ["-vf", f"crop={fw}:{fh}:{bx}:{by}"]
                cmd += ["-frames:v", "1", "-q:v", "2", frame_path]
                proc = await run_in_threadpool(subprocess.run, cmd, capture_output=True, text=True, timeout=60)
                if proc.returncode != 0 or not os.path.exists(frame_path):
                    raise HTTPException(500, f"{filename}: falha ao extrair frame.")
                items.append({
                    "index": index, "filename": filename, "frame_path": frame_path,
                    "frame_width": fw, "frame_height": fh,
                    "source_width": int(info["width"]), "source_height": int(info["height"]),
                    "base_x": bx, "base_y": by,
                    "video_width": max(100, min(1080, int(cfg["video_width"]))),
                    "position_x": max(0.0, min(1.0, float(cfg["position_x"]))),
                    "position_y": max(0.0, min(1.0, float(cfg["position_y"]))),
                    "manual_crop": None,
                })
            finally:
                _remove(input_path)
    except Exception:
        for item in items:
            _remove(item.get("frame_path"))
        raise

    _sessions[token] = {"fundo": fundo, "config": cfg, "items": items, "created_at": time.time()}
    return {
        "ok": True, "token": token, "total": len(items),
        "editor_url": str(request.base_url).rstrip("/") + f"/api/v1/editor/batch/{token}",
    }


@router.get("/editor/batch/{token}", response_class=HTMLResponse)
async def open_editor(token: str):
    _session(token)
    return editor_html(token)


@router.get("/editor/batch/{token}/background")
async def background(token: str):
    return FileResponse(_session(token)["fundo"])


@router.get("/editor/batch/{token}/frame/{index}")
async def frame(token: str, index: int):
    return FileResponse(_item(_session(token), index)["frame_path"], media_type="image/jpeg")


@router.get("/editor/batch/{token}/config")
async def get_config(token: str):
    return _public(_session(token))


@router.put("/editor/batch/{token}/config")
async def put_config(token: str, payload: dict = Body(...)):
    session = _session(token)
    cfg = payload.get("config") or {}
    try:
        session["config"].update({
            "video_width": max(100, min(1080, int(cfg.get("video_width", session["config"]["video_width"])))),
            "position_x": max(0.0, min(1.0, float(cfg.get("position_x", session["config"]["position_x"])))),
            "position_y": max(0.0, min(1.0, float(cfg.get("position_y", session["config"]["position_y"])))),
        })
    except (TypeError, ValueError):
        raise HTTPException(400, "Posicionamento inválido.")
    for raw in payload.get("items") or []:
        try:
            index = int(raw["index"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Índice inválido.")
        item = _item(session, index)
        try:
            item["video_width"] = max(
                100,
                min(1080, int(raw.get("video_width", item.get("video_width", session["config"]["video_width"])))),
            )
            item["position_x"] = max(
                0.0,
                min(1.0, float(raw.get("position_x", item.get("position_x", session["config"]["position_x"])))),
            )
            item["position_y"] = max(
                0.0,
                min(1.0, float(raw.get("position_y", item.get("position_y", session["config"]["position_y"])))),
            )
        except (TypeError, ValueError):
            raise HTTPException(400, "Posicionamento individual inválido.")
        if "manual_crop" in raw:
            item["manual_crop"] = _crop(raw.get("manual_crop"), item)
    return {"ok": True}


@router.get("/editor/batch/{token}/result")
async def result(token: str, x_api_secret: Optional[str] = Header(None)):
    _auth(x_api_secret)
    session = _session(token)
    items = []
    for item in session["items"]:
        c = item.get("manual_crop")
        translated = None if not c else {
            "x": item["base_x"] + c["x"], "y": item["base_y"] + c["y"],
            "w": c["w"], "h": c["h"],
        }
        items.append({
            "index": item["index"], "filename": item["filename"],
            "video_width": item.get("video_width", session["config"]["video_width"]),
            "position_x": item.get("position_x", session["config"]["position_x"]),
            "position_y": item.get("position_y", session["config"]["position_y"]),
            "manual_crop": translated,
            "source_width": item["source_width"], "source_height": item["source_height"],
        })
    return {"ok": True, "config": session["config"], "items": items}
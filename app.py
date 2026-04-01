import os, uuid, time, threading, re, subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

UPLOAD_DIR = Path('uploads')
OUTPUT_DIR = Path('output')
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

FONT_BOLD = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
             '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf']
FONT_REG  = ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
             '/usr/share/fonts/dejavu/DejaVuSans.ttf']

def find_font(c):
    for p in c:
        if os.path.exists(p): return p
    return None

def esc(t):
    return str(t).strip().replace('\\','\\\\').replace("'","\\'").replace(':','\\:').replace('%','\\%')

def fade_expr(t0, dur=0.45):
    return f"if(lt(t\\,{t0:.2f})\\,0\\,min(1\\,(t-{t0:.2f})/{dur:.2f}))"

def generate_video(photo_path, data, out_path):
    W,H,FPS,DUR = 540,960,24,10
    T0 = DUR*0.58
    FRAMES = FPS*DUR
    fb = f"fontfile='{find_font(FONT_BOLD)}':" if find_font(FONT_BOLD) else ""
    fr = f"fontfile='{find_font(FONT_REG)}':"  if find_font(FONT_REG)  else ""
    logo        = data.get('logo','').strip()
    size_text   = data.get('size','').strip()
    brand       = data.get('brand','').strip()
    description = data.get('description','').strip()
    banner_hex  = data.get('banner_color','#bb1111').lstrip('#')
    logo_pos    = data.get('logo_pos','left')
    vf = []
    vf.append(f"scale={W}:{H}:force_original_aspect_ratio=increase")
    vf.append(f"crop={W}:{H}")
    vf.append(f"zoompan=z='1.0+0.22*on/{FRAMES}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={FRAMES}:s={W}x{H}:fps={FPS}")
    vf.append("vignette=PI/4:mode=backward")
    if logo:
        lx = '18' if logo_pos=='left' else 'w-tw-18'
        vf.append(f"drawtext=text='{esc(logo)}':{fb}fontcolor=white:fontsize=28:x={lx}:y=18:shadowcolor=black@0.7:shadowx=1:shadowy=1:alpha='min(1\\,t/0.8)'")
    if description:
        vf.append(f"drawbox=x=0:y=ih*0.705:w=iw:h=ih*0.155:color=0x{banner_hex}@0.90:t=fill:enable='gte(t\\,{T0:.2f})'")
        for i,line in enumerate(description.split('\n')[:2]):
            yp=0.745+i*0.052
            vf.append(f"drawtext=text='{esc(line)}':{fb}fontcolor=white:fontsize=27:x=(w-tw)/2:y=ih*{yp:.3f}:shadowcolor=black@0.5:shadowx=1:shadowy=1:alpha='{fade_expr(T0)}':enable='gte(t\\,{T0:.2f})'")
    if size_text:
        t1=T0+0.2; sy=0.875 if description else 0.845
        vf.append(f"drawtext=text='{esc(size_text)}':{fb}fontcolor=white:fontsize=54:x=(w-tw)/2:y=ih*{sy:.3f}:shadowcolor=black@0.65:shadowx=2:shadowy=2:alpha='{fade_expr(t1)}':enable='gte(t\\,{t1:.2f})'")
    if brand:
        t1=T0+0.25
        vf.append(f"drawtext=text='{esc(brand)}':{fr}fontcolor=white:fontsize=32:x=(w-tw)/2:y=ih*0.932:shadowcolor=black@0.55:shadowx=1:shadowy=1:alpha='{fade_expr(t1)}':enable='gte(t\\,{t1:.2f})'")
    cmd = ['ffmpeg','-loop','1','-framerate',str(FPS),'-t',str(DUR+0.5),'-i',photo_path,
           '-vf',','.join(vf),'-t',str(DUR),'-c:v','libx264','-crf','22','-preset','fast',
           '-pix_fmt','yuv420p','-movflags','+faststart','-y',out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr[-600:]}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    import base64 as b64mod
    from PIL import Image
    import io
    data = request.get_json(force=True, silent=True) or {}
    photos_b64 = data.get('photos', [])
    if not photos_b64:
        return jsonify({'error': 'Загрузите хотя бы одно фото шины'}), 400
    session_id  = uuid.uuid4().hex[:10]
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir()
    photo_path = None
    for b64str in photos_b64[:1]:
        try:
            img = Image.open(io.BytesIO(b64mod.b64decode(b64str))).convert('RGB')
            img.thumbnail((1200,1200), Image.LANCZOS)
            dest = session_dir / 'photo.jpg'
            img.save(str(dest), 'JPEG', quality=92)
            photo_path = str(dest)
            break
        except: continue
    if not photo_path:
        return jsonify({'error': 'Не удалось обработать фото'}), 400
    form = {k: str(data.get(k,'')).strip() for k in ('size','brand','description','logo','logo_pos','banner_color')}
    if not re.match(r'^#[0-9a-fA-F]{6}$', form.get('banner_color','')): form['banner_color']='#bb1111'
    if not form.get('size'): return jsonify({'error': 'Введите размер шины'}), 400
    safe  = form['size'].replace('/','- ').replace(' ','_')
    name  = f"{safe}_{session_id[:6]}.mp4"
    opath = str(OUTPUT_DIR / name)
    try:
        generate_video(photo_path, form, opath)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e)[:400], 'trace': traceback.format_exc()[-600:]}), 500
    finally:
        import shutil; shutil.rmtree(str(session_dir), ignore_errors=True)
    threading.Thread(target=lambda: (time.sleep(7200), Path(opath).unlink(missing_ok=True)), daemon=True).start()
    return jsonify({'url': f'/download/{name}', 'filename': name})

@app.route('/download/<filename>')
def download(filename):
    path = OUTPUT_DIR / filename
    if not path.exists(): return 'Не найден', 404
    return send_file(str(path), as_attachment=True, download_name=filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)

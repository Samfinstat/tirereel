import os, uuid, time, threading, re, subprocess, io
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
        if os.path.exists(p):
            return p
    return None

def esc(t):
    return (str(t).strip()
            .replace('\\', '\\\\')
            .replace("'",  "\\'")
            .replace(':',  '\\:')
            .replace('%',  '\\%'))

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return r

def generate_video(photo_path, data, out_path):
    W, H = 540, 960
    FPS  = 24
    DUR  = 10
    T0   = 5.5

    fb = f"fontfile='{find_font(FONT_BOLD)}':" if find_font(FONT_BOLD) else ""
    fr = f"fontfile='{find_font(FONT_REG)}':"  if find_font(FONT_REG)  else ""

    logo        = data.get('logo',        '').strip()
    size_text   = data.get('size',        '').strip()
    brand       = data.get('brand',       '').strip()
    description = data.get('description', '').strip()
    banner_hex  = data.get('banner_color', '#bb1111').lstrip('#')
    logo_pos    = data.get('logo_pos',    'left')

    tmp_dir  = Path(out_path).parent / (Path(out_path).stem + '_tmp')
    tmp_dir.mkdir(exist_ok=True)

    try:
        # ── Step 1: prepare base frame (scaled + darkened) ───────────────────
        base_jpg = str(tmp_dir / 'base.jpg')
        run(['ffmpeg', '-i', photo_path,
             '-vf', f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
             '-frames:v', '1', '-y', base_jpg])

        # ── Step 2: build drawtext filter ────────────────────────────────────
        dt = []
        if logo:
            lx = '18' if logo_pos == 'left' else f'{W}-tw-18'
            dt.append(f"drawtext=text='{esc(logo)}':{fb}fontcolor=white:fontsize=28:x={lx}:y=18:shadowcolor=black:shadowx=1:shadowy=1")

        if description:
            dt.append(f"drawbox=x=0:y=ih*0.705:w=iw:h=ih*0.155:color=0x{banner_hex}@0.90:t=fill:enable='gte(t,{T0})'")
            for i, line in enumerate(description.split('\n')[:2]):
                yp = 0.745 + i * 0.052
                dt.append(f"drawtext=text='{esc(line)}':{fb}fontcolor=white:fontsize=27:x=(w-tw)/2:y=ih*{yp:.3f}:shadowcolor=black:shadowx=1:shadowy=1:enable='gte(t,{T0})'")

        if size_text:
            sy  = 0.875 if description else 0.845
            dt.append(f"drawtext=text='{esc(size_text)}':{fb}fontcolor=white:fontsize=54:x=(w-tw)/2:y=ih*{sy:.3f}:shadowcolor=black:shadowx=2:shadowy=2:enable='gte(t,{T0+0.2})'")

        if brand:
            dt.append(f"drawtext=text='{esc(brand)}':{fr}fontcolor=white:fontsize=32:x=(w-tw)/2:y=ih*0.932:shadowcolor=black:shadowx=1:shadowy=1:enable='gte(t,{T0+0.3})'")

        vf_str = ','.join(dt) if dt else 'null'

        # ── Step 3: encode video from still image ─────────────────────────────
        run(['ffmpeg',
             '-loop', '1', '-framerate', str(FPS), '-t', str(DUR + 0.5),
             '-i', base_jpg,
             '-vf', vf_str,
             '-t', str(DUR),
             '-c:v', 'libx264', '-crf', '22', '-preset', 'fast',
             '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
             '-y', out_path])

    finally:
        import shutil
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate():
    import base64 as b64
    from PIL import Image

    data       = request.get_json(force=True, silent=True) or {}
    photos_b64 = data.get('photos', [])
    if not photos_b64:
        return jsonify({'error': 'Загрузите хотя бы одно фото'}), 400

    session_id  = uuid.uuid4().hex[:10]
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir()
    photo_path  = None

    for b64str in photos_b64[:1]:
        try:
            img = Image.open(io.BytesIO(b64.b64decode(b64str))).convert('RGB')
            img.thumbnail((1400, 1400), Image.LANCZOS)
            dest = session_dir / 'photo.jpg'
            img.save(str(dest), 'JPEG', quality=92)
            photo_path = str(dest)
            break
        except Exception:
            continue

    if not photo_path:
        return jsonify({'error': 'Не удалось обработать фото'}), 400

    form = {k: str(data.get(k, '')).strip()
            for k in ('size','brand','description','logo','logo_pos','banner_color')}
    if not re.match(r'^#[0-9a-fA-F]{6}$', form.get('banner_color','')):
        form['banner_color'] = '#bb1111'
    if not form.get('size'):
        return jsonify({'error': 'Введите размер шины'}), 400

    safe  = re.sub(r'[^a-zA-Z0-9_\-]', '_', form['size'])
    name  = f"{safe}_{session_id[:6]}.mp4"
    opath = str(OUTPUT_DIR / name)

    try:
        generate_video(photo_path, form, opath)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e)[:600], 'trace': traceback.format_exc()[-400:]}), 500
    finally:
        import shutil
        shutil.rmtree(str(session_dir), ignore_errors=True)

    threading.Thread(
        target=lambda: (time.sleep(7200), Path(opath).unlink(missing_ok=True)),
        daemon=True
    ).start()

    return jsonify({'url': f'/download/{name}', 'filename': name})


@app.route('/download/<filename>')
def download(filename):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return 'Не найден', 404
    return send_file(str(path), as_attachment=True, download_name=filename)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)

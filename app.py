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
            .replace("'", "\\'")
            .replace(':', '\\:')
            .replace('%', '\\%'))

def generate_video(photo_path, data, out_path):
    W, H  = 540, 960
    FPS   = 24
    DUR   = 10
    T0    = 5.8   # text appears at second 5.8

    fb = f"fontfile='{find_font(FONT_BOLD)}':" if find_font(FONT_BOLD) else ""
    fr = f"fontfile='{find_font(FONT_REG)}':"  if find_font(FONT_REG)  else ""

    logo        = data.get('logo',        '').strip()
    size_text   = data.get('size',        '').strip()
    brand       = data.get('brand',       '').strip()
    description = data.get('description', '').strip()
    banner_hex  = data.get('banner_color', '#bb1111').lstrip('#')
    logo_pos    = data.get('logo_pos',    'left')

    # ── Filter chain ──────────────────────────────────────────────────────────
    vf = []

    # Scale to 2x target, then zoompan down to target (gives room for zoom)
    vf.append(f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")

    # Slow zoom-in: z goes from 1.0 to 1.22 over 240 frames
    # Using n (frame index) instead of on for compatibility
    vf.append(
        f"zoompan=z='1.0+n*0.00092':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={FPS*DUR}:s={W}x{H}:fps={FPS}"
    )

    # Vignette
    vf.append("vignette=angle=PI/4")

    # Logo — always visible, top corner
    if logo:
        lx = '18' if logo_pos == 'left' else 'w-tw-18'
        vf.append(
            f"drawtext=text='{esc(logo)}':{fb}"
            f"fontcolor=white:fontsize=28:x={lx}:y=18:"
            f"shadowcolor=black:shadowx=1:shadowy=1"
        )

    # Coloured banner
    if description:
        vf.append(
            f"drawbox=x=0:y=ih*0.705:w=iw:h=ih*0.155:"
            f"color=0x{banner_hex}@0.90:t=fill:"
            f"enable='gte(t,{T0})'"
        )
        for i, line in enumerate(description.split('\n')[:2]):
            yp = 0.745 + i * 0.052
            vf.append(
                f"drawtext=text='{esc(line)}':{fb}"
                f"fontcolor=white:fontsize=27:x=(w-tw)/2:y=ih*{yp:.3f}:"
                f"shadowcolor=black:shadowx=1:shadowy=1:"
                f"enable='gte(t,{T0})'"
            )

    # Tyre size
    if size_text:
        sy = 0.875 if description else 0.845
        vf.append(
            f"drawtext=text='{esc(size_text)}':{fb}"
            f"fontcolor=white:fontsize=54:x=(w-tw)/2:y=ih*{sy:.3f}:"
            f"shadowcolor=black:shadowx=2:shadowy=2:"
            f"enable='gte(t,{T0+0.2})'"
        )

    # Brand
    if brand:
        vf.append(
            f"drawtext=text='{esc(brand)}':{fr}"
            f"fontcolor=white:fontsize=32:x=(w-tw)/2:y=ih*0.932:"
            f"shadowcolor=black:shadowx=1:shadowy=1:"
            f"enable='gte(t,{T0+0.25})'"
        )

    cmd = [
        'ffmpeg',
        '-loop', '1', '-framerate', str(FPS),
        '-t', str(DUR + 1),
        '-i', photo_path,
        '-vf', ','.join(vf),
        '-t', str(DUR),
        '-c:v', 'libx264', '-crf', '22', '-preset', 'fast',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        '-y', out_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])


# ── Flask routes ──────────────────────────────────────────────────────────────

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
            for k in ('size', 'brand', 'description', 'logo', 'logo_pos', 'banner_color')}
    if not re.match(r'^#[0-9a-fA-F]{6}$', form.get('banner_color', '')):
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

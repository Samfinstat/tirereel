import os, uuid, time, threading, math
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB

UPLOAD_DIR = Path('uploads')
OUTPUT_DIR = Path('output')
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Поиск шрифтов ─────────────────────────────────────────────────────────────
FONT_CANDIDATES_BOLD = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
    '/app/fonts/DejaVuSans-Bold.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
]
FONT_CANDIDATES_REG = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/app/fonts/DejaVuSans.ttf',
    'C:/Windows/Fonts/arial.ttf',
]


def find_font(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


# ── Видео-генерация ───────────────────────────────────────────────────────────

def hex_to_rgb(hex_color: str):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def ease_inout(t: float) -> float:
    return t * t * (3 - 2 * t)


def generate_video(photo_paths: list, data: dict, out_path: str):
    """
    Генерирует MP4 из фотографий шины с анимацией и текстом.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
    import numpy as np
    from moviepy.editor import ImageSequenceClip

    W, H = 540, 960
    FPS = 24
    DURATION = 10          # секунд на видео
    TEXT_START = 0.58      # текст появляется в 58% длительности
    FADE_IN_DUR = 0.55     # секунды на затухание текста

    total_frames = FPS * DURATION
    text_start_frame = int(total_frames * TEXT_START)
    n_photos = len(photo_paths)
    frames_per_photo = total_frames // n_photos
    fade_frames = int(FPS * 0.55)

    # Загружаем шрифты
    font_bold_path = find_font(FONT_CANDIDATES_BOLD)
    font_reg_path  = find_font(FONT_CANDIDATES_REG)

    from PIL import ImageFont
    def load_font(path, size):
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    fonts = {
        'logo':  load_font(font_bold_path, 28),
        'size':  load_font(font_bold_path, 64),
        'desc':  load_font(font_bold_path, 26),
        'brand': load_font(font_reg_path,  30),
        'small': load_font(font_reg_path,  20),
    }

    # Данные для текста
    logo        = data.get('logo', '').strip()
    size_text   = data.get('size', '').strip()
    brand       = data.get('brand', '').strip()
    description = data.get('description', '').strip()
    banner_hex  = data.get('banner_color', '#BB1111')
    logo_pos    = data.get('logo_pos', 'left')

    banner_rgb = hex_to_rgb(banner_hex)

    # ── Подготовка каждого фото ──────────────────────────────────────────────
    def prepare_photo(path):
        img = Image.open(path).convert('RGB')
        # Масштабируем чтобы заполнить кадр (с запасом 35% для зума)
        ratio = max(W / img.width, H / img.height) * 1.35
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        return img.resize((new_w, new_h), Image.LANCZOS)

    def make_bg(img):
        """Тёмный размытый фон из фото."""
        ratio = max(W / img.width, H / img.height)
        bg = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        x0 = (bg.width - W) // 2
        y0 = (bg.height - H) // 2
        bg = bg.crop((x0, y0, x0 + W, y0 + H))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
        bg = ImageEnhance.Brightness(bg).enhance(0.18)
        # Тёплый оттенок
        r, g, b = bg.split()
        r = ImageEnhance.Brightness(r).enhance(1.2)
        g = ImageEnhance.Brightness(g).enhance(1.05)
        return Image.merge('RGB', (r, g, b))

    def crop_frame(img, scale, pan_y_frac, W, H):
        """Кадрирование для Ken Burns: scale=1.0 → оригинал, >1 → зум."""
        sw = int(W / scale)
        sh = int(H / scale)
        # Центр + вертикальный сдвиг
        cx = img.width // 2
        cy = int(img.height * (0.5 - pan_y_frac * 0.08))
        x0 = max(0, cx - sw // 2)
        y0 = max(0, cy - sh // 2)
        x0 = min(x0, img.width  - sw)
        y0 = min(y0, img.height - sh)
        cropped = img.crop((x0, y0, x0 + sw, y0 + sh))
        return cropped.resize((W, H), Image.LANCZOS)

    def make_vignette(W, H):
        """Тёмная виньетка по краям."""
        vig = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(vig)
        steps = 40
        for i in range(steps):
            t = i / steps
            alpha = int(200 * (1 - t) ** 2.2)
            margin = int(min(W, H) * 0.5 * t)
            d.rectangle([margin, margin, W - margin, H - margin],
                        outline=(0, 0, 0, alpha), width=3)
        return vig

    def add_text(frame_img, t_sec, total_sec):
        """Накладывает текстовый оверлей на кадр."""
        t0 = total_sec * TEXT_START
        if t_sec < t0:
            return frame_img

        alpha_frac = min(1.0, (t_sec - t0) / FADE_IN_DUR)
        a = int(255 * alpha_frac)

        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)

        # Логотип
        if logo:
            lx = 18 if logo_pos == 'left' else None
            d.text((lx or W - 18, 18), logo,
                   font=fonts['logo'], fill=(255, 255, 255, a),
                   anchor='la' if lx else 'ra')

        # Баннер + описание
        banner_y = int(H * 0.705)
        banner_h = int(H * 0.155)
        if description:
            d.rectangle([0, banner_y, W, banner_y + banner_h],
                        fill=(*banner_rgb, int(225 * alpha_frac)))
            lines = description.replace('\\n', '\n').split('\n')[:3]
            for li, line in enumerate(lines):
                yp = banner_y + 14 + li * 34
                bbox = d.textbbox((0, 0), line, font=fonts['desc'])
                tw = bbox[2] - bbox[0]
                d.text(((W - tw) / 2, yp), line,
                       font=fonts['desc'], fill=(255, 255, 255, a))

        # Размер (крупно)
        if size_text:
            bbox = d.textbbox((0, 0), size_text, font=fonts['size'])
            tw = bbox[2] - bbox[0]
            sy = banner_y + banner_h + 14 if description else int(H * 0.84)
            d.text(((W - tw) / 2, sy), size_text,
                   font=fonts['size'], fill=(255, 255, 255, a))

        # Бренд
        if brand:
            bbox = d.textbbox((0, 0), brand, font=fonts['brand'])
            tw = bbox[2] - bbox[0]
            by = int(H * 0.935)
            d.text(((W - tw) / 2, by), brand,
                   font=fonts['brand'], fill=(255, 255, 255, int(210 * alpha_frac)))

        return Image.alpha_composite(frame_img.convert('RGBA'), overlay)

    # ── Генерация кадров ─────────────────────────────────────────────────────
    photos_full = [prepare_photo(p) for p in photo_paths]
    backgrounds = [make_bg(p) for p in photos_full]
    vignette    = make_vignette(W, H)

    frames_out = []

    for f in range(total_frames):
        # Какое фото?
        raw_idx = f // frames_per_photo
        photo_idx = min(raw_idx, n_photos - 1)
        t_in_photo = (f % frames_per_photo) / frames_per_photo   # 0..1

        # Ken Burns: масштаб 1.0 → 1.22, небольшой сдвиг вверх
        scale   = 1.0  + 0.22  * ease_inout(t_in_photo)
        pan_y   = 1.0  - t_in_photo                               # начинаем снизу

        photo_frame = crop_frame(photos_full[photo_idx], scale, pan_y, W, H)
        bg_frame    = backgrounds[photo_idx].copy()

        # Кроссфейд между фотографиями
        if n_photos > 1 and photo_idx < n_photos - 1:
            frame_in_photo = f % frames_per_photo
            if frame_in_photo >= frames_per_photo - fade_frames:
                fade_t = (frame_in_photo - (frames_per_photo - fade_frames)) / fade_frames
                next_idx = photo_idx + 1
                scale_next = 1.0 + 0.22 * ease_inout(0)
                pan_y_next = 1.0
                next_frame = crop_frame(photos_full[next_idx], scale_next, pan_y_next, W, H)
                next_bg    = backgrounds[next_idx].copy()
                photo_frame = Image.blend(photo_frame, next_frame, fade_t)
                bg_frame    = Image.blend(bg_frame,    next_bg,    fade_t)

        # Сборка кадра
        composite = Image.blend(bg_frame, photo_frame, 0.80)
        composite = composite.convert('RGBA')
        composite.paste(vignette, (0, 0), vignette)

        # Текст
        t_sec = f / FPS
        composite = add_text(composite, t_sec, DURATION)
        frames_out.append(np.array(composite.convert('RGB')))

    # ── Кодирование ─────────────────────────────────────────────────────────
    clip = ImageSequenceClip(frames_out, fps=FPS)
    clip.write_videofile(
        out_path, codec='libx264', audio=False,
        verbose=False, logger=None,
        ffmpeg_params=['-crf', '21', '-preset', 'fast', '-pix_fmt', 'yuv420p'],
    )
    clip.close()


# ── Flask маршруты ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate():
    # Получаем фото
    files = request.files.getlist('photos')
    if not files or not files[0].filename:
        return jsonify({'error': 'Загрузите хотя бы одно фото шины'}), 400

    # Сохраняем фото
    session_id = uuid.uuid4().hex[:10]
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir()

    photo_paths = []
    for f in files[:5]:   # максимум 5 фото
        ext  = Path(f.filename).suffix.lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
            continue
        name = f"{len(photo_paths):02d}{ext}"
        dest = session_dir / name
        f.save(str(dest))
        photo_paths.append(str(dest))

    if not photo_paths:
        return jsonify({'error': 'Неподдерживаемый формат. Используйте JPG или PNG'}), 400

    # Данные из формы
    data = {
        'size':         request.form.get('size', '').strip(),
        'brand':        request.form.get('brand', '').strip(),
        'description':  request.form.get('description', '').strip(),
        'logo':         request.form.get('logo', '').strip(),
        'logo_pos':     request.form.get('logo_pos', 'left'),
        'banner_color': request.form.get('banner_color', '#BB1111'),
    }

    if not data['size']:
        return jsonify({'error': 'Введите размер шины'}), 400

    # Генерируем видео
    safe_size = data['size'].replace('/', '-').replace(' ', '_')
    out_name  = f"{safe_size}_{session_id[:6]}.mp4"
    out_path  = str(OUTPUT_DIR / out_name)

    try:
        generate_video(photo_paths, data, out_path)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()[-600:]}), 500
    finally:
        # Удаляем загруженные фото
        import shutil
        shutil.rmtree(str(session_dir), ignore_errors=True)

    # Планируем удаление через 2 часа
    def cleanup():
        time.sleep(7200)
        Path(out_path).unlink(missing_ok=True)
    threading.Thread(target=cleanup, daemon=True).start()

    return jsonify({'url': f'/download/{out_name}', 'filename': out_name})


@app.route('/download/<filename>')
def download(filename):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return 'Файл не найден или истёк срок хранения (2 ч)', 404
    return send_file(str(path), as_attachment=True, download_name=filename)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

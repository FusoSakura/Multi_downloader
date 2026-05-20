"""
다기능 영상 다운로더 - Flask 웹 버전
- /                  메인 페이지
- POST /api/info     영상 정보/화질 목록 조회
- POST /api/download 백그라운드 다운로드 시작 (task_id 반환)
- GET  /api/progress/<task_id> 진행률 폴링
- GET  /api/file/<task_id>     완료된 파일 다운로드
"""

import os
import sys
import uuid
import zipfile
import threading
import webbrowser
from pathlib import Path


# ============================================================
# PyInstaller 번들 경로 처리
# ============================================================
def _resource_dir() -> Path:
    """번들이면 _MEIPASS(압축 해제 임시 폴더), 아니면 스크립트 폴더"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).parent

def _app_dir() -> Path:
    """쓰기 가능한 폴더 — 번들이면 exe 옆, 아니면 스크립트 폴더"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent

RESOURCE_DIR = _resource_dir()
APP_DIR      = _app_dir()

# 번들 시 _MEIPASS 안에 ffmpeg.exe 가 있으므로 그 폴더를 알려줌
FFMPEG_LOCATION = str(RESOURCE_DIR) if getattr(sys, 'frozen', False) else None


# ============================================================
# 시작 에러 처리 헬퍼
# - Windows에서 더블클릭 실행 시 콘솔이 즉시 닫히는 문제 방지
# ============================================================
def die(msg, code=1):
    """에러를 출력하고 사용자가 읽을 수 있게 멈춘 후 종료."""
    bar = "=" * 60
    sys.stderr.write(f"\n{bar}\n[!] {msg}\n{bar}\n")
    sys.stderr.flush()
    # Windows이거나, 콘솔 입력이 가능한 환경이면 Enter 대기
    if sys.platform == 'win32' and sys.stdin and sys.stdin.isatty():
        try:
            input("\n[Enter 키를 누르면 종료] ")
        except (EOFError, KeyboardInterrupt):
            pass
    elif sys.platform == 'win32':
        # 더블클릭으로 띄운 콘솔은 isatty가 False일 수 있어 fallback
        try:
            input("\n[Enter 키를 누르면 종료] ")
        except Exception:
            pass
    sys.exit(code)


# 의존성 import — 각각 개별 try로 감싸서 어느 패키지가 빠졌는지 명확히
try:
    from flask import (Flask, render_template, request,
                       jsonify, send_file, abort)
except ImportError as e:
    die(f"Flask가 설치되어 있지 않습니다.\n"
        f"  해결: pip install flask\n"
        f"  원본 에러: {e}")

try:
    import yt_dlp
except ImportError as e:
    die(f"yt-dlp가 설치되어 있지 않습니다.\n"
        f"  해결: pip install yt-dlp\n"
        f"  원본 에러: {e}")


# ============================================================
# 앱 설정
# ============================================================
app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / 'templates'),
    static_folder=str(RESOURCE_DIR / 'static'),
)

# 다운로드 결과 저장 폴더 — exe 옆에 생성 (번들 시 _MEIPASS 안은 쓰기 불가)
DOWNLOAD_ROOT = APP_DIR / 'downloads'
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# 진행 중인 task 상태 (스레드 안전)
tasks = {}          # task_id -> dict
tasks_lock = threading.Lock()


# ============================================================
# 헬퍼: 정보/화질 추출
# ============================================================
def _codec_label(vcodec):
    """vcodec 문자열을 사람이 읽기 좋은 이름으로 변환"""
    v = (vcodec or '').lower().split('.')[0]
    return {
        'avc1': 'H.264', 'h264': 'H.264',
        'vp09': 'VP9',   'vp9':  'VP9',
        'av01': 'AV1',
        'hev1': 'HEVC',  'hvc1': 'HEVC',
    }.get(v, (v or '?').upper())


def _codec_priority(vcodec):
    """
    코덱 선호 점수. 높을수록 우선 채택.
    H.264(3) > VP9(2) > 기타(1) > AV1(0).
    AV1은 호환성 문제로 가장 낮게 둔다.
    """
    v = (vcodec or '').lower()
    if v.startswith('avc1') or v.startswith('h264'):
        return 3
    if v.startswith('vp09') or v.startswith('vp9'):
        return 2
    if v.startswith('av01'):
        return 0
    return 1


def extract_qualities(info):
    """
    formats 배열에서 (height, fps) 조합별 대표 포맷 1개씩 추출.
    같은 (height, fps)에 여러 코덱이 있으면 H.264 > VP9 > 기타 > AV1 순으로 채택.
    정렬: height 내림차순 → 같은 height면 fps 내림차순.
    """
    formats = info.get('formats') or []
    best = {}  # (height, fps) -> format dict
    for f in formats:
        vcodec = (f.get('vcodec') or '').lower()
        if not vcodec or vcodec == 'none':
            continue
        h = f.get('height')
        if not h:
            continue
        # fps는 None일 수 있음 → 0으로 정규화(정렬/표시용)
        fps = f.get('fps')
        fps_key = int(round(fps)) if fps else 0
        key = (h, fps_key)

        cur = best.get(key)
        if cur is None:
            best[key] = f
            continue
        # 코덱 우선순위가 더 높으면 교체
        if _codec_priority(vcodec) > _codec_priority(cur.get('vcodec')):
            best[key] = f

    # height 내림차순, 그 안에서 fps 내림차순
    sorted_items = sorted(best.items(), key=lambda kv: (-kv[0][0], -kv[0][1]))

    return [{
        'height':   h,
        'fps':      fps_key if fps_key else None,
        'ext':      f.get('ext'),
        'codec':    _codec_label(f.get('vcodec')),
        'filesize': f.get('filesize') or f.get('filesize_approx'),
    } for (h, fps_key), f in sorted_items]


def fetch_info_dict(url):
    """다운로드 없이 메타데이터만 추출"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


# ============================================================
# 헬퍼: 진행률 훅
# ============================================================
def make_progress_hook(task_id):
    def hook(d):
        with tasks_lock:
            if task_id not in tasks:
                return
            status = d.get('status')
            if status == 'downloading':
                total = (d.get('total_bytes')
                         or d.get('total_bytes_estimate') or 0)
                downloaded = d.get('downloaded_bytes', 0) or 0
                percent = (downloaded / total * 100) if total else 0
                # 재생목록인 경우 현재 항목 위치
                info_dict = d.get('info_dict') or {}
                idx = info_dict.get('playlist_index')
                n = info_dict.get('n_entries')
                tasks[task_id].update({
                    'status': 'downloading',
                    'progress': percent,
                    'downloaded': downloaded,
                    'total': total,
                    'speed': (d.get('_speed_str') or '').strip(),
                    'eta': (d.get('_eta_str') or '').strip(),
                    'playlist_index': idx,
                    'playlist_total': n,
                })
            elif status == 'finished':
                # 한 항목 다운로드 완료(병합/변환은 postprocessor에서)
                tasks[task_id].update({
                    'status': 'processing',
                    'progress': 100,
                })
    return hook


# ============================================================
# 백그라운드 다운로드
# ============================================================
def run_download(task_id, url, mode, quality, fps=None):
    out_dir = DOWNLOAD_ROOT / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if mode == 'video':
            height = int(quality)
            # fps 조건절: 선택한 fps가 있으면 정확 매칭 우선,
            # 없으면 fps 무관. 60fps 선택 시 [fps>30], 30fps 선택 시 [fps<=30]
            # 으로 그룹을 구분해 의도와 다른 fps가 잡히는 것을 방지.
            if fps and int(fps) >= 50:
                fps_cond = '[fps>30]'          # 50/60fps 그룹
            elif fps:
                fps_cond = '[fps<=30]'         # 24/25/30fps 그룹
            else:
                fps_cond = ''                  # fps 정보 없음 → 조건 없음

            # 코덱 우선순위: H.264(avc1) > VP9(vp09) > (최후) 그 외.
            # AV1(av01)은 호환성 문제(구형 기기/플레이어에서 재생 불가)로
            # 명시적으로 배제 → 정말 다른 선택지가 없을 때만 허용.
            # [vcodec^=avc1]  : H.264만
            # [vcodec^=vp09]  : VP9만  (yt-dlp의 vcodec은 'vp09...' 형식)
            # [vcodec!^=av01] : AV1 제외 (그 외 코덱 허용)
            H = height
            ydl_opts = {
                **(({'ffmpeg_location': FFMPEG_LOCATION}) if FFMPEG_LOCATION else {}),
                'format': (
                    # --- fps 매칭 + H.264 ---
                    f'bestvideo[height<={H}]{fps_cond}[vcodec^=avc1]+bestaudio[ext=m4a]/'
                    f'bestvideo[height<={H}]{fps_cond}[vcodec^=avc1]+bestaudio/'
                    # --- fps 매칭 + VP9 ---
                    f'bestvideo[height<={H}]{fps_cond}[vcodec^=vp09]+bestaudio/'
                    # --- fps 매칭 + AV1만 아니면 허용 ---
                    f'bestvideo[height<={H}]{fps_cond}[vcodec!^=av01]+bestaudio/'
                    # --- fps 조건 해제 + H.264 ---
                    f'bestvideo[height<={H}][vcodec^=avc1]+bestaudio[ext=m4a]/'
                    f'bestvideo[height<={H}][vcodec^=avc1]+bestaudio/'
                    # --- fps 조건 해제 + VP9 ---
                    f'bestvideo[height<={H}][vcodec^=vp09]+bestaudio/'
                    # --- fps 조건 해제 + AV1만 아니면 허용 ---
                    f'bestvideo[height<={H}][vcodec!^=av01]+bestaudio/'
                    # --- 단일 파일(progressive) 중 AV1 아닌 것 ---
                    f'best[height<={H}][vcodec!^=av01]/'
                    # --- 최후의 수단: 무엇이든 (AV1 포함될 수 있음) ---
                    f'bestvideo[height<={H}]+bestaudio/'
                    f'best[height<={H}]'
                ),
                'outtmpl': str(out_dir / '%(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'progress_hooks': [make_progress_hook(task_id)],
                'noplaylist': False,
                'quiet': True,
                'no_warnings': True,
                # restrictfilenames 제거: 한글 등 비ASCII 문자가 보존되도록
                # (yt-dlp 기본 동작이 OS 호환 가능한 sanitize 수행)
            }
        else:  # audio
            kbps = int(quality)
            ydl_opts = {
                **(({'ffmpeg_location': FFMPEG_LOCATION}) if FFMPEG_LOCATION else {}),
                'format': 'bestaudio/best',
                'outtmpl': str(out_dir / '%(title)s.%(ext)s'),
                'progress_hooks': [make_progress_hook(task_id)],
                'noplaylist': False,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': str(kbps),
                }],
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # 결과 파일 모음 — 영상은 .mp4, 오디오는 .mp3, 그 외 잔여 파일은 제외
        if mode == 'video':
            valid_ext = {'.mp4', '.mkv', '.webm'}
        else:
            valid_ext = {'.mp3'}
        result_files = [p for p in out_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in valid_ext]

        if not result_files:
            # 후처리 실패 등으로 매칭 안 됐을 때 fallback
            result_files = [p for p in out_dir.iterdir() if p.is_file()]

        if not result_files:
            raise RuntimeError("다운로드된 파일을 찾을 수 없습니다.")

        # 여러 개면 ZIP으로 묶기
        if len(result_files) > 1:
            zip_path = out_dir / 'playlist.zip'
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in result_files:
                    zf.write(p, arcname=p.name)
            final_path = zip_path
            final_name = f'playlist_{len(result_files)}_items.zip'
        else:
            final_path = result_files[0]
            final_name = final_path.name

        with tasks_lock:
            tasks[task_id].update({
                'status': 'done',
                'progress': 100,
                'filename': final_name,
                'filepath': str(final_path),
                'filesize': final_path.stat().st_size,
            })

    except Exception as e:
        with tasks_lock:
            tasks[task_id].update({
                'status': 'error',
                'error': f'{type(e).__name__}: {e}',
            })


# ============================================================
# 라우트
# ============================================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['POST'])
def api_info():
    payload = request.get_json(silent=True) or {}
    url = (payload.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'URL이 비어 있습니다.'}), 400

    try:
        info = fetch_info_dict(url)
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'정보 조회 실패: {e}'}), 400
    except Exception as e:
        return jsonify({'error': f'{type(e).__name__}: {e}'}), 500

    is_playlist = info.get('_type') == 'playlist'
    if is_playlist:
        entries = [e for e in (info.get('entries') or []) if e]
        if not entries:
            return jsonify({'error': '재생목록이 비어 있습니다.'}), 400
        # 화질 목록은 첫 영상 기준 (entries는 메타가 얕을 수 있음)
        first_url = entries[0].get('webpage_url') or entries[0].get('url')
        try:
            first_info = fetch_info_dict(first_url)
        except Exception:
            first_info = entries[0]
        return jsonify({
            'is_playlist': True,
            'title': info.get('title') or 'Playlist',
            'count': len(entries),
            'thumbnail': (first_info.get('thumbnail')
                          or (first_info.get('thumbnails') or [{}])[-1].get('url')),
            'qualities': extract_qualities(first_info),
        })

    return jsonify({
        'is_playlist': False,
        'title': info.get('title') or '(no title)',
        'duration': info.get('duration'),
        'uploader': info.get('uploader') or info.get('channel'),
        'thumbnail': (info.get('thumbnail')
                      or (info.get('thumbnails') or [{}])[-1].get('url')),
        'qualities': extract_qualities(info),
    })


@app.route('/api/download', methods=['POST'])
def api_download():
    payload = request.get_json(silent=True) or {}
    url = (payload.get('url') or '').strip()
    mode = payload.get('mode')
    quality = payload.get('quality')
    fps = payload.get('fps')  # video일 때만 사용, 선택적

    if not url:
        return jsonify({'error': 'URL이 없습니다.'}), 400
    if mode not in ('video', 'audio'):
        return jsonify({'error': 'mode는 video 또는 audio여야 합니다.'}), 400
    if quality is None:
        return jsonify({'error': 'quality가 지정되지 않았습니다.'}), 400
    try:
        int(quality)
    except (TypeError, ValueError):
        return jsonify({'error': 'quality는 정수여야 합니다.'}), 400
    # fps는 선택적 — 값이 있으면 정수여야 함
    if fps is not None:
        try:
            fps = int(fps)
        except (TypeError, ValueError):
            return jsonify({'error': 'fps는 정수여야 합니다.'}), 400

    task_id = uuid.uuid4().hex
    with tasks_lock:
        tasks[task_id] = {'status': 'queued', 'progress': 0}

    th = threading.Thread(
        target=run_download,
        args=(task_id, url, mode, quality, fps),
        daemon=True,
    )
    th.start()
    return jsonify({'task_id': task_id})


@app.route('/api/progress/<task_id>')
def api_progress(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task:
            return jsonify({'error': '작업을 찾을 수 없습니다.'}), 404
        # filepath는 클라이언트에 노출하지 않음
        return jsonify({k: v for k, v in task.items() if k != 'filepath'})


@app.route('/api/file/<task_id>')
def api_file(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task or task.get('status') != 'done':
            abort(404)
        filepath = task.get('filepath')
        filename = task.get('filename')
    if not filepath or not Path(filepath).exists():
        abort(404)
    return send_file(filepath, as_attachment=True, download_name=filename)


if __name__ == '__main__':
    import socket

    HOST = '127.0.0.1'
    PORT = 5000

    # 포트 사전 점검: Werkzeug는 OSError를 자체적으로 sys.exit 처리하므로
    # except 절이 잡지 못함. 미리 같은 방식으로 bind 시도해 확인한다.
    def _check_port(host, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        finally:
            s.close()

    try:
        _check_port(HOST, PORT)
    except OSError as e:
        die(f"포트 {PORT}이(가) 이미 사용 중이거나 권한이 없습니다.\n"
            f"  해결 1: 다른 Flask/Python 프로세스를 종료하세요.\n"
            f"           Windows: netstat -ano | findstr :5000\n"
            f"           Linux/macOS: lsof -i :5000\n"
            f"  해결 2: app.py 안의 PORT = 5000 → 다른 숫자(예: 5050)로 변경\n"
            f"  원본 에러: {e}")

    # 시작 시 진단 정보
    print("=" * 60)
    print("  FETCH · Media Downloader")
    print("=" * 60)
    print(f"  Python      : {sys.version.split()[0]} ({sys.platform})")
    print(f"  Flask       : OK")
    print(f"  yt-dlp      : {yt_dlp.version.__version__}")
    print(f"  Working dir : {Path(__file__).parent}")
    print(f"  Downloads   : {DOWNLOAD_ROOT}")
    print(f"  Listening   : http://{HOST}:{PORT}")
    print("=" * 60)
    print("  브라우저에서 위 주소로 접속하세요. 종료는 Ctrl+C.")
    print()

    # 서버가 뜬 뒤 1.2초 후 브라우저 자동 열기
    threading.Timer(1.2, lambda: webbrowser.open(f'http://{HOST}:{PORT}')).start()

    try:
        # use_reloader=False: 전역 task dict와 스레드가 있어 reloader 비활성
        app.run(host=HOST, port=PORT, debug=False,
                threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n[*] 종료합니다.")
    except Exception as e:
        die(f"예기치 못한 오류: {type(e).__name__}: {e}")

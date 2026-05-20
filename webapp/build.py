#!/usr/bin/env python3
"""
빌드 스크립트: ffmpeg 자동 다운로드 → PyInstaller exe 생성
실행: python build.py
"""
import os
import sys
import zipfile
import subprocess
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent

FFMPEG_ZIP_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)
FFMPEG_TARGETS = {"ffmpeg.exe", "ffprobe.exe"}


def step(msg: str):
    print(f"\n{'=' * 60}\n  {msg}\n{'=' * 60}")


def download_ffmpeg():
    missing = [n for n in FFMPEG_TARGETS if not (HERE / n).exists()]
    if not missing:
        print("[OK] ffmpeg.exe / ffprobe.exe 이미 존재 — 건너뜀")
        return

    step(f"ffmpeg 다운로드 중 (약 70~110 MB)...")
    print(f"  URL: {FFMPEG_ZIP_URL}")
    zip_path = HERE / "_ffmpeg_tmp.zip"

    def _progress(block, block_size, total):
        done = block * block_size
        pct = min(done / total * 100, 100) if total else 0
        mb = done // (1024 * 1024)
        total_mb = total // (1024 * 1024) if total else "?"
        print(f"\r  {pct:5.1f}%  {mb} MB / {total_mb} MB", end="", flush=True)

    try:
        urllib.request.urlretrieve(FFMPEG_ZIP_URL, zip_path, reporthook=_progress)
        print()
    except Exception as e:
        print(f"\n[!] 다운로드 실패: {e}")
        print("    ffmpeg.exe / ffprobe.exe 를 직접 webapp 폴더에 복사하세요.")
        print("    다운로드: https://github.com/BtbN/FFmpeg-Builds/releases")
        sys.exit(1)

    step("ffmpeg.exe / ffprobe.exe 추출 중")
    extracted = set()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = member.rsplit("/", 1)[-1]
            if name in FFMPEG_TARGETS:
                data = zf.read(member)
                out = HERE / name
                out.write_bytes(data)
                extracted.add(name)
                print(f"  {name}  ({len(data) // 1024:,} KB)")

    zip_path.unlink()

    if extracted != FFMPEG_TARGETS:
        missing_now = FFMPEG_TARGETS - extracted
        print(f"[!] 추출 실패: {missing_now}")
        sys.exit(1)

    print("[OK] ffmpeg 준비 완료")


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        import PyInstaller as _pi
        print(f"[OK] PyInstaller {_pi.__version__} 이미 설치됨")
    except ImportError:
        step("PyInstaller 설치 중")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )


def build_exe():
    step("PyInstaller 빌드 시작")

    dist_dir  = HERE / "dist"
    build_dir = HERE / "build"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",                          # 서버 로그 표시용 콘솔 창
        "--name", "MultiDownloader",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(HERE),
        # Flask 템플릿·정적 파일
        "--add-data", f"{HERE / 'templates'};templates",
        "--add-data", f"{HERE / 'static'};static",
        # ffmpeg 바이너리
        "--add-binary", f"{HERE / 'ffmpeg.exe'};.",
        "--add-binary", f"{HERE / 'ffprobe.exe'};.",
        # yt-dlp 전체 모듈 (수백 개의 extractor 포함)
        "--collect-all", "yt_dlp",
        # Flask 계열 hidden import
        "--hidden-import", "flask",
        "--hidden-import", "werkzeug",
        "--hidden-import", "werkzeug.serving",
        "--hidden-import", "werkzeug.routing",
        "--hidden-import", "jinja2",
        "--hidden-import", "markupsafe",
        "--hidden-import", "click",
        "--hidden-import", "itsdangerous",
        str(HERE / "app.py"),
    ]

    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print("\n[!] PyInstaller 빌드 실패. 위 오류 메시지를 확인하세요.")
        sys.exit(result.returncode)

    exe = dist_dir / "MultiDownloader.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        step("빌드 완료!")
        print(f"  파일 위치: {exe}")
        print(f"  파일 크기: {size_mb:.1f} MB")
        print(f"\n  → MultiDownloader.exe 를 더블클릭하면 실행됩니다.")
        print(f"    (yt-dlp, ffmpeg, Flask 모두 내장)")
    else:
        print("[!] exe 파일이 생성되지 않았습니다.")
        sys.exit(1)


if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  FETCH · MultiDownloader  빌드 스크립트")
    print("=" * 60)
    download_ffmpeg()
    ensure_pyinstaller()
    build_exe()

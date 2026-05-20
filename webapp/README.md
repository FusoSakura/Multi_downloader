# FETCH — Flask 기반 영상/오디오 다운로더

yt-dlp + ffmpeg + Flask로 만든 로컬 웹 다운로더.
브라우저에서 URL을 넣고 화질·음질을 골라 받는 구조.

## 파일 구조
```
webapp/
├── app.py              ← Flask 백엔드 (라우트 + 스레드 + 진행률 훅)
├── requirements.txt
├── templates/
│   └── index.html      ← UI 마크업
└── static/
    ├── style.css       ← 산업용 콘솔 디자인
    └── app.js          ← 상태 머신 + 폴링
```

## 실행 방법

```bash
# 1) 의존성 설치
pip install -r requirements.txt

# 2) ffmpeg가 PATH에 잡혀 있어야 함
#    Windows: winget install ffmpeg  (또는 https://www.gyan.dev/ffmpeg/builds/)
#    macOS:   brew install ffmpeg
#    Linux:   sudo apt install ffmpeg

# 3) 실행
python app.py

# 브라우저로 http://127.0.0.1:5000 접속
```

## API 요약

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET  | `/`                        | 메인 UI |
| POST | `/api/info`                | `{url}` → 메타데이터 + 화질 목록 |
| POST | `/api/download`            | `{url, mode, quality}` → `{task_id}` |
| GET  | `/api/progress/<task_id>`  | 진행률 폴링 (500ms 권장) |
| GET  | `/api/file/<task_id>`      | 완료된 파일 다운로드 |

`mode`는 `"video"` 또는 `"audio"`, `quality`는 정수(영상=height, 오디오=kbps).

## 동작 흐름
1. 사용자가 URL 입력 → `/api/info`로 메타데이터/화질 목록 조회
2. 모드(VIDEO/AUDIO) + 화질/비트레이트 선택
3. `/api/download` 호출 → 백그라운드 스레드에서 yt-dlp 실행, `task_id` 반환
4. JS가 `/api/progress/<task_id>`를 500ms마다 폴링하여 UI 업데이트
5. 완료 시 `/api/file/<task_id>`로 결과 파일 다운로드
   - 재생목록이면 여러 파일을 ZIP으로 묶어서 전송

## PyInstaller로 .exe 만들기 (Windows)

이전 v3.9 다운로더처럼 단일 실행파일로 묶을 때는 `templates/`와 `static/`을
같이 포함해야 함:

```bash
pyinstaller --onefile --noconsole ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  app.py
```

ffmpeg.exe도 같은 폴더에 두거나, `app.py`에서 `ydl_opts`에 `'ffmpeg_location'`
키로 경로 지정 가능.

## 외부 기기에서 접속하려면
`app.py` 마지막 줄을 `host='0.0.0.0'`으로 변경. 같은 와이파이의 폰/태블릿에서
`http://<PC의 IP>:5000`으로 접근 가능. (방화벽 허용 필요)

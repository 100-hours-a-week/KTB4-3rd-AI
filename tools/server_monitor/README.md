# 서버 모니터링 봇 로컬 실행·시연·벤치마크

로컬과 배포는 env 값만 다르다. 코드·설정(`app/Server_Monitor_Bot/config/config.toml`)은 그대로 두고, 아래 순서대로 각자 PC에서 띄운다.

## 1. 준비

| 항목 | 내용 |
|---|---|
| 도구 | Docker Desktop, Python 3.12(`.venv`) |
| 봇 토큰 | 각자 만든다. 같은 텔레그램 토큰은 한 곳에서만 받을 수 있어 팀원 토큰을 같이 쓰면 메시지를 서로 뺏는다 |
| 텔레그램 | BotFather에서 봇 생성 → `TELEGRAM_BOT_TOKEN`, 내 사용자 id → `Admin_Telegram_ID` |
| 디스코드(선택) | Developer Portal에서 앱·봇 생성(Message Content Intent 켜기) → 내 서버에 초대 → `DISCORD_BOT_TOKEN`, 채널 id → `DISCORD_CHANNEL_ID`, 내 사용자 id → `Admin_Discord_ID`. 그 채널에 글을 쓸 수 있는 사람은 모두 팀원으로 본다 |
| OpenAI | `OPENAI_API_KEY_1`(또는 `OPENAI_API_KEY_MONITOR`). 판단 모델(Decisions API)도 같은 키로 부른다 |

## 2. AI DB(MySQL + Qdrant) 띄우기

```bash
cd infra/ai-db
cp .env.example .env        # 세 값을 각자 정해서 채운다
docker compose up -d
```

- `mysql_data/`, `qdrant_data/`가 이 폴더에 생긴다. git에 올리지 않는다.
- 처음 뜰 때 `ai_local` DB·계정과 스키마가 만들어진다(`init/01_stages.sh`).

## 3. 앱 + 봇 띄우기

```bash
cp .env.example .env        # 1절 값과 아래 세 값을 채운다
# AI_DB_URL=mysql://ai_local:<AI_DB_PASSWORD>@host.docker.internal:3306/ai_local?ssl=required
# AI_QDRANT_URL=http://host.docker.internal:6333
# AI_QDRANT_API_KEY=<QDRANT_API_KEY>
# DRILL_IMAGE_URL=http://host.docker.internal:8766/ok.png
docker build --build-arg EXTRAS=monitor -t sbm-ai:test .
docker run -d --name sbm-app --env-file .env -p 127.0.0.1:8000:8000 sbm-ai:test
```

- 스테이지는 `AI_DB_URL`의 DB 이름으로 정해진다(`ai_local` → local). 배포는 같은 이미지에 `ai_dev`·`ai_prod` DB와 배포용 토큰만 다르다.
- 텔레그램에서 봇에게 `/help`, 디스코드 채널에 `/help`를 보내 답이 오면 된다.

## 4. 시연용 장애 만들기(`demo.py`)

```bash
.venv/bin/python tools/server_monitor/demo.py serve      # 테스트 이미지 서버(:8766, ok·missing·slow·broken)
.venv/bin/python tools/server_monitor/demo.py missing 20 # 이미지 404 요청 20건 → 1분 안에 알림
.venv/bin/python tools/server_monitor/demo.py broken 20  # 이미지 서버 500
.venv/bin/python tools/server_monitor/demo.py rate429 20 # OpenAI 429 기록 20건(컨테이너 안에서 직접 넣음)
```

| env | 기본값 | 뜻 |
|---|---|---|
| `DEMO_IMAGE_PORT` | 8766 | 테스트 이미지 서버 포트 |
| `DEMO_APP_URL` | http://127.0.0.1:8000 | 요청을 보낼 앱 주소 |
| `DEMO_CONTAINER` | sbm-app | `rate429`가 들어갈 컨테이너 |

## 5. 벤치마크(`bench.py`)

```bash
.venv/bin/python tools/server_monitor/bench.py run            # 묶음 0~9 전체 → bench_raw.json
.venv/bin/python tools/server_monitor/bench.py run 8 9        # 판단 모델·대기열만
.venv/bin/python tools/server_monitor/bench.py report bench_raw.json [v1_raw.json]
```

- 측정은 봇 토큰 없이 띄운 컨테이너에서 한다(실제 봇과 측정이 메시지를 나눠 받지 않게). 텔레그램 왕복을 잴 때만 `BENCH_TG_TOKEN`을 넘긴다.
- 측정이 지식·문서·대화를 남기므로 시작 전에 MySQL·Qdrant를 백업하고 끝나면 되돌린다.

| env | 기본값 | 뜻 |
|---|---|---|
| `BENCH_CONTAINER` | sbm-app | 측정할 컨테이너 |
| `BENCH_DOCS` | samples/ | 업로드 측정에 쓸 문서 폴더 |
| `BENCH_SCALE` | 1 | 반복 횟수 배율 |
| `BENCH_OUT` | bench_raw.json | 결과 파일 |

## 6. samples

| 파일 | 쓰임 |
|---|---|
| T1~T5 | 문서 업로드·RAG 질문 시험(md·txt·docx·pdf·겹침) |
| T6_깨진파일.pdf | 변환 실패 처리 시험 |
| ok.png | 테스트 이체 화면(가상 이름·계좌) |
| decisions.json | 판단 모델 정확도 시험(의도 28 · 질문 종류 20) |

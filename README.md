# algo-runner-starter

URL로 들어가서 **알고리즘을 돌려주는** 웹앱 스타터. Agent Platform 에서 검증된 부분만
추려 만든 깡통 스켈레톤이다. 에이전트·팀·학습 같은 건 다 뺐다.

구성: **비밀번호 1개 게이트 + 입력(폼 파라미터 + 파일) → 알고리즘(파이썬 + 필요시 Claude) →
결과 + 외부 공개(Cloudflare 터널)**.

```
당신이 넣는 것                버튼 누르면            나오는 것
- 파라미터(숫자/텍스트)   →   POST /api/run    →   결과(JSON)
- (선택) 파일                  algorithm.py 실행
```

---

## 0. 새 컴퓨터에서 처음 한 번 (Windows 가정)

전제: Python 3.12+(3.14 OK), `uv`, Node 18+, `pnpm`. (없으면 winget 으로 설치)

```powershell
# 백엔드
cd backend
uv sync
# 프런트
cd ../frontend
pnpm install
```

`.env` 만들기: `copy .env.example .env` 후 값 채움(아래 §3).

## 1. 실행 (개발)

두 개 띄운다(터미널 2개 또는 scripts/dev.ps1):
```powershell
# 터미널 A — 백엔드 (8000)
cd backend; uv run python -m algo_runner._serve
# 터미널 B — 프런트 (3000)
cd frontend; pnpm dev
```
브라우저 http://localhost:3000 → 비밀번호 입력 → 폼/파일 넣고 실행.

## 2. ★ 당신 알고리즘 넣는 곳 — 딱 한 파일

`backend/src/algo_runner/algorithm.py` 의 `run_algorithm()` 안에 로직을 쓴다.
- 입력: `params`(dict, 폼 값), `file_bytes`/`file_name`(업로드 파일, 없으면 None)
- 출력: JSON 으로 직렬화 가능한 dict
- Claude 가 필요하면 `from .llm import ask_claude` 호출(키 없으면 예외 → 알아서 처리).

프런트에 입력 칸을 더 넣고 싶으면 `frontend/src/app/page.tsx` 의 폼만 고치면 된다.

## 3. .env 키
```
APP_PASSWORD=원하는비번        # 비우면 인증 없음(공개)
AUTH_SECRET=                   # 비우면 자동 생성·재사용
ALLOWED_ORIGINS=               # 외부공개 URL(쉼표). 비우면 localhost:3000
PORT=8000
FRONTEND_PORT=3000
# Claude 쓸 때만(둘 중 하나):
ANTHROPIC_API_KEY=
CLAUDE_CODE_OAUTH_TOKEN=
CLOUDFLARED_PATH=             # 비우면 PATH·WinGet 에서 자동 탐색
```

## 4. 외부 공개 (URL로 남이 들어오게)
1. cloudflared 설치: `winget install --id Cloudflare.cloudflared`
2. 앱 실행 중 상태에서: `POST /api/admin/tunnel/start` (또는 프런트의 "공개 켜기" 버튼)
   → `https://...trycloudflare.com` 주소가 나온다. 그 주소를 알려주면 끝.
3. 무료·임시 주소(켤 때마다 바뀜). 고정 주소는 도메인 + named tunnel 필요.
- same-origin 프록시(next.config rewrites)로 **프런트 포트 하나만** 열면 API까지 동작.

---

## ⚠️ Windows 함정 모음 (이미 해결해둠 — 다시 부딪히지 말 것)

- **asyncio subprocess**: Windows 기본 루프는 subprocess 미지원 → `_serve.py` 가 winloop 로 띄운다.
  claude-agent-sdk(`claude` CLI spawn) 쓰면 필수.
- **claude-agent-sdk cwd 깨짐**: Windows 에서 `cwd`에 백슬래시 경로를 주면 슬러그화돼 엉뚱한 폴더에
  씀. → cwd 주지 말고 **절대경로**로 파일 작업, 필요시 `sandbox=False`.
- **uv/pnpm 백그라운드 실행**: 백그라운드 셸은 PATH 를 못 잡을 수 있음 → 풀패스로 실행.
- **reloader 고아 프로세스**: `--reload` 가 Windows 에서 파일변경 감지 불안정 + 워커 고아화.
  개발 중 반복 재시작하면 포트 점유 프로세스를 트리(`taskkill /T`)로 정리.
- **다운로드/SSE 인증**: `<a href>` 다운로드는 토큰을 못 실음 → `fetch + Authorization` 후 blob 저장.
  curl 로 POST 테스트 시 `Expect:` 헤더 끄기(`-H "Expect:"`).
- **한글 콘솔(cp949)**: 출력에 깨지면 파일로 써서 확인.

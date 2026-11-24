# opencode2openai

Repo: **https://github.com/Quan3s/opencode2openai** (public)

App Python **không cần dependency** (`stdlib` của Python 3.10+) đứng giữa OpenCode và client OpenAI:
client gửi request theo chuẩn **OpenAI Chat Completions / Responses**, app dịch sang API của OpenCode
rồi trả về response đúng format OpenAI (JSON hoặc SSE streaming).

```
OpenAI SDK / Cursor / Cline / LibreChat
        │  /v1/chat/completions  (OpenAI format)
        ▼
  opencode2openai  (app Python này)
        │  mode=opencode       → POST /api/experimental/generate/text   (opencode serve)
        │  mode=opencode_free  → POST /openai/v1/chat/completions       (CLI shape, không cần serve)
        │  mode=openai         → POST /v1/chat/completions              (opencode2api gateway)
        ▼
   OpenCode / OpenCode Zen
```

**Có sẵn 3 lớp dùng:** bridge HTTP (OpenAI-compatible), CLI `o2a`, và web dashboard React.
Bridge core và CLI chỉ dùng stdlib; Bun chỉ là transport TLS cho chế độ `opencode_free`;
dashboard là React/Vite (chỉ cần build lúc đóng gói, runtime không cần Node).

## Chạy nhanh

```bash
cd opencode2openai
cp .env.example .env        # chỉnh key + mode cho phù hợp
python3 app.py              # mặc định http://0.0.0.0:8080
```

Hoặc dùng biến môi trường trực tiếp:

```bash
CLIENT_API_KEY=my-secret UPSTREAM_MODE=opencode \
OPENCODE_BASE_URL=http://127.0.0.1:4096 python3 app.py
```

> File `.env` được đọc tự động, biến môi trường thật có độ ưu tiên cao hơn.
> Có thể thay bằng file JSON: `python3 app.py --config config.json` (key trùng tên env).

## CLI wrapper (`o2a`)

Wrapper nhỏ bằng stdlib, dùng trực tiếp không cần cài dependency:

```bash
./bin/o2a serve --port 8080                 # chạy bridge (đọc .env, exec app.py)
./bin/o2a models                            # liệt kê model
./bin/o2a chat "Chỉ trả lời đúng chữ OK."    # chat JSON
./bin/o2a chat "Đếm 1..5" --stream          # chat SSE, in theo thời gian thực
./bin/o2a chat "chào bạn" --responses       # gọi /v1/responses
./bin/o2a config set CLIENT_API_KEY my-key  # ghi vào .env
./bin/o2a config list                       # liệt kê (key/token bị che)
./bin/o2a doctor                            # kiểm tra Bun + /health
```

Mặc định CLI gọi `http://127.0.0.1:8080` với key `demo-local-key`; đổi bằng
`--base` / `--key` hoặc biến `O2A_BASE_URL` / `CLIENT_API_KEY`.

Tool call từ CLI:

```bash
cat > tools.json <<'JSON'
[{"type":"function","function":{"name":"calculator",
  "description":"Tính biểu thức toán học",
  "parameters":{"type":"object","properties":{"expression":{"type":"string"}}}}}]
JSON
./bin/o2a chat "Tính 12*(3+4)" --tools-file tools.json --tool-result 84
```

Response in ra `[tool_call] {...}`; `--tool-result` gửi kết quả tool vòng tiếp theo.
Tên client tool được khôi phục từ dạng `mcp_<hash>` của upstream.

## Web dashboard (React)

Dashboard chạy tại `/` (và `/dashboard`) khi đã build. Giao diện tối, responsive,
icon **inline SVG** (không dùng icon font/CDN), hỗ trợ:

- chat JSON hoặc streaming SSE, nút dừng giữa chừng;
- đổi base URL, **API key** (lưu trong `localStorage`, input dạng password), model,
  system prompt, bật/tắt tool definitions;
- thẻ `tool_call` hiển thị tên tool + arguments JSON; tool `calculator` tự chạy và
  gửi kết quả vòng tiếp, tool khác cho phép nhập result thủ công;
- copy tin nhắn, tạo cuộc trò chuyện mới, trạng thái kết nối.

Phát triển local (Vite proxy `/v1` → bridge cổng `8081`):

```bash
cd dashboard
npm install
npm run dev                 # http://localhost:8080
```

Build production (bridge tự serve `dashboard/dist`):

```bash
cd dashboard && npm run build && cd ..
python3 app.py              # dashboard tại http://localhost:8080/
```

## Ba chế độ

### 1. `UPSTREAM_MODE=opencode` (mặc định) — dịch từ OpenCode API

Khởi động OpenCode server nếu chưa có:

```bash
opencode serve --port 4096
```

Giả sử `opencode serve` đang chạy ở `http://127.0.0.1:4096` (đổi bằng `OPENCODE_BASE_URL`).
App dùng endpoint stateless `POST /api/experimental/generate/text` của OpenCode:

- gộp `messages` thành một prompt có nhãn `System/User/Assistant/Tool result`;
- map `model` thành `{"providerID": "opencode", "id": "big-pickle"}`;
- nhận `{"data": {"text": "..."}}` và đóng gói thành `chat.completion` / `response` của OpenAI.

`GET /v1/models` lấy danh sách từ `GET /api/model` của OpenCode.

### 2. `UPSTREAM_MODE=opencode_free` — tái tạo client free của OpenCode

Không cần `opencode serve` và không cần gateway trung gian. Bridge dựng lại request
mà CLI OpenCode 2.x gửi tới `https://opencode.ai/inference`:

```dotenv
UPSTREAM_MODE=opencode_free
OPENCODE_FREE_BASE_URL=https://opencode.ai/inference
# Tự động: UPSTREAM_API_KEY=public
# Tự động: UPSTREAM_USER_AGENT=opencode/latest/2.3.0/cli
```

Những phần được tái tạo:

- endpoint `/openai/v1/chat/completions` với `Authorization: Bearer public`;
- UA `opencode/latest/2.3.0/cli`, `x-opencode-client: cli`, session id `ses_...`, project id 40-hex,
  header trace `b3`/`traceparent`;
- system prompt CLI 2.3.0 (capture từ request thật) + environment block và 41 tool definitions chính thức;
- client tool đổi tên thành `mcp_<sha256[:12]>` rồi khôi phục tên gốc trong response;
- `stream: true`, `stream_options.include_usage`, `max_completion_tokens: 32000`.

**Transport:** mã Python vẫn dùng toàn bộ thư viện chuẩn, nhưng request free được gửi qua
một helper Bun nhỏ (`assets/bun_http.js`). Lý do: upstream chặn theo TLS fingerprint —
cùng một body/header, `curl` và Python `urllib` nhận `429`, còn Bun (cùng runtime với CLI
chính thức) nhận `200`. Đặt `O2A_BUN=/đường/dẫn/tới/bun` nếu bun không có trong `PATH`;
nếu không có bun, bridge fallback sang `urllib` và có thể lại gặp `429`.

Bridge có thể gom SSE thành JSON hoặc passthrough SSE theo thời gian thực, kèm `usage`
và `tool_calls` thật từ upstream.

> `429 FreeUsageLimitError` là thông báo chung của upstream free lane, **không phải hết quota của
> `big-pickle`** (model này không có giới hạn dạng vài tỷ token). Nguyên nhân đã xác nhận là TLS
> fingerprint: cùng body/header, `urllib`/`curl` nhận `429`, Bun nhận `200`. `o2a serve` tự tìm
> Bun ở `PATH`, `~/.bun/bin/bun` và `/usr/local/bin/bun`; kiểm tra bằng `./bin/o2a doctor`.
> Nếu vẫn `429` dù đã dùng Bun, đó là giới hạn IP/phiên free của upstream — thử IP khác hoặc dùng
> lane Go/Zen có key (`/zen/go` yêu cầu API key, không phải chế độ free).

### 3. `UPSTREAM_MODE=openai` — nói chuyện với gateway opencode2api

Dùng khi bạn đã có gateway (ví dụ `jasonxu114514/opencode2api`, `Nystic-Shadow/Opencode2api`)
hoặc gọi thẳng OpenCode inference:

```dotenv
UPSTREAM_MODE=openai
UPSTREAM_BASE_URL=http://127.0.0.1:8081/v1
UPSTREAM_API_KEY=O2P-xxxxxxxx
MODEL_MAP={"gpt-4o":"big-pickle","claude-sonnet-4-5":"glm-5.3-flash"}
```

App forward request tới gateway, đổi alias model sang ID thật, truyền key gateway,
và đổi `model` trong SSE chunk về alias mà client đã gửi.

## Gọi thử

```bash
curl http://localhost:8080/v1/models \
  -H 'Authorization: Bearer my-secret'

curl http://localhost:8080/v1/chat/completions \
  -H 'Authorization: Bearer my-secret' \
  -H 'Content-Type: application/json' \
  -d '{"model":"big-pickle","messages":[{"role":"user","content":"Hello"}]}'

# streaming
curl -N http://localhost:8080/v1/chat/completions \
  -H 'Authorization: Bearer my-secret' \
  -H 'Content-Type: application/json' \
  -d '{"model":"big-pickle","stream":true,"messages":[{"role":"user","content":"Đếm từ 1 đến 5"}]}'
```

Python (OpenAI SDK):

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="my-secret")

out = client.chat.completions.create(
    model="big-pickle",
    messages=[{"role": "user", "content": "Giải thích ngắn gọn quantum superposition"}],
)
print(out.choices[0].message.content)

for chunk in client.chat.completions.create(
    model="big-pickle",
    messages=[{"role": "user", "content": "Đếm từ 1 đến 5"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

## Endpoints

| Endpoint | Hành vi |
| --- | --- |
| `GET /`, `/dashboard` | Web dashboard React (nếu đã build), nếu không có `dist` thì trả JSON thông tin bridge |
| `GET /health`, `/healthz` | Status (không cần key) |
| `GET /v1/models` | Danh sách model OpenAI format |
| `POST /v1/chat/completions` | Chat Completions JSON/SSE |
| `POST /v1/responses` | Responses API (JSON, input string hoặc messages) |

Auth: nếu `CLIENT_API_KEY` khác rỗng, client phải gửi `Authorization: Bearer <key>`
hoặc `x-api-key: <key>`. Để trống để tắt auth (chỉ nên dùng localhost).

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `HOST`, `PORT` | `0.0.0.0`, `8080` | Địa chỉ nghe |
| `CLIENT_API_KEY` | `""` | Key client dùng để gọi bridge |
| `UPSTREAM_MODE` | `opencode` | `opencode`, `opencode_free` hoặc `openai` |
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | Địa chỉ `opencode serve` |
| `OPENCODE_API_KEY` | `""` | Password/key nếu OpenCode server yêu cầu |
| `OPENCODE_PROVIDER_ID` | `opencode` | `providerID` khi tạo model ref |
| `OPENCODE_FREE_BASE_URL` | `https://opencode.ai/inference` | Base URL chế độ `opencode_free` |
| `UPSTREAM_BASE_URL` | `https://opencode.ai/inference/openai/v1` | Base URL gateway chế độ `openai` |
| `UPSTREAM_API_KEY` | `""` | Key gửi tới gateway; tự thành `public` ở chế độ `opencode_free` |
| `UPSTREAM_USER_AGENT` | `opencode/latest/2.3.0/cli` | User-Agent gửi tới upstream (khớp CLI 2.3.0) |
| `DEFAULT_MODEL` | `big-pickle` | Model khi client không gửi `model` |
| `MODEL_MAP` | `{}` | JSON alias client → model ID upstream |
| `APPEND_ASSISTANT_CUE` | `true` | Thêm `Assistant:` cuối prompt ở chế độ `opencode` |
| `STREAM_CHUNK_CHARS` | `12` | Kích thước chunk giả lập streaming |
| `REQUEST_TIMEOUT` | `180` | Timeout upstream (giây) |
| `O2A_BUN` | `bun` | Đường dẫn Bun dùng làm transport cho chế độ `opencode_free` |

## Giới hạn

- Chế độ `opencode` dùng endpoint generate **stateless**: mỗi request là một session mới,
  bridge không lưu lịch sử và **không thực thi tool**; tool_calls/tool results được đưa vào prompt dạng text.
- Không có token usage thật từ OpenCode generate API → `usage` là ước lượng theo độ dài text.
- Streaming ở chế độ `opencode` là chia nhỏ text đã generate xong (SSE chunk chuẩn OpenAI),
  không phải token streaming thật; các chế độ `openai` và `opencode_free` stream passthrough theo thời gian thực.
- Chế độ `opencode_free` cần Bun làm transport để khớp TLS fingerprint của CLI. `o2a serve` tự
  tìm Bun ở `PATH`, `~/.bun/bin/bun`, `/usr/local/bin/bun`; thiếu Bun thì bridge fallback sang
  `urllib` và upstream gần như chắc chắn trả `429 FreeUsageLimitError`.
- `big-pickle` không có quota token kiểu "vài tỷ token"; lỗi 429 là của lane free/upstream,
  thường do sai TLS fingerprint chứ không phải model hết hạn mức.
- Ảnh đầu vào (`image_url`) được chuyển thành `[image: ...]` dạng text ở chế độ `opencode`.
- `/v1/responses` chưa hỗ trợ `stream: true` — dùng `/v1/chat/completions` nếu cần streaming.

## Test

```bash
python3 -m unittest -v
```

Test dùng một fake upstream server, không cần OpenCode thật hay mạng. Các test `FreeModeTests`
kiểm tra prompt/tools/headers và SSE passthrough của chế độ `opencode_free`.

Đã thử live từ Railway sau khi dùng transport Bun: `/health`, `/v1/models`,
`/v1/chat/completions` (JSON và SSE) đều trả `200`; nội dung thật từ `big-pickle` là `OK`.
Trước đó, cùng request gửi bằng `urllib`/`curl` bị `429`, trong khi Bun gửi cùng body/header
trả `200` — xác nhận upstream chặn theo TLS fingerprint, không phải quota hay sai shape.

## Deploy

### 1. Railway source deploy từ GitHub (auto deploy — khuyến nghị)

Repo: https://github.com/Quan3s/opencode2openai

1. Railway Dashboard → **New Project** → **Deploy from GitHub repo** → chọn repo trên
   (cần cài GitHub App cho account/org chứa repo).
2. Railway đọc `railway.json`: builder `DOCKERFILE`, file `Dockerfile`, start command
   `python3 app.py`, healthcheck `GET /health`, listen port `8080`.
3. Tab **Variables** thêm tối thiểu:

   ```dotenv
   UPSTREAM_MODE=opencode_free
   CLIENT_API_KEY=my-secret
   ```

   Thêm nếu dùng gateway/mode khác: `UPSTREAM_BASE_URL`, `UPSTREAM_API_KEY`, `MODEL_MAP`,
   `OPENCODE_BASE_URL`. **Không** đưa key upstream dạng `oc_sk_...` vào repo hay log;
   dùng biến môi trường Railway.
4. Tab **Settings → Networking** → **Generate Domain**. Mở domain để xem dashboard;
   client OpenAI dùng `https://<domain>/v1`.

Mỗi push lên branch mặc định của repo sẽ tự động build & deploy (auto deploy).

Tương đương bằng CLI (sau khi `railway link` tới project):

```bash
railway variables set UPSTREAM_MODE=opencode_free CLIENT_API_KEY=my-secret
railway up                 # deploy source từ thư mục hiện tại
railway domain             # tạo domain public
```

### 2. Docker local / self-host

```bash
docker build -t opencode2openai .
docker run --rm -p 8080:8080 \
  -e UPSTREAM_MODE=opencode_free \
  -e CLIENT_API_KEY=my-secret \
  opencode2openai
```

Image 3 stage: lấy Bun từ `oven/bun:1.4`, build dashboard bằng `node:22-alpine`,
rồi copy cả hai vào `python:3.12-slim`. `O2A_BUN` mặc định `/usr/local/bin/bun`,
dashboard có sẵn tại `/`. Healthcheck: `curl http://localhost:8080/health`.

### 3. Chạy thẳng bằng Python

```bash
cp .env.example .env && python3 app.py --port 8080
# hoặc: ./bin/o2a serve --port 8080
```

Cần Bun trong `PATH` (hoặc `O2A_BUN=...`) nếu dùng `UPSTREAM_MODE=opencode_free`;
dashboard cần `cd dashboard && npm run build` để xuất hiện tại `/`.

> Port Railway phải là `8080`. Start command là `python3 app.py`.
> File `.env` nằm trong `.gitignore`; repo chỉ chứa `.env.example`.

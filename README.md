# kokoro-tts

Kokoro-82M 专用 TTS 服务:官方音色自动同步 + 全参数 REST API(token 鉴权),配合 agent skill 按需调用。纯 CPU,单容器,服务端不落盘。

## 架构

```
┌─ 服务(kokoro-tts 容器)─┐      ┌─ agent 侧 ────────────┐
│ REST API + token 鉴权    │ ←─── │ skill(按需加载)        │
│ 音色同步(官方 HF 仓库)  │      │ tts.py CLI + config   │
└─────────────────────────┘      └───────────────────────┘
```

- **纯 REST**:所有请求合成后音频字节直接随响应体返回,**服务端不落任何音频文件**,没有存储积累问题
- **skill 代替 MCP**:不占常驻上下文,agent 需要配音时才加载;`config.json` 集中管理服务地址 + token
- **官方音色同步**:启动时/随时调用从官方 HF 仓库拉最新音色,纯 numpy 转换(镜像无 torch),热加载

## 快速开始

```bash
cp .env.example .env    # 建议设置 API_TOKEN
docker compose up -d --build
curl http://localhost:8000/health
```

## REST API

鉴权:除 `/health` 外均需 `Authorization: Bearer <API_TOKEN>`(或 `?token=`)。

| 端点 | 说明 |
|---|---|
| `POST /v1/audio/speech` | 合成(JSON body)→ 音频字节直返 |
| `GET /v1/audio/speech?input=...` | URL 参数版 |
| `GET /v1/voices?model=&language=&gender=` | 音色元数据(性别/语言/官方评级/标记/特注) |
| `POST /v1/admin/sync-voices` | 同步官方最新音色并热加载 |
| `GET /health` | 健康检查(无鉴权) |

### 合成参数(`POST /v1/audio/speech`)

| 参数 | 默认 | 说明 |
|---|---|---|
| `model` | `v1.1-zh` | 音色组:`v1.1-zh`(中文 100+英 3)/ `v1.0`(经典 54,含 af_heart) |
| `input` / `phonemes` | 必填其一 | 文本 / 音素串(高级) |
| `voice` | 按语言自动 | 音色 id |
| `blend_voice` + `blend_weight` | 无 / 0.5 | 音色混合(0.7 = 70% blend_voice) |
| `speed` | 1.0 | 语速 0.5~2.0 |
| `volume` | 1.0 | 音量(响度归一化后叠加) |
| `peak_target` | 0.95 | 峰值归一化目标,0 关闭 |
| `sentence_pause` | 0.25 | 句间停顿秒数(0~5) |
| `clause_pause` | 0.1 | 子句间停顿秒数(0~5) |
| `trim` / `continuous` | true / false | 修剪静音 / 连续模式 |
| `language` | 自动检测 | `zh` / `en-us` / ... |
| `response_format` | mp3 | wav / mp3 / pcm / opus / flac |
| `sample_rate` | 24k 原生 | 8000~96000 重采样 |

响应头:`X-TTS-Voice` / `X-TTS-Model` / `X-TTS-Source-Sample-Rate` / `X-TTS-Duration-Sec`。

```bash
curl -s http://localhost:8000/v1/audio/speech \
  -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"voice":"zf_001","input":"千里之行,始于足下。","sentence_pause":0.6,"response_format":"wav"}' \
  -o out.wav
```

## Agent skill(代替 MCP)

`skill/` 目录三件套,**不用 MCP 时不占任何上下文**,agent 需要配音时才加载:

| 文件 | 说明 |
|---|---|
| `SKILL.md` | 触发条件 + 用法说明(agent 按需读取) |
| `tts.py` | 零依赖 CLI(pure stdlib):`pick` / `voices` / `speak` / `sync` / `health` |
| `config.json` | **服务地址 + token 在这里配置** |

安装:把 `skill/` 注册到你的 agent 客户端(如 ZCode 的 skills 目录),然后编辑 `config.json`:

```json
{ "base_url": "http://<host>:8000", "api_token": "<与 .env 的 API_TOKEN 一致>" }
```

agent 工作流(见 SKILL.md):

```bash
# 1. 按场景推荐(agent 描述需求即可)
python tts.py pick --language zh --gender female --scenario audiobook

# 2. 合成,音频保存到 --out 指定的调用方本地路径
python tts.py speak --voice zf_001 --text "千里之行,始于足下。" --out hello.mp3
```

`pick` 返回带理由的候选(官方评级/标记/特注);`voices --min-grade A` 只看 A/B 级;音色元数据含**性别/语言/官方质量评级 A~F/训练时长/🔥❤️ 标记/特注**,性别 157/157 全覆盖。

## 音色同步机制

- 官方源:`hexgrad/Kokoro-82M`(54)+ `hexgrad/Kokoro-82M-v1.1-zh`(103),`HF_ENDPOINT` 默认 hf-mirror
- 官方 `.pt` → 纯 numpy 解析 → 合并进 `data/voices-<组>.bin` + 元数据清单;官方 VOICES.md 的评级/标记一并入库
- 启动同步 + `POST /v1/admin/sync-voices` 两种触发;同步后热加载,无需重启;断网用内置种子包继续服务

## 资源与稳定性

- 实测(笔记本 CPU):RTF ≈ 0.5~0.7,内存恒定 ~0.9GB(8 轮连续调用 +38MB 后走平,无泄漏)
- 内存看门狗:每 30 分钟采样,超基线 300MB 或 MEM_LIMIT 85% 告警
- `MEM_LIMIT` 默认 2g

## 故障排查

- **401**:token 不匹配;skill 的 `config.json` 与服务 `.env` 的 `API_TOKEN` 必须一致
- **同步失败**:检查 `HF_ENDPOINT` 可达性;失败不影响启动(种子包兜底)
- **音色 400**:该音色不在当前组,`GET /v1/voices?model=...` 确认,或触发 sync
- **声音轻/响**:调 `peak_target`(默认 0.95)+ `volume`

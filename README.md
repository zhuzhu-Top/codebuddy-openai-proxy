# CodeBuddy OpenAI Proxy

一个非官方、本机运行的 OpenAI Chat Completions 兼容层。它读取当前 Windows
用户已登录的 CodeBuddy 会话，将 OpenAI SDK 请求转发到该账户可访问的
CodeBuddy 服务。凭据只在本机内存中使用，不会由本项目上传或写入配置文件。

> 本项目与腾讯、CodeBuddy 或 OpenAI 均无隶属或认可关系。仅用于你自己的账户，
> 并请遵守相关服务条款。不要共享登录凭据。

## 兼容性

- 系统：Windows 10/11（自动读取凭据依赖 Windows DPAPI）
- Python：3.10–3.13
- 接口：`GET /v1/models`、`GET /v1/models/{id}`、
  `POST /v1/chat/completions`、`POST /v1/responses`
- 调用方式：OpenAI Python SDK、curl、Codex 及其他兼容客户端
- 未实现：Embeddings、音频和图像接口

## 安装与启动

先安装并登录 CodeBuddy，然后在 PowerShell 中运行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
codebuddy-openai-proxy doctor
codebuddy-openai-proxy serve
```

默认只监听 `http://127.0.0.1:8765`，本地接口不验证 API key。不要将服务绑定到
局域网或公网地址，否则其他人可以使用你的 CodeBuddy 权限。

代理会通过 DPAPI 解密当前用户的 CodeBuddy Secret Storage，不会复制或输出
CodeBuddy access token。CodeBuddy 会话更新后，代理会自动刷新内存缓存。
企业账户通常可直接使用；个别个人账户若无法自动找到设备令牌，可临时设置
`CODEBUDDY_DEVICE_TOKEN` 环境变量。所有手动覆盖项见 `.env.example`。

## OpenAI Python SDK

开发环境安装 SDK：`python -m pip install openai`。

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    # OpenAI SDK 要求该参数非空，但本地代理会忽略它。
    api_key="not-needed",
)

response = client.chat.completions.create(
    model="zhuzhu-gpt-5.6-sol",
    messages=[{"role": "user", "content": "你好"}],
    extra_body={"reasoning_effort": "high"},
)
print(response.choices[0].message.content)
```

流式调用：

```python
stream = client.chat.completions.create(
    model="zhuzhu-gpt-5.6-sol",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
    extra_body={"reasoning_effort": "none"},
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

`reasoning_effort` 支持 `none`、`minimal`、`low`、`medium`、`high`、
`xhigh` 和 `max`。实际支持程度、思考内容是否返回及 token 统计取决于上游模型。
Chat Completions 支持非流式/流式、usage、JSON mode、tools，以及 OpenAI 的命名
`tool_choice`；代理会将后者转换为 CodeBuddy 可接受且语义等价的格式。

已验证的请求能力包括多轮消息、system/tool 角色、工具结果回传、`temperature`、
`top_p`、penalty、`seed`、`max_tokens`、`max_completion_tokens`、
`stream_options.include_usage` 和 `response_format={"type":"json_object"}`。

CodeBuddy 会静默忽略 `n > 1`、`stop` 和 `logprobs`，也不能保证严格
`json_schema`。代理会对这些组合明确返回 OpenAI 格式的 400，避免客户端误以为
约束已经生效。旧版 `functions`/`function_call` 不保证兼容，请使用 `tools`。

## curl

```powershell
curl.exe http://127.0.0.1:8765/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{\"model\":\"zhuzhu-gpt-5.6-sol\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"stream\":false,\"reasoning_effort\":\"none\"}'
```

健康检查无需认证：`GET /health`。模型列表：`GET /v1/models`。代理会合并当前
CodeBuddy 账户的远程模型配置与本机企业自定义模型缓存，并缓存 8 分钟；动态发现
失败时才回退到 `CODEBUDDY_MODELS`。运行 `codebuddy-openai-proxy models` 可查看
同一列表。

对外模型名统一使用 `zhuzhu-` 前缀，避免与 Cursor 内置模型重名。在 Cursor 的
“Add Custom Model”中填写 `zhuzhu-gpt-5.6-sol`；Cursor 内部自动形成的
`custom:zhuzhu-gpt-5.6-sol` 也会被代理正确识别。

## Codex

新版 Codex 仅使用 Responses API。代理会把 `/v1/responses` 请求转换成
CodeBuddy Chat Completions，并把文本、SSE 和函数调用结果转换回 Responses
格式。

### 1. 启动代理

保持以下命令运行：

```powershell
codebuddy-openai-proxy serve
```

另开一个 PowerShell 确认服务正常：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

应返回 `status: ok`。

### 2. 配置 Codex

编辑 `%USERPROFILE%\.codex\config.toml`，设置默认模型和本地 provider：

```toml
model = "gpt-5.6-sol"
model_provider = "zhuzhu-local"
model_reasoning_effort = "high"

[model_providers.zhuzhu-local]
name = "CodeBuddy Local Proxy"
base_url = "http://127.0.0.1:8765/v1"
wire_api = "responses"
supports_websockets = false
```

`api_key` 无需配置。代理同时接受 `gpt-5.6-sol` 和
`zhuzhu-gpt-5.6-sol`，并解析为当前 CodeBuddy 账户中对应的真实模型。

配置后完全退出并重新打开 Codex。Codex 修改模型或推理等级时可能重写文件顶部的
`model` 和 `model_reasoning_effort`，但应保留 `model_provider =
"zhuzhu-local"`。

### 3. Microsoft Store 版

Microsoft Store 中的应用进程名可能显示为 `ChatGPT.exe`，其中的 Codex 工作区
仍读取同一个 `%USERPROFILE%\.codex\config.toml`。

商店版模型菜单只显示 OpenAI 内置标签，不能显示自定义 provider 名称。因此菜单
中的 “5.6 Sol”不会显示成 “CodeBuddy Local Proxy”；真正决定请求去向的是
`model_provider = "zhuzhu-local"`。选择 “5.6 Sol”后，请检查该设置没有被改回
其他 provider。

### 4. 验证请求经过本地代理

在 Codex 中发送一条消息。运行代理的窗口应出现类似日志：

```text
GET /v1/models?client_version=... 200 OK
POST /v1/responses 200 OK
```

看到 `POST /v1/responses` 即可确认 Codex 调用了
`http://127.0.0.1:8765/v1`，而不是直接请求 OpenAI。

也可以用 Codex CLI 验证：

```powershell
codex exec --skip-git-repo-check "Reply with exactly CODEX_LOCAL_OK"
```

已验证 Codex 0.162 的文本响应、Responses SSE、`exec_command` 工具调用及工具
结果回传。CodeBuddy 不接受 reasoning 与 tools 同时显式传递；代理会在工具请求
中省略显式 reasoning 参数，让模型使用默认推理设置。

### Codex 故障排查

- 返回 `404 model_not_found`：更新并重启代理；Codex 可能发送裸模型名
  `gpt-5.6-sol`，新版本代理会自动解析。
- 提示 `/v1/responses` 未实现：当前运行的是旧代理进程，停止占用 8765 端口的
  旧进程后重新执行 `codebuddy-openai-proxy serve`。
- 模型菜单没有自定义名称：这是商店版 UI 的限制；以代理窗口是否收到
  `POST /v1/responses` 为准。
- 请求出现 11128：通常是 Codex 内置的大段提示被上游拦截；当前转换层会过滤已知
  Codex 样板元数据，请更新到最新版。
- 请求出现 11150：CodeBuddy 不接受 reasoning 与 tools 同时显式传递；当前版本
  已自动兼容。

## 配置

所有配置均可用环境变量或当前目录的 `.env` 覆盖。默认限制为 10 MiB 请求体、
8 个并发请求、600 秒读取超时。服务没有本地鉴权并默认绑定回环地址；不要绑定
公网或局域网地址。

## 故障排查

- `doctor` 显示未认证：确认 CodeBuddy 已安装并已登录，然后重启 CodeBuddy。
- 提示缺少 `X-Device-Token`：个人账户可从本人一次成功请求中取得该值，并仅在
  本机设置 `CODEBUDDY_DEVICE_TOKEN`；不要提交或分享。
- 401/403：CodeBuddy 会话可能已过期，重新登录后重试。
- 模型调用失败：重新登录 CodeBuddy 后，用 `codebuddy-openai-proxy models`
  刷新并核对模型 ID。

安全问题请参阅 `SECURITY.md`，贡献流程参阅 `CONTRIBUTING.md`。

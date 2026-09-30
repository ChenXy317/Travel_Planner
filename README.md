# 旅行规划

本地运行的中文旅行规划助手。说出城市、天数、人数、偏好和预算后，模型负责决定下一步，坐标、天气、地点、搜索摘要、路程和金额只来自工具。你确认后，计划文档按已保存的行程填写，数字不会再经过模型重写。

不登录，也不订机票、火车票、酒店或门票。适合在自己的机器上把一次短途旅行排清楚。

可以先发这句：

> 下周五起，两个人去青岛玩三天，想看海和博物馆，酒店一晚 400，预算 3000。排好之后给我一份计划文档。

## 界面

打开 http://127.0.0.1:5174 。左边是对话和工具卡，右边是按天展开的行程。页眉「模型」可以临时更换对话接口。

一次规划常常要一两分钟。工具会逐张回来，页面会一直等着，不用反复发送。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js。

```bash
cp .env.example backend/.env
# 编辑 backend/.env，填入对话模型和搜索密钥
./start.sh
```

`start.sh` 会安装 Python 3.12 依赖和前端依赖，在 `127.0.0.1:8001` 启动 API，再在 `127.0.0.1:5174` 启动页面。脚本退出时会关掉 API。

请只开这一个 API 进程。限速和当前会话状态都在进程里面，多进程会拆开。

## 配置

密钥只放在 `backend/.env`，不要提交。

| 变量 | 作用 |
| --- | --- |
| `LLM_BASE_URL` | OpenAI 兼容接口的地址 |
| `LLM_MODEL` | 模型名 |
| `LLM_API_KEY` | 对话密钥 |
| `SEARCH_API_KEY` | Brave Search 的 `X-Subscription-Token` |

对话接口试用过 Ollama Cloud：`https://ollama.com/v1`，模型 `gemma4:31b-cloud`。三项都留空时，地址回退到本机 `http://127.0.0.1:11434/v1`。

页眉里的修改只留在本次运行的内存中。密钥不会从接口返回；不填表示保持原值。重启后回到 `backend/.env`。页面上填写的地址只接受公网或本机回环。

`SEARCH_API_KEY` 留空时，搜索直接失败，不会发出网络请求。搜索优先使用 Brave LLM Context；账号没有该接口时，自动改用 Web Search。返回给模型的都是标题、链接和片段。

## 一次规划怎么走

模型通过 LangChain `create_agent` 与下面的工具交替进行。工具之间不会互相调用。

| 工具 | 做什么 |
| --- | --- |
| `geocode` | 把地名解析成坐标 |
| `get_weather` | 查询已解析坐标的每日天气 |
| `search_places` | 在周围查找博物馆、公园、海滩、景点或观景点 |
| `web_search` | 检索开放时间、门票和闭馆信息 |
| `estimate_route` | 估算步行或驾车路程 |
| `estimate_budget` | 按单价乘数量汇总已知费用 |
| `save_itinerary` | 在你明确同意后保存行程 |
| `write_plan_document` | 按已保存的行程写出计划文档 |
| `list_itineraries` | 列出最近保存的行程 |

计划文档固定六节：行程概要、每日安排、交通、费用、信息来源、说明。保存后可从页面或 `GET /api/v1/itineraries/{id}/document` 下载 Markdown。

对话、缓存和行程存在本地 SQLite。外部结果会按各自的有效期缓存；缓存命中不再请求外网。

## 限制

- 最多两座城市、5 天、每天 3 个站点。
- 天气预报只接受 1 到 5 天，并且要落在未来 16 天内。
- 一次对话最多搜索 4 次。
- 开放时间和价格只抄搜索摘要。摘要里没有的价格保持未知，不用城市均价填上。
- 金额用整数分。带链接的价格必须对得上这次会话里出现过的搜索链接，对不上则整次预算失败。
- 路程来自 OSRM 演示服务，不保证可用。失败会写在工具卡上，不会用直线距离代替。

## 数据来源

天气来自 [Open-Meteo](https://open-meteo.com/)，使用时需署名，许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。地点来自 OpenStreetMap 贡献者，经由 [Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) 查询。路程来自 [OSRM](https://project-osrm.org/) 演示服务。搜索由 [Brave Search](https://brave.com/search/api/) 提供。

## 开发

后端测试使用录好的响应和假模型，不访问公网。

```bash
cd backend
uv run pytest
```

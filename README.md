# 墙面霉菌 UV 双光谱分级检测

用户拍两张图（白光 + 365nm UV），后端给出 L1–L5 分级、受影响区域与处置建议。

**核心设计：大模型不做测量，只做判读。** 报告上的每一个数字都来自 OpenCV，
因此可复现、可审计、模型不可用时仍能出结论；大模型负责分级、审核候选区域、
推断成因。两者结论冲突时显式呈现，不悄悄取平均。

设计说明书：https://claude.ai/code/artifact/78c68cfe-6654-4685-a967-93586021e61d

---

## 快速开始

```bash
./setup.sh          # 创建 .venv、装依赖、生成 .env
vi .env             # 改 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
./run.sh            # 启动，顺带打印内网地址方便手机访问
```

需要 Python 3.10+。系统里有多个版本时用 `PYTHON=python3.12 ./setup.sh` 指定。
不想用脚本就手动来：

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://<服务器IP>:8000`。

### 手机拍摄与 HTTPS

浏览器只在**安全上下文**（HTTPS 或 localhost）下开放 `getUserMedia`。手机连内网 IP
走的是 HTTP，所以实时取景与对齐描边用不了 —— 页面会说明原因并给出「用相机拍摄」
（唤起系统相机，HTTP 下照常可用）和「从相册选择」，检测流程完整，只是少了对齐辅助。

要拿到完整体验（实时取景 + 洋葱皮对齐）：

```bash
./run.sh --https                     # 首次会自动生成自签名证书
scripts/make_cert.sh 192.168.1.23    # 换了 Wi-Fi / IP 变了就重新生成
```

自签名证书手机首次访问会提示「不安全」，点继续访问即可。证书 SAN 里会写入当前内网 IP。

Docker：

```bash
docker compose up -d --build
```

模型跑在宿主机时，`.env` 里用 `http://host.docker.internal:8100/v1`。
`docker-compose.yml` 里附了一段 vLLM 服务的注释配置可直接启用。

---

## 配置（.env）

| 变量 | 说明 |
|---|---|
| `LLM_BASE_URL` | OpenAI 兼容端点，如 `http://127.0.0.1:8100/v1` |
| `LLM_API_KEY` | 私有化部署通常填任意占位值 |
| `LLM_MODEL` | 默认 `qwen3.6-35b-a3b`，须与端点实际提供的模型名一致 |
| `LLM_JSON_MODE` | 端点不支持 `response_format` 时会自动降级，无需手动关 |
| `REFERENCE_MODE` | `anchor`（默认，注入 L1/L3/L5 三张 UV 参考图）/ `none` / `full` |
| `GRADING_STRATEGY` | `llm_led`（默认，大模型主导）/ `cv_led`（阈值主导） |
| `ENABLE_CALIBRATION_CARD` | 开启标定卡检测 |
| `RETENTION_DAYS` | 图像与记录保留天数，默认 30 |
| `STORAGE_DIR` | 必须是**本地磁盘**目录。SQLite 在 NFS / SMB / 共享挂载上拿不到文件锁，会报 `disk I/O error`（服务启动时会给出明确提示） |

分级阈值与文字判据在 `config/grading.yaml`，**改文件即生效，无需重启**。

---

## 接口

```
POST   /api/v1/analyze                  白光图 + UV 图 -> 202 {task_id}
GET    /api/v1/tasks/{id}/stream        SSE 分段进度，CV 结果约 2 秒先到
GET    /api/v1/tasks/{id}               最终结果 JSON
GET    /api/v1/tasks/{id}/assets/{f}    white.jpg / uv.jpg / overlay.jpg / mask.png
DELETE /api/v1/tasks/{id}               用户删除记录
GET    /api/v1/config                   前端启动配置
GET    /api/v1/health                   含大模型连通性
```

分享结果：`http://<host>/?task=<task_id>`（无账号体系，凭链接访问）。

---

## 流水线

```
预检 -> 配准 -> 分割量化 -> 大模型判读 -> 仲裁 -> 报告
```

1. **预检** 分辨率、模糊度（Laplacian 方差）、过曝、UV 图环境光污染。不合格
   直接返回重拍提示，不浪费一次模型调用。
2. **配准** ORB + RANSAC，失败回退 ECC；再失败则标记未配准并降级差异指标。
   前端的洋葱皮取景已把偏差压得很小，这里只是兜底。
3. **分割量化** Lab-L 平场校正 + 清洁墙面百分位阈值。产出掩膜、轮廓、覆盖率、
   连片度、斑块数。有标定卡时额外做亮度归一与绝对面积换算。
4. **判读** 白光图 + UV 图 + **带编号的掩膜叠加图** 一并发给模型，任务是
   「审这些编号区域」而不是「找霉斑」——后者会让坐标漂移，前者不会。
5. **仲裁** CV 与模型分级一致则加分；差 1 级取高者；差 ≥2 级标记需人工复核。
   缺 UV 图时置信度封顶 0.6，未配准封顶 0.75。
6. **报告** 原图、掩膜、模型原始响应、算法与阈值版本号全部落库。

---

## 三个能力边界（写进产品，不要越界）

- **区域**：能标，但由 OpenCV 出像素级掩膜与轮廓，不让大模型输出坐标。
- **菌种**：不能。只给形态学倾向 + 强制取样提示。`morphology_hint.confidence`
  只允许 `low` / `medium`，prompt 里硬性禁止输出属种名称。
- **数量**：仅 L1–L2 有物理意义。L3 起菌落融合成片，后端置
  `count_is_meaningful=false`，前端不显示计数 —— 显示「122 个菌落」会让人
  误以为可以逐个清除。

---

## 标定卡

```bash
python3 scripts/make_calibration_card.py card.png
```

生成 85.6×54mm（银行卡尺寸）300dpi 印刷稿：四角 ArUco 标记 + 白/灰/荧光/黑四色块。
贴墙上一起拍进去，一次解决三件事：绝对面积（cm²）、UV 亮度归一、白平衡。

**印刷要求**：哑光纸；关闭打印机色彩增强；荧光块须用荧光墨或荧光标签纸
（普通油墨在 365nm 下不发光）；印完量一下实际宽度，不是 85.6mm 就改
`CALIB_CARD_WIDTH_MM`。

没有标定卡时系统照常工作，但 UV 覆盖率会标记为 `estimated`，且不给绝对面积。

## UV 光源

必须 **365nm + ZWB2/UG-1 滤光片**。市面便宜的 395nm「紫光手电」可见紫光泄漏严重，
会把整面墙照成紫色、淹没微弱荧光。这是最影响成像质量的单一因素。

---

## 测试

```bash
./.venv/bin/python scripts/ladder.py       # 10 张参考图跑分割，校验覆盖率单调 + 阈值判级
./.venv/bin/python scripts/smoke_test.py   # 端到端 33 项：上传/SSE/判读/误检剔除/降级/删除
./.venv/bin/python scripts/mock_llm.py     # 桩模型，手动联调时另开一个终端跑
```

`smoke_test.py` 会在自己进程里起桩模型，不需要先手动启动。

`ladder.py` 是改动 `app/cv/segment.py` 任何参数后的必跑项 ——
白光覆盖率必须保持单调递增，否则阈值判级会失效。当前基线：

| | L1 | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|
| 白光覆盖率 | 2.2% | 3.8% | 14.7% | 51.0% | 73.7% |
| UV 覆盖率 | 9.0% | 19.6% | 53.6% | 55.0% | 78.2% |
| 隐性扩散倍数 | 4.1× | 5.2× | 3.6× | 1.1× | 1.1× |

---

## 目录

```
setup.sh           创建虚拟环境、装依赖、生成 .env
run.sh             启动服务（--https 走 TLS）
scripts/make_cert.sh  生成自签名证书
app/
  config.py        .env 与 grading.yaml 热加载
  main.py          FastAPI 路由与 SSE
  pipeline.py      六段流水线编排
  arbitrate.py     CV 与大模型的分级仲裁
  schemas.py       API 契约与大模型输出强校验
  store.py         SQLite + 文件存储、保留期清理
  cv/              intake / registration / calib / segment / render
  llm/             client（OpenAI 兼容）/ prompt
web/               单页前端，无框架无外部依赖
config/grading.yaml  分级标准（热加载）
reference/         L1–L5 参考图，用于 prompt 锚定与回归测试
```

---

## 免责声明（须在产品中呈现）

本工具提供筛查性参考，不构成室内环境检测报告、健康或医疗建议、菌种鉴定结论。
严重霉污（L4/L5）应由具备资质的机构处理。

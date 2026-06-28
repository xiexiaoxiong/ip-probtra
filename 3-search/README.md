# 项目结构说明

# 本地运行
## 运行流程
bash scripts/local_run.sh -m flow

## 运行节点
bash scripts/local_run.sh -m node -n node_name

# 启动HTTP服务
bash scripts/http_run.sh -m http -p 5000

## 商品页抓取原型

当前仓库已新增一个独立的商品页抓取原型，不接入现有模块3主流程，方便在测试分支上单独验证。

### 能力说明

- 使用 `Playwright` 打开商品页，尽量模拟真实用户的等待与滚动
- 页面稳定后分别提取可见文字块和图片候选区域
- 自动输出首屏截图、整页截图、图片裁剪图
- 若本机安装了 `tesseract`，可对裁剪后的图片区块做 OCR 兜底
- 支持人工登录/人工验证模式，并复用本地浏览器登录态
- 支持用本地接入的智谱多模态模型筛选“真正的商品详情图”
- 支持先对整页截图做多模态区域定位，再按坐标拆分成多个详情小图
- 最终生成结构化结果文件 `capture-result.json`

### 快速测试

1. 安装依赖并确保 `Playwright Chromium` 可用

```bash
cd /Users/xiexiaoxiong/Documents/patent/3-search
uv sync -p python3.12
uv run playwright install chromium
```

2. 运行本地测试页

```bash
cd /Users/xiexiaoxiong/Documents/patent/3-search
uv run python src/tools/product_page_capture.py \
  --url "file:///Users/xiexiaoxiong/Documents/patent/3-search/src/static/product_capture_fixture.html" \
  --output-dir ./tmp/product-capture-fixture \
  --no-ocr
```

3. 运行真实商品页

```bash
cd /Users/xiexiaoxiong/Documents/patent/3-search
uv run python3 src/tools/product_page_capture.py \
  --url "https://example.com/product-page" \
  --output-dir ./tmp/product-capture-real \
  --ocr \
  --wait-selector ".product-title"
```

4. 运行需要人工登录/验证的页面

```bash
cd /Users/xiexiaoxiong/Documents/patent/3-search
uv run python3 src/tools/product_page_capture.py \
  --url "https://detail.1688.com/offer/930711203351.html" \
  --output-dir ./tmp/product-capture-1688 \
  --manual-login \
  --user-data-dir ./tmp/browser-profiles/1688 \
  --detail-image-candidate-limit 16 \
  --detail-image-max-results 8 \
  --ocr
```

首次运行时会打开有界面浏览器，并在终端提示你完成登录、滑块或验证码。完成后按一次 Enter，脚本继续抓取；后续只要复用同一个 `--user-data-dir`，就会尽量沿用之前的登录态。

脚本会先生成一批图片区块候选，再调用本地已接入的智谱兼容多模态模型判断哪些候选图更像“商品详情介绍图”。若本地没有配置模型环境变量，脚本会自动回退到启发式规则，不会中断抓取。

当前默认优先级是：

1. 先对整页截图调用多模态，识别多个“商品详情图区域”
2. 将整页按模型返回的坐标裁成多个小图
3. 若整页区域定位失败，再退回候选图片区块筛选模式

### 输出内容

- `screenshots/viewport.png`: 首屏截图
- `screenshots/full-page.png`: 整页截图
- `screenshots/image-*.png`: 从整页截图中裁出的重点图片区块
- `screenshots/detail_image_crops`: 经过多模态筛选后保留下来的商品详情图
- `text/visible-text.txt`: 归并后的可见文字
- `capture-result.json`: 页面元信息、加载状态、文字块、图片块与 OCR 结果
- `capture-result.json.manual_login`: 人工登录是否启用、是否实际触发、命中的登录/验证信号
- `capture-result.json.image_candidates`: 所有图片区块候选
- `capture-result.json.images`: 最终筛选出的商品详情图
- `capture-result.json.detail_image_selection`: 多模态是否启用、候选数量、筛选错误等元信息
- `capture-result.json.fullpage_region_detection`: 整页截图区域识别是否成功、返回了多少区域

### 测试命令

```bash
cd /Users/xiexiaoxiong/Documents/patent/3-search
uv run pytest tests/test_product_page_capture.py
```

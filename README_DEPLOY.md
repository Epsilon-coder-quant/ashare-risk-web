# A股风控助手 GitHub + Render 部署

这是可部署到 Render 的云端 Web 版本，不包含本机 Excel、券商导出、缓存或账户数据。

## 部署流程

1. 新建 GitHub 仓库，例如 `ashare-risk-web`。
2. 将本目录推送到 GitHub。
3. 在 Render 选择 `New` -> `Blueprint`，连接该 GitHub 仓库。
4. Render 会读取 `render.yaml`，创建 Web Service。
5. 服务启动后访问 Render 给出的 `*.onrender.com` 地址。

## 自定义 .com 域名

1. 注册你选择的 `.com` 域名，例如 `ashare-risk.com`。
2. 在 Render 服务页面打开 `Settings` -> `Custom Domains`。
3. 添加 `www.你的域名.com` 或根域名。
4. 按 Render 页面给出的 DNS 记录，在域名注册商处添加 `CNAME` 或 `A/ALIAS` 记录。
5. 等 DNS 生效后，Render 会自动签发 HTTPS 证书。

## 环境变量

`render.yaml` 已设置：

- `ASHARE_RISK_USE_MOCK=0`
- `ASHARE_RISK_MOCK_ON_FAILURE=0`
- `ASHARE_RISK_FAST_START=1`
- `ASHARE_REALTIME_REFRESH_SECONDS=1`
- `ASHARE_MARKET_SNAPSHOT_MAX_AGE_SECONDS=1800`

如后续接入 Tushare 或 DeepSeek，可在 Render 的 Environment 中添加：

- `TUSHARE_TOKEN`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL`

## 说明

云端版本无法读取你本机东方财富客户端和本地 Excel；它只使用公开行情接口和你在页面中手动输入的数据。私有券商数据仍建议留在本机桌面版。

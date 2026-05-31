# 中国股票风控系统（Streamlit 版）

这是一个用于分析中国股票市场风险、板块风险、个股风险和仓位风险的风控系统。它不预测涨跌，也不输出确定性的“买入/卖出”建议，只输出风险提示和仓位建议。

## 文件结构

```text
ashare_streamlit_risk_system/
├── requirements.txt
├── app.py
├── data_loader.py
├── risk_model.py
├── visualization.py
├── config.py
└── README.md
```

## 安装

建议使用 Python 3.10+。

```bash
cd ashare_streamlit_risk_system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
streamlit run app.py
```

打开浏览器中的本地地址，例如：

```text
http://localhost:8501
```

## 数据源

默认使用真实联网数据，不自动降级到 mock：

- 全市场快照：后台任务优先使用 `AKShare stock_zh_a_spot`，已避开当前网络下不稳定的 Eastmoney push2 高速端点。
- 个股/ETF实时：腾讯行情接口兜底。
- 指数/个股/ETF历史K线：AKShare 优先，腾讯K线和东方财富K线兜底。
- ETF流向：后台每秒读取 ETF 二级市场成交额增量，结合价格方向估算流入/流出代理。

如果 AKShare 字段名变化，兼容逻辑集中在 `data_loader.py` 的 `normalize_spot_df()` 和 `normalize_hist_df()`。

## 缓存机制

所有外部数据都有本地 pickle 缓存，默认目录：

```text
.cache/
```

缓存时间在 `config.py` 的 `CACHE_TTL_SECONDS` 中配置。

在页面侧边栏点击“清空本地缓存”可以删除缓存。

## Mock Data 模式

默认不会自动降级到 mock data。如果实时接口临时失败，页面会使用最近一次真实缓存并显示更新时间；如果没有真实缓存，会提示等待实时数据，而不会用模拟数据冒充行情。

如需调试演示，可手动开启 mock：

```bash
export ASHARE_RISK_ALLOW_MOCK_TOGGLE=1
export ASHARE_RISK_USE_MOCK=1
streamlit run app.py
```

## Tushare Token 配置

第一版不强制依赖 Tushare，但预留了配置入口。

方式一：环境变量

```bash
export TUSHARE_TOKEN="你的token"
```

方式二：项目目录创建 `.env`

```text
TUSHARE_TOKEN=你的token
```

方式三：页面侧边栏输入 Token。该方式仅在当前 Streamlit 会话中使用。

## 模型说明

### Market Risk Score

市场风险分越高代表市场越不适合重仓，包含：

- 指数趋势风险：跌破20日/60日均线加分
- 市场广度风险：上涨家数占比越低，风险越高
- 跌停风险：跌停家数越多，风险越高
- 成交额风险：成交额低于20日均值，风险升高
- 波动率风险：近20日年化波动率越高，风险越高
- 最大回撤风险：近20日最大回撤越大，风险越高

映射仓位：

- 0-20：市场强势，建议总仓位 80%-100%
- 20-40：正常偏强，建议总仓位 60%-80%
- 40-60：震荡，建议总仓位 30%-60%
- 60-80：高风险，建议总仓位 10%-30%
- 80-100：系统性风险，建议总仓位 0%-10%

### Stock Risk Score

个股风险分包括：

- 波动率风险 25%
- 趋势破坏风险 25%
- 流动性风险 15%
- 最大回撤风险 15%
- 成交量异常风险 10%
- 特殊风险 10%，例如 ST、退市风险、接近跌停、连续大跌

### 仓位管理

基础仓位：

```text
账户总资金 × 单笔最大允许亏损比例 ÷ 止损幅度
```

然后根据市场风险分和个股风险分打折。若个股风险分超过80，最终仓位为0；若市场风险分超过80，禁止新开仓。

## 注意

本系统是风控和复盘工具，不构成投资建议。实际交易前仍需要结合账户约束、流动性、公告、停复牌、交易制度和自身风险承受能力复核。

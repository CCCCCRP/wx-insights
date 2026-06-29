# 登录与凭证（爬虫前置）

> crawl 依赖有效微信公众平台 **token + cookie**，本身不执行登录。

## 流程概览

```
python -m worker login --email
    → scan/window.py 判断发信时间窗
    → mail/mailer.py 发送二维码邮件
    → scan/client.py HTTP 轮询扫码
    → auth_manager 持久化到 WECHAT_API_DIR/data/.credentials.json
    → worker/data/token_state.json 记录元数据
```

## crawl 如何使用凭证

```python
# service.py _get_creds
from utils.auth_manager import auth_manager
creds = auth_manager.get_credentials()  # {token, cookie, ...}
```

用于：
- `ArticleFetcher` — 列表 API
- `BizSearcher` — 搜 fakeid
- `ArticlePageFetcher` — token 正文回退

## 相关环境变量

见 `worker/config.py`：`LOGIN_WINDOW_*`, `SMTP_*`, `NOTIFY_EMAIL`, `LOGIN_PUBLIC_URL`

## 故障排查

| 症状 | 检查 |
|------|------|
| crawl 一直等 token | `python -m worker status` |
| 列表 空/报错 | token 过期 → 重新 login |
| 正文全 token 失败 | cookie 是否完整 |

## 延伸阅读

- 扫码实现：`worker/scan/client.py`
- Token 封装：`worker/auth/token_manager.py`

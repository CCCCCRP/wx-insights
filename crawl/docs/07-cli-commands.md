# 爬虫 CLI 命令

## crawl

```bash
python -m worker crawl [选项]
```

| 选项 | 说明 |
|------|------|
| `--week last` | 上一自然周（默认） |
| `--week 2026-W25` | 指定 ISO 周 |
| `--no-wait` | 无 token 不等待 |
| `--wait-timeout N` | 等待 token 秒数 |
| `--no-content` | 不抓正文 |
| `--force-content` | 强制重抓正文 |

## 前置命令

```bash
python -m worker login --email      # 扫码登录
python -m worker status             # 查看 token
python -m worker clear-token        # 清空凭证
python -m worker accounts resolve   # 补 fakeid
```

## 输出示例

```
采集周期 2026-W25: 2026-06-16 ~ 2026-06-22
  返朴: 5 篇（入库 5，正文 5，本地跳过 2）
完成。共入库 42 篇，正文 40 篇（公开 30，token 8，本地跳过 2，txt 回填库 1），失败 2 篇
归档目录: worker/data/archive/2026-W25
```

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 无 token / 采集取消 |

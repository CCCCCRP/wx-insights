"""
wxspirder worker — 独立子项目

    worker/
    ├── config/
    │   └── accounts.yaml    # 公众号列表
    ├── config.py            # 环境变量与路径
    ├── cli.py               # 命令行入口
    ├── data/
    │   ├── archive/         # 按周 txt 归档
    │   └── token_state.json
    ├── auth/                # token 管理
    ├── mail/                # 邮件通知
    ├── scan/                # 扫码登录
    └── crawl/               # 按周采集
"""

__all__ = []

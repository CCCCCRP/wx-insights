from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from worker.config import API_DIR, DATA_DIR

STATE_FILE = DATA_DIR / "token_state.json"
TZ_CN = timezone(timedelta(hours=8))


class TokenManager:
    """统一管理微信凭证（.env / credentials.json）与 worker 侧 token 元数据。"""

    def _auth_manager(self):
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))
        from utils.auth_manager import auth_manager  # noqa: E402

        return auth_manager

    # ── worker 元数据（token_state.json）──────────────────

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(TZ_CN).isoformat(timespec="seconds")

    def load_meta(self) -> Dict[str, Any]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not STATE_FILE.exists():
            return {}
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))

    def save_meta(self, state: Dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def mark_pending(self) -> None:
        state = self.load_meta()
        state.update({
            "last_login_attempt_at": self._now_iso(),
            "last_login_status": "pending",
        })
        self.save_meta(state)

    def mark_success(self, creds: dict) -> None:
        expire_ms = creds["expire_time"]
        state = self.load_meta()
        state.update({
            "token_refreshed_at": self._now_iso(),
            "token_expires_at": datetime.fromtimestamp(
                expire_ms / 1000, TZ_CN
            ).isoformat(timespec="seconds"),
            "token_expires_at_ms": expire_ms,
            "last_login_status": "success",
            "login_nickname": creds.get("nickname", ""),
            "login_fakeid": creds.get("fakeid", ""),
        })
        self.save_meta(state)

    def mark_failed(self, reason: str = "") -> None:
        state = self.load_meta()
        state.update({
            "last_login_status": "failed",
            "last_login_error": reason,
        })
        self.save_meta(state)

    def token_refreshed_at(self) -> Optional[str]:
        return self.load_meta().get("token_refreshed_at")

    # ── 微信凭证（auth_manager）────────────────────────────

    def save_credentials(self, creds: dict) -> bool:
        return self._auth_manager().save_credentials(
            token=creds["token"],
            cookie=creds["cookie"],
            fakeid=creds.get("fakeid", ""),
            nickname=creds.get("nickname", ""),
            expire_time=creds["expire_time"],
        )

    def clear(self) -> None:
        self._auth_manager().clear_credentials()
        self.mark_failed("manual clear")

    def auth_status(self) -> dict:
        return self._auth_manager().get_status()

    def is_logged_in(self) -> bool:
        st = self.auth_status()
        return bool(st.get("loggedIn")) and not st.get("isExpired", False)

    def status_report(self) -> str:
        lines = ["=== auth_manager ==="]
        for k, v in self.auth_status().items():
            lines.append(f"  {k}: {v}")
        lines.append("=== worker token_meta ===")
        for k, v in self.load_meta().items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


token_manager = TokenManager()

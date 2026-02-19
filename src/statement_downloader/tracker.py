import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import DOWNLOAD_LOG_PATH, STATEMENTS_DIR


def _empty_log() -> dict:
    return {
        "version": 1,
        "lastUpdated": None,
        "brokerages": {},
    }


class DownloadTracker:
    def __init__(self, log_path: Path = DOWNLOAD_LOG_PATH):
        self.log_path = log_path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.log_path.exists():
            return _empty_log()
        try:
            with open(self.log_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return _empty_log()

    def _save(self) -> None:
        self.data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp file in same directory, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=self.log_path.parent, suffix=".tmp"
        )
        try:
            with open(fd, "w") as f:
                json.dump(self.data, f, indent=2)
            Path(tmp_path).replace(self.log_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _ensure_brokerage(self, brokerage_slug: str, display_name: str, folder_name: str) -> dict:
        if brokerage_slug not in self.data["brokerages"]:
            self.data["brokerages"][brokerage_slug] = {
                "displayName": display_name,
                "folderName": folder_name,
                "accounts": {},
            }
        return self.data["brokerages"][brokerage_slug]

    def _ensure_account(
        self,
        brokerage_slug: str,
        display_name: str,
        folder_name: str,
        account_label: str,
        account_type: str,
        account_last4: str,
    ) -> dict:
        brokerage = self._ensure_brokerage(brokerage_slug, display_name, folder_name)
        if account_label not in brokerage["accounts"]:
            brokerage["accounts"][account_label] = {
                "accountLabel": account_label,
                "accountType": account_type,
                "accountNumberLast4": account_last4,
                "statements": [],
            }
        return brokerage["accounts"][account_label]

    def is_downloaded(
        self, brokerage_slug: str, account_label: str, statement_date: str
    ) -> bool:
        brokerage = self.data["brokerages"].get(brokerage_slug)
        if not brokerage:
            return False
        account = brokerage.get("accounts", {}).get(account_label)
        if not account:
            return False
        return any(
            s["statementDate"] == statement_date for s in account["statements"]
        )

    def get_downloaded_dates(
        self, brokerage_slug: str, account_label: str
    ) -> set[str]:
        brokerage = self.data["brokerages"].get(brokerage_slug)
        if not brokerage:
            return set()
        account = brokerage.get("accounts", {}).get(account_label)
        if not account:
            return set()
        return {s["statementDate"] for s in account["statements"]}

    def record_download(
        self,
        brokerage_slug: str,
        display_name: str,
        folder_name: str,
        account_label: str,
        account_type: str,
        account_last4: str,
        statement_date: str,
        filename: str,
        file_path: Path,
        downloaded_by: str = "playwright",
    ) -> None:
        account = self._ensure_account(
            brokerage_slug, display_name, folder_name,
            account_label, account_type, account_last4,
        )

        file_size = file_path.stat().st_size if file_path.exists() else 0
        sha256 = _compute_sha256(file_path) if file_path.exists() else ""

        account["statements"].append({
            "statementDate": statement_date,
            "filename": filename,
            "downloadedAt": datetime.now(timezone.utc).isoformat(),
            "downloadedBy": downloaded_by,
            "fileSizeBytes": file_size,
            "sha256": sha256,
        })

        self._save()

    def get_all_hashes(self, brokerage_slug: str) -> dict[str, str]:
        """Return {sha256: filename} for all downloaded statements in a brokerage."""
        hashes = {}
        brokerage = self.data["brokerages"].get(brokerage_slug)
        if not brokerage:
            return hashes
        for account in brokerage.get("accounts", {}).values():
            for stmt in account.get("statements", []):
                h = stmt.get("sha256", "")
                if h:
                    hashes[h] = stmt["filename"]
        return hashes

    def get_status_summary(self) -> dict[str, dict[str, int]]:
        """Return {brokerage_slug: {account_label: count}} for all tracked statements."""
        summary = {}
        for slug, brokerage in self.data.get("brokerages", {}).items():
            summary[slug] = {}
            for label, account in brokerage.get("accounts", {}).items():
                summary[slug][label] = len(account.get("statements", []))
        return summary


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

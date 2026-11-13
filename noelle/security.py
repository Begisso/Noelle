"""Users / roles / sessions and the audit trail (logs/audit/YYYY-MM-DD.csv)."""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import secrets
import time

ROLES = ["viewer", "operator", "engineer", "admin"]


def role_level(role: str) -> int:
    try:
        return ROLES.index(role)
    except ValueError:
        return 0


def hash_pw(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()
    return salt, h


class Security:
    def __init__(self, project):
        self.project = project
        self.path = os.path.join(project.root, "security.json")
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, tuple[str, float]] = {}
        self.audit_dir = os.path.join(project.root, "logs", "audit")
        self.load()

    @property
    def enabled(self) -> bool:
        return bool(self.project.cfg.get("security", {}).get("enabled"))

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.users = {u["name"]: u for u in json.load(f).get("users", [])}

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"users": list(self.users.values())}, f, indent=2)

    def set_user(self, name: str, role: str, password: str | None = None):
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        u = self.users.get(name, {"name": name})
        u["role"] = role
        if password:
            u["salt"], u["hash"] = hash_pw(password)
        self.users[name] = u
        self.save()

    def delete_user(self, name):
        self.users.pop(name, None)
        self.save()

    def login(self, name: str, password: str) -> str | None:
        u = self.users.get(name)
        if not u or "hash" not in u:
            self.audit(name, "login-failed", "", "", "")
            return None
        _, h = hash_pw(password, u["salt"])
        if not hmac.compare_digest(h, u["hash"]):
            self.audit(name, "login-failed", "", "", "")
            return None
        token = secrets.token_urlsafe(24)
        self.sessions[token] = (name, time.time() + 12 * 3600)
        self.audit(name, "login", "", "", "")
        return token

    def logout(self, token):
        s = self.sessions.pop(token, None)
        if s:
            self.audit(s[0], "logout", "", "", "")

    def user_for(self, token: str | None) -> tuple[str, str]:
        """Returns (user, role). With security disabled everybody is admin."""
        if not self.enabled:
            return "local", "admin"
        s = self.sessions.get(token or "")
        if not s or s[1] < time.time():
            return "guest", self.project.cfg.get("security", {}).get("guest_role", "viewer")
        name = s[0]
        return name, self.users.get(name, {}).get("role", "viewer")

    def audit(self, user, action, target, old, new, source=""):
        try:
            os.makedirs(self.audit_dir, exist_ok=True)
            p = os.path.join(self.audit_dir, time.strftime("%Y-%m-%d") + ".csv")
            new_file = not os.path.exists(p)
            with open(p, "a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["Time", "User", "Action", "Target", "Old", "New", "Source"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), user, action, target, old, new, source])
        except OSError:
            pass

import asyncio
import aiohttp
import json
import logging
from typing import Optional, Any
from config import DATABASES

logger = logging.getLogger(__name__)


class FirebaseManager:
    def __init__(self):
        self.db_name = None
        self.db_url = None
        self.db_keys = []
        self._session = None
        self._connected = False

    @property
    def session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def connect(self):
        for name, config in DATABASES.items():
            url = config["url"].rstrip("/")
            keys = config.get("keys", [])
            try:
                test_url = f"{url}/.json?shallow=true"
                async with self.session.get(test_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status in (200, 401, 403):
                        self.db_name = name
                        self.db_url = url
                        self.db_keys = keys
                        self._connected = True
                        logger.info(f"Connected to Firebase database: {name}")
                        return True
            except Exception as e:
                logger.warning(f"Failed to connect to {name}: {e}")
                continue
        logger.error("No Firebase database available")
        return False

    def _build_url(self, path: str, use_auth: bool = False) -> str:
        url = f"{self.db_url}/{path.lstrip('/')}.json"
        if use_auth and self.db_keys:
            url += f"?auth={self.db_keys[0]}"
        return url

    async def _request(self, method: str, path: str, data: Any = None) -> Optional[Any]:
        if not self._connected:
            raise ConnectionError("No Firebase database connected")
        url = self._build_url(path)
        try:
            kwargs = {"url": url, "timeout": aiohttp.ClientTimeout(total=10)}
            if data is not None:
                kwargs["data"] = json.dumps(data) if isinstance(data, (dict, list)) else data
                if method in ("put", "post", "patch"):
                    kwargs["headers"] = {"Content-Type": "application/json"}
            async with getattr(self.session, method)(**kwargs) as resp:
                if resp.status in (200, 204):
                    text = await resp.text()
                    return json.loads(text) if text.strip() else None
                if resp.status == 401:
                    for key in self.db_keys:
                        auth_url = f"{url}&auth={key}" if "?" in url else f"{url}?auth={key}"
                        kwargs["url"] = auth_url
                        async with getattr(self.session, method)(**kwargs) as auth_resp:
                            if auth_resp.status in (200, 204):
                                text = await auth_resp.text()
                                return json.loads(text) if text.strip() else None
                return None
        except Exception as e:
            logger.error(f"Firebase request failed: {e}")
            return None

    async def get(self, path: str) -> Optional[Any]:
        return await self._request("get", path)

    async def put(self, path: str, data: Any) -> bool:
        result = await self._request("put", path, data)
        return result is not None

    async def post(self, path: str, data: Any) -> Optional[str]:
        result = await self._request("post", path, data)
        if result and "name" in result:
            return result["name"]
        return None

    async def patch(self, path: str, data: dict) -> bool:
        result = await self._request("patch", path, data)
        return result is not None

    async def delete(self, path: str) -> bool:
        result = await self._request("delete", path)
        return result is not None

    async def get_user(self, user_id: int) -> Optional[dict]:
        return await self.get(f"/users/{user_id}")

    async def update_user(self, user_id: int, data: dict) -> bool:
        return await self.patch(f"/users/{user_id}", data)

    async def create_user(self, user_id: int, data: dict) -> bool:
        return await self.put(f"/users/{user_id}", data)

    async def get_available_numbers(self) -> list:
        data = await self.get("/numbers/available")
        if isinstance(data, dict):
            return list(data.keys())
        return []

    async def assign_number(self, number: str, user_id: int) -> bool:
        import time as time_module
        ts = time_module.time()
        return await self.put(f"/numbers/assigned/{number}", {
            "user_id": str(user_id),
            "assigned_at": ts
        })

    async def release_number(self, number: str) -> bool:
        assigned = await self.get(f"/numbers/assigned/{number}")
        if assigned:
            history = {
                "user": assigned.get("user_id"),
                "assigned": assigned.get("assigned_at"),
                "released": __import__("time").time()
            }
            await self.put(f"/numbers/history/{number}", history)
            await self.delete(f"/numbers/assigned/{number}")
        return True

    async def add_number(self, number: str) -> bool:
        return await self.put(f"/numbers/available/{number}", True)

    async def remove_number(self, number: str) -> bool:
        await self.delete(f"/numbers/available/{number}")
        await self.release_number(number)
        return True

    async def get_referral(self, code: str) -> Optional[dict]:
        return await self.get(f"/referrals/{code}")

    async def create_referral(self, code: str, referrer_id: int) -> bool:
        return await self.put(f"/referrals/{code}", {
            "referrer": str(referrer_id),
            "usedBy": [],
            "createdAt": __import__("time").time()
        })

    async def use_referral(self, code: str, user_id: int) -> bool:
        ref = await self.get_referral(code)
        if not ref:
            return False
        used_by = ref.get("usedBy", [])
        if str(user_id) in used_by:
            return False
        used_by.append(str(user_id))
        return await self.patch(f"/referrals/{code}", {"usedBy": used_by})

    async def add_log(self, action: str, user_id: int, details: dict = None) -> bool:
        import time as time_module
        ts = time_module.time()
        return await self.post(f"/logs", {
            "timestamp": ts,
            "action": action,
            "user": str(user_id),
            "details": details or {}
        })

    async def get_all_users(self) -> dict:
        data = await self.get("/users")
        return data if isinstance(data, dict) else {}

    async def get_banned_users(self) -> list:
        data = await self.get("/admin/settings/bannedUsers")
        return data if isinstance(data, list) else []

    async def ban_user(self, user_id: int) -> bool:
        banned = await self.get_banned_users()
        if str(user_id) not in banned:
            banned.append(str(user_id))
            return await self.put("/admin/settings/bannedUsers", banned)
        return True

    async def unban_user(self, user_id: int) -> bool:
        banned = await self.get_banned_users()
        if str(user_id) in banned:
            banned.remove(str(user_id))
            return await self.put("/admin/settings/bannedUsers", banned)
        return True

    async def get_settings(self) -> dict:
        data = await self.get("/admin/settings")
        return data if isinstance(data, dict) else {}

    async def update_settings(self, data: dict) -> bool:
        return await self.patch("/admin/settings", data)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

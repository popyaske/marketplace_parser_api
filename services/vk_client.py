"""Клиент VK API для публикации видео в личный профиль."""
import logging
import os
import time
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = "5.199"


class VKClient:
    def __init__(self, token: str, user_id: int, timeout: float = 300.0):
        if not token:
            raise ValueError("VK_ACCESS_TOKEN не задан")
        self.token = token
        self.user_id = int(user_id)
        self.client = httpx.Client(timeout=timeout)

    def _call(self, method: str, **params) -> dict:
        params["access_token"] = self.token
        params["v"] = VK_API_VERSION
        r = self.client.post(f"{VK_API_BASE}/{method}", data=params)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"VK API error {err.get('error_code')}: {err.get('error_msg')}"
            )
        return data.get("response", {})

    def upload_video(
        self,
        video_path: str,
        name: str,
        description: str = "",
        wallpost: bool = True,
    ) -> Tuple[int, int]:
        """
        Загружает видео в личный профиль через video.save.
        Возвращает (owner_id, video_id).
        """
        file_size = os.path.getsize(video_path)

        logger.info("VK: запрашиваю upload URL (video.save, size=%d)...", file_size)
        save_data = self._call(
            "video.save",
            name=name[:128],
            description=description[:5000],
            wallpost=1 if wallpost else 0,
            is_private=0,
        )
        upload_url = save_data.get("upload_url")
        video_id = save_data.get("video_id")
        owner_id = save_data.get("owner_id")

        if not upload_url:
            raise RuntimeError(f"video.save не вернул upload_url: {save_data}")

        logger.info("VK: загружаю файл видео...")
        with open(video_path, "rb") as f:
            r = self.client.post(upload_url, files={"video_file": f})
        r.raise_for_status()

        logger.info("VK: видео загружено owner_id=%s, video_id=%s", owner_id, video_id)
        return int(owner_id), int(video_id)

    def post_to_wall(self, message: str, attachments: str = "") -> Optional[int]:
        """Публикует запись на стене пользователя."""
        params = {
            "owner_id": self.user_id,
            "message": message,
        }
        if attachments:
            params["attachments"] = attachments
        resp = self._call("wall.post", **params)
        return resp.get("post_id")

    def close(self):
        self.client.close()
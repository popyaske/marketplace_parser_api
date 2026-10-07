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

    def upload_story(
        self,
        image_path: str,
        add_to_news: bool = True,
        group_id: int = 0,
        link_url: str = "",
        link_text: str = "more",
    ) -> dict:
        """
        Публикует фото в VK Историю.

        Args:
            image_path: путь к файлу (JPEG/PNG), 9:16 (1080×1920)
            add_to_news: True — в новости профиля, False — в личку
            group_id: ID сообщества (0 — от имени пользователя)
            link_url: URL для перехода из истории (2048 символов макс)
            link_text: текст ссылки (more, view, buy, learn_more и т.д.)

        Returns:
            dict: {"id": ..., "owner_id": ..., ...} — объект истории

        Note:
            Требуется право доступа `stories` у токена.
            Для юзер-токена — получить через devsupport@corp.vk.com.
            Для сообществ — выдаётся в настройках.
        """
        file_size = os.path.getsize(image_path)

        # === 1. Получаем upload URL ===
        params = {
            "add_to_news": 1 if add_to_news else 0,
        }
        if group_id:
            params["group_id"] = group_id
        if link_url:
            params["link_url"] = link_url[:2048]
            params["link_text"] = link_text

        logger.info("VK story: запрашиваю upload URL (size=%d)...", file_size)
        upload_data = self._call("stories.getPhotoUploadServer", **params)
        upload_url = upload_data.get("upload_url")
        if not upload_url:
            raise RuntimeError(
                f"stories.getPhotoUploadServer не вернул upload_url: {upload_data}"
            )

        # === 2. Загружаем файл ===
        logger.info("VK story: загружаю изображение...")
        with open(image_path, "rb") as f:
            r = self.client.post(
                upload_url,
                files={"file": ("story.jpg", f, "image/jpeg")},
            )
        r.raise_for_status()

        try:
            upload_result = r.json()
        except Exception as e:
            raise RuntimeError(f"Не JSON от upload: {r.text[:300]}") from e

        # В ответе приходит либо {"upload_result": "..."} либо напрямую объект
        upload_results = upload_result.get("upload_result") or r.text

        # === 3. Сохраняем историю ===
        logger.info("VK story: сохраняю историю...")
        save_params = {"upload_results": upload_results}
        if group_id:
            save_params["group_id"] = group_id

        save_data = self._call("stories.save", **save_params)

        items = save_data.get("items") or []
        if not items:
            raise RuntimeError(f"stories.save вернул пусто: {save_data}")

        story = items[0]
        logger.info(
            "VK story: опубликовано id=%s owner_id=%s",
            story.get("id"), story.get("owner_id"),
        )
        return story

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
"""core/telegram_links.py"""
from typing import Optional


def build_message_link(
    chat_id: int | str,
    message_id: int,
    chat_username: Optional[str] = None,
) -> str:
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    cid = str(chat_id).strip()
    if cid.startswith("-100"):
        cid = cid[4:]
    elif cid.startswith("-"):
        cid = cid[1:]
    return f"https://t.me/c/{cid}/{message_id}"
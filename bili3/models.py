from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ImageRecord:
    url: str
    url_clean: str
    file_name: str
    width: int = 0
    height: int = 0
    remote_size: int | None = None
    status: str = "pending"
    local_size: int | None = None


@dataclass(slots=True)
class PostRecord:
    dynamic_id: str
    uid: int
    dynamic_type: str = ""
    major_type: str = ""
    kind: str = "other"
    title: str = ""
    description: str = ""
    published_at: int = 0
    is_charge: bool = False
    is_accessible: bool = True
    is_forward: bool = False
    images: list[ImageRecord] = field(default_factory=list)
    author_name: str = ""
    author_avatar: str = ""

from datetime import datetime
from pydantic import BaseModel


class SongOut(BaseModel):
    id: int
    bvid: str
    title: str
    uploader: str
    duration: int
    cover: str
    download_status: str
    file_path: str = ""
    file_size: int = 0

    class Config:
        from_attributes = True


class QueueOut(BaseModel):
    id: int
    order: int
    status: str
    song: SongOut

    class Config:
        from_attributes = True


class PlaylistOut(BaseModel):
    id: int
    name: str
    created_at: datetime

    class Config:
        from_attributes = True


class PlayState(BaseModel):
    current: SongOut | None = None
    queue: list[QueueOut] = []
    status: str = "idle"
    position: float = 0
    volume: float = 0.8


class ImportRequest(BaseModel):
    bvid: str


class QueueAddRequest(BaseModel):
    bvid: str


class ReorderRequest(BaseModel):
    order: list[int]


class PlaylistCreate(BaseModel):
    name: str

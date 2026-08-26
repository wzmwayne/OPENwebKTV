from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, BigInteger
from sqlalchemy.orm import relationship
from .database import Base


class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True)
    bvid = Column(String(20), unique=True, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    uploader = Column(String(100), default="")
    duration = Column(Integer, default=0)
    cover = Column(String(500), default="")
    file_path = Column(String(500), default="")
    file_size = Column(BigInteger, default=0)
    download_status = Column(String(20), default="pending")
    lyrics = Column(Text, default="")   # 下载时同时保存的歌词(JSON: [{start,end,text}]); '[]'=显式无歌词(不回落兜底), ''=未存储(会回落B站/第三方)
    search_keyword = Column(String(100), default="")   # 用户搜索词(LRCLIB歌词查询用)
    created_at = Column(DateTime, default=datetime.utcnow)


class QueueItem(Base):
    __tablename__ = "queue_items"

    id = Column(Integer, primary_key=True)
    song_id = Column(Integer, ForeignKey("songs.id"), nullable=False)
    order = Column(Integer, nullable=False)
    status = Column(String(20), default="waiting")
    added_at = Column(DateTime, default=datetime.utcnow)

    song = relationship("Song")


class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PlaylistSong(Base):
    __tablename__ = "playlist_songs"

    id = Column(Integer, primary_key=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id"), nullable=False)
    song_id = Column(Integer, ForeignKey("songs.id"), nullable=False)
    order = Column(Integer, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

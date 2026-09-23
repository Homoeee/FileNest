from dataclasses import asdict, dataclass, field
from pathlib import Path
import uuid


CATEGORIES = {
    "文档": "pdf doc docx xls xlsx ppt pptx txt md csv odt rtf",
    "图片": "jpg jpeg png gif bmp webp svg tif tiff heic",
    "视频": "mp4 mov avi mkv webm wmv m4v",
    "音频": "mp3 wav flac aac ogg m4a wma",
    "压缩包": "zip rar 7z tar gz bz2 xz",
    "安装包": "exe msi msix dmg apk",
}


def category(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return next((name for name, extensions in CATEGORIES.items()
                 if ext in extensions.split()), "其他")


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime_ns: int
    device: int
    inode: int
    category: str
    kind: str = "file"


@dataclass
class Rule:
    name: str
    folder: str = ""
    extensions: str = ""
    keyword: str = ""
    min_size: int = 0
    max_size: int | None = None
    date_from: str = ""
    date_to: str = ""
    action: str = "move"
    conflict: str = "skip"
    by_month: bool = False
    prefix: str = ""
    suffix: str = ""
    replace_from: str = ""
    replace_to: str = ""
    add_date: bool = False
    sequence: bool = False
    enabled: bool = True
    categories: list[str] = field(default_factory=list)
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)


def default_rules() -> list[Rule]:
    return [Rule(name=f"{name}分类", folder=name, categories=[name],
                 uid=uuid.uuid5(uuid.NAMESPACE_URL, "filenest:" + name).hex)
            for name in [*CATEGORIES, "文件夹"]]


@dataclass
class PlanItem:
    source: str
    destination: str
    action: str
    status: str
    reason: str
    snapshot: dict = field(default_factory=dict)
    conflict: str = "skip"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

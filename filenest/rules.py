from datetime import date, datetime
from pathlib import Path

from .models import Rule
from .paths import relative_folder


def validate(rule: Rule):
    if not rule.name.strip():
        raise ValueError("规则名称不能为空")
    if rule.action not in {"move", "copy", "rename"}:
        raise ValueError("动作必须为移动、复制或重命名")
    if rule.conflict not in {"skip", "number"}:
        raise ValueError("冲突策略无效")
    relative_folder(rule.folder)
    if rule.min_size < 0 or (rule.max_size is not None and rule.max_size < rule.min_size):
        raise ValueError("文件大小范围无效")
    dates = [date.fromisoformat(v) if v else None for v in (rule.date_from, rule.date_to)]
    if all(dates) and dates[0] > dates[1]:
        raise ValueError("起始日期不能晚于结束日期")


def matches(rule, file):
    if not rule.enabled:
        return False
    path = Path(file.path)
    extensions = rule.extensions.lower().replace(",", " ").replace("，", " ").split()
    if extensions and path.suffix.lower().lstrip(".") not in [e.lstrip(".") for e in extensions]:
        return False
    if rule.keyword.casefold() not in path.name.casefold():
        return False
    if file.size < rule.min_size or (rule.max_size is not None and file.size > rule.max_size):
        return False
    modified = datetime.fromtimestamp(file.mtime_ns / 1e9).date().isoformat()
    return not ((rule.date_from and modified < rule.date_from)
                or (rule.date_to and modified > rule.date_to))


def renamed(rule, file, index):
    p = Path(file.path)
    stem = p.stem
    if rule.replace_from:
        stem = stem.replace(rule.replace_from, rule.replace_to)
    stem = rule.prefix + stem + rule.suffix
    if rule.add_date:
        stem += "_" + datetime.fromtimestamp(file.mtime_ns / 1e9).strftime("%Y%m%d")
    if rule.sequence:
        stem += f"_{index:03d}"
    return stem + p.suffix

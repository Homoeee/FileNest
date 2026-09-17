from datetime import datetime
from pathlib import Path

from .models import PlanItem
from .paths import available, fingerprint, inside, key, relative_folder, safe_path, validate_name
from .rules import matches, renamed, validate


def build_plan(files, rules, target, internal=(), cancel=None, progress=None):
    for rule in rules:
        validate(rule)
    target = safe_path(target)
    reserved, result, counters = set(), [], {}
    for file in files:
        if cancel is not None and cancel.is_set():
            break
        rule = next((r for r in rules if matches(r, file)), None)
        item = PlanItem(file.path, "", "", "unmatched", "没有匹配的启用规则")
        result.append(item)
        if rule is None:
            continue
        item.action, item.conflict = rule.action, rule.conflict
        item.reason = f"匹配规则「{rule.name}」：所有设置的条件均满足"
        try:
            source = safe_path(file.path)
            if any(inside(source, p) for p in internal):
                raise ValueError("不能处理内部数据文件")
            identity = id(rule)
            counters[identity] = counters.get(identity, 0) + 1
            name = renamed(rule, file, counters[identity])
            validate_name(name)
            folder = source.parent if rule.action == "rename" else target / relative_folder(rule.folder)
            if rule.by_month and rule.action != "rename":
                folder /= datetime.fromtimestamp(file.mtime_ns / 1e9).strftime("%Y/%m")
            destination = safe_path(folder / name)
            item.destination = str(destination)
            if any(inside(destination, p) for p in internal):
                raise ValueError("不能写入内部数据目录")
            if key(source) == key(destination):
                raise ValueError("原路径与目标路径相同，无需处理")
            if rule.action == "move" and source.drive.casefold() != destination.drive.casefold():
                raise ValueError("移动仅支持同一磁盘；跨磁盘请使用复制")
            if destination.exists() or key(destination) in reserved:
                if rule.conflict == "skip":
                    item.status, item.reason = "skipped", item.reason + "；目标同名冲突，跳过"
                    continue
                destination = available(destination, reserved)
                item.destination = str(destination)
                item.reason += "；同名自动编号"
            item.snapshot = fingerprint(source, cancel)
            if any(item.snapshot[k] != getattr(file, k) for k in ("size", "mtime_ns", "device", "inode")):
                raise ValueError("文件自扫描后发生变化，请重新扫描")
            reserved.add(key(destination))
            item.status = "ready"
        except InterruptedError:
            result.pop()
            break
        except (OSError, ValueError) as exc:
            item.status, item.reason = "invalid", str(exc)
        if progress:
            progress(f"生成预览 {len(result)} / {len(files)}")
    return result

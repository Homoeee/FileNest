from datetime import datetime
from pathlib import Path

from .models import PlanItem
from .paths import available, inside, key, relative_folder, safe_path, validate_name
from .rules import matches, renamed, validate
from .safety import check_operable, check_tree, snapshot


def build_plan(files, rules, target, internal=(), cancel=None, progress=None, source_excluded=()):
    for rule in rules:
        validate(rule)
    target = safe_path(target) if target else None
    reserved, result, counters = set(), [], {}
    for file in files:
        if cancel is not None and cancel.is_set():
            break
        rule = next((r for r in rules if matches(r, file)), None)
        item = PlanItem(file.path, '', '', 'unmatched', '没有匹配的启用规则')
        result.append(item)
        if rule is None:
            continue
        item.action, item.conflict = rule.action, rule.conflict
        item.reason = f'匹配规则「{rule.name}」：所有设置的条件均满足'
        try:
            source = check_operable(file.path, (*internal, *source_excluded))
            if file.kind == 'directory':
                item.warnings.append('整体处理文件夹及其全部内容；其子项目不会重复执行')
            if rule.action in {'delete', 'recycle'}:
                item.destination = '永久删除（不可恢复）' if rule.action == 'delete' else 'Windows 回收站'
                item.warnings.append('永久删除不可撤销，执行前须输入确认文字' if rule.action == 'delete'
                                     else '从原位置移除；请在 Windows 回收站还原，不提供应用内撤销')
            else:
                identity = id(rule)
                counters[identity] = counters.get(identity, 0) + 1
                name = renamed(rule, file, counters[identity])
                validate_name(name)
                if rule.action in {'move', 'copy'} and target is None:
                    raise ValueError('移动或复制规则需要目标目录')
                folder = source.parent if rule.action == 'rename' else target / relative_folder(rule.folder)
                if rule.by_month and rule.action != 'rename':
                    folder /= datetime.fromtimestamp(file.mtime_ns / 1e9).strftime('%Y/%m')
                destination = check_operable(folder / name, internal)
                item.destination = str(destination)
                if key(source) == key(destination):
                    raise ValueError('原路径与目标路径相同，无需处理')
                if file.kind == 'directory' and inside(destination, source):
                    raise ValueError('目标不能位于待处理文件夹内部')
                if destination.exists() or key(destination) in reserved:
                    if rule.conflict == 'skip':
                        item.status, item.reason = 'skipped', item.reason + '；目标同名冲突，跳过'
                        continue
                    destination = available(destination, reserved)
                    item.destination = str(destination)
                    item.reason += '；同名自动编号'
                if rule.action == 'move' and source.drive.casefold() != destination.drive.casefold():
                    item.warnings.append('跨盘移动：先复制并校验，再移除源项目；中断时请核对两侧记录')
                reserved.add(key(destination))
            item.snapshot = snapshot(source, cancel)
            if any(item.snapshot[k] != getattr(file, k) for k in ('size', 'mtime_ns', 'device', 'inode')):
                raise ValueError('项目自扫描后发生变化，请重新扫描')
            check_tree(source, item.snapshot, (*internal, *source_excluded))
            item.status = 'ready'
        except InterruptedError:
            result.pop()
            break
        except (OSError, ValueError) as exc:
            item.status, item.reason = 'invalid', str(exc)
        if progress:
            progress(f'生成预览 {len(result)} / {len(files)}')
    # 父目录与子项同时选中时，只执行最外层可执行目录，避免重复移动或删除。
    parents = [p for p in result if p.status == 'ready' and p.snapshot.get('kind') == 'directory']
    for item in result:
        if item.status == 'ready' and any(p is not item and inside(item.source, p.source) for p in parents):
            item.status, item.reason = 'skipped', '已包含在所选父文件夹的整体操作中，不重复处理'
    active = [p for p in result if p.status == 'ready']
    for item in active:
        if item.action not in {'delete', 'recycle'} and any(p is not item and
                (inside(item.destination, p.source) or inside(p.source, item.destination)) for p in active):
            item.status, item.reason = 'invalid', '目标与本批次其他来源交叠，请拆分批次处理'
    return result

import threading
import uuid
from pathlib import Path

import pytest

from filenest.executor import Executor
from filenest.models import Rule
from filenest.planner import build_plan
from filenest.scanner import scan
from filenest.storage import Store


@pytest.fixture
def workspace():
    # pytest 的临时根目录固定到项目内，避免测试触碰真实资料目录。
    root = Path(__file__).parent / "_tmp" / f"case-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    yield root
    # 这里只清理由本测试以随机名称明确创建的目录。
    import shutil
    shutil.rmtree(root)


def write(path, content=b"sample"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scan_handles_chinese_exclusions_and_partial_files(workspace):
    source = workspace / "来源"
    write(source / "合同.pdf")
    write(source / "图片" / "照片.JPG")
    write(source / "忽略" / "秘密.txt")
    write(source / "下载中.part")

    files, errors = scan([source], recursive=True, excluded=[source / "忽略"])

    assert not errors
    assert [Path(f.path).name for f in files] == ["合同.pdf", "照片.JPG"]
    assert {f.category for f in files} == {"文档", "图片"}


def test_plan_matches_first_rule_and_numbers_conflicts(workspace):
    source, target = workspace / "来源", workspace / "目标"
    write(source / "季度合同.pdf", b"new")
    write(target / "合同" / "季度合同.pdf", b"old")
    files, _ = scan([source])
    rules = [
        Rule("合同", folder="合同", extensions="pdf", keyword="合同", conflict="number"),
        Rule("所有 PDF", folder="文档", extensions="pdf"),
    ]

    plan = build_plan(files, rules, target)

    assert len(plan) == 1
    assert plan[0].status == "ready"
    assert Path(plan[0].destination).name == "季度合同 (1).pdf"
    assert "合同" in Path(plan[0].destination).parts
    assert "同名自动编号" in plan[0].reason


def test_execute_move_records_and_undoes(workspace):
    source, target = workspace / "来源", workspace / "目标"
    original = write(source / "报告.txt", "内容".encode())
    files, _ = scan([source])
    plan = build_plan(files, [Rule("文档", folder="文档", extensions="txt")], target)
    store = Store(workspace / "内部")
    executor = Executor(store)

    batch = executor.execute(plan)
    destination = Path(plan[0].destination)
    assert destination.read_text(encoding="utf-8") == "内容"
    assert not original.exists()
    assert store.operations(batch)[0]["status"] == "success"

    executor.undo(batch)
    row = store.operations(batch)[0]
    assert original.read_text(encoding="utf-8") == "内容"
    assert not destination.exists()
    assert row["undo_status"] == "success"


def test_execute_rejects_file_changed_after_preview(workspace):
    source, target = workspace / "来源", workspace / "目标"
    original = write(source / "报告.txt", b"before")
    files, _ = scan([source])
    plan = build_plan(files, [Rule("文本", folder="文本", extensions="txt")], target)
    original.write_bytes(b"changed after preview")
    store = Store(workspace / "内部")

    batch = Executor(store).execute(plan)

    row = store.operations(batch)[0]
    assert row["status"] == "failed"
    assert "预览后发生变化" in row["reason"]
    assert original.exists()
    assert not Path(plan[0].destination).exists()


def test_execute_skips_conflict_created_after_preview(workspace):
    source, target = workspace / "来源", workspace / "目标"
    original = write(source / "同名.txt", b"source")
    files, _ = scan([source])
    plan = build_plan(files, [Rule("文本", folder="文本", extensions="txt", conflict="number")], target)
    destination = write(Path(plan[0].destination), b"appeared later")
    store = Store(workspace / "内部")

    batch = Executor(store).execute(plan)

    row = store.operations(batch)[0]
    assert row["status"] == "skipped"
    assert original.read_bytes() == b"source"
    assert destination.read_bytes() == b"appeared later"


def test_cancelled_items_are_distinct_from_completed(workspace):
    source, target = workspace / "来源", workspace / "目标"
    write(source / "一.txt")
    write(source / "二.txt")
    files, _ = scan([source])
    plan = build_plan(files, [Rule("文本", folder="文本", extensions="txt")], target)
    cancel = threading.Event()
    cancel.set()
    store = Store(workspace / "内部")

    batch = Executor(store).execute(plan, cancel)

    rows = store.operations(batch)
    assert {row["status"] for row in rows} == {"cancelled"}
    assert all(Path(item.source).exists() for item in plan)


def test_undo_refuses_to_overwrite_new_file(workspace):
    source, target = workspace / "来源", workspace / "目标"
    original = write(source / "记录.txt", b"original")
    files, _ = scan([source])
    plan = build_plan(files, [Rule("文本", folder="文本", extensions="txt")], target)
    store = Store(workspace / "内部")
    executor = Executor(store)
    batch = executor.execute(plan)
    write(original, b"new file")

    executor.undo(batch)

    row = store.operations(batch)[0]
    assert row["undo_status"] == "failed"
    assert "不覆盖" in row["undo_reason"]
    assert original.read_bytes() == b"new file"
    assert Path(plan[0].destination).read_bytes() == b"original"


def test_copy_is_recorded_but_not_automatically_undone(workspace):
    source, target = workspace / "来源", workspace / "目标"
    original = write(source / "资料.txt", b"content")
    files, _ = scan([source])
    plan = build_plan(files, [Rule("复制文本", folder="副本", extensions="txt", action="copy")], target)
    store = Store(workspace / "内部")
    executor = Executor(store)
    batch = executor.execute(plan)

    executor.undo(batch)

    row = store.operations(batch)[0]
    assert row["status"] == "success"
    assert row["undo_status"] == ""
    assert original.exists() and Path(plan[0].destination).exists()


def test_repeated_execution_does_not_duplicate_or_fake_success(workspace):
    source, target = workspace / "来源", workspace / "目标"
    write(source / "唯一.txt", b"once")
    files, _ = scan([source])
    plan = build_plan(files, [Rule("文本", folder="文本", extensions="txt")], target)
    store = Store(workspace / "内部")
    executor = Executor(store)

    first = executor.execute(plan)
    second = executor.execute(plan)

    assert store.operations(first)[0]["status"] == "success"
    assert store.operations(second)[0]["status"] == "failed"
    assert Path(plan[0].destination).read_bytes() == b"once"


def test_recover_marks_interrupted_work_without_claiming_success(workspace):
    source = write(workspace / "来源" / "待处理.txt")
    destination = workspace / "目标" / "待处理.txt"
    files, _ = scan([source.parent])
    plan = build_plan(files, [Rule("文本", folder="", extensions="txt")], destination.parent)
    store = Store(workspace / "内部")
    batch, ids = store.begin(plan)
    store.update(ids[0], status="running")

    store.recover()

    row = store.operations(batch)[0]
    assert row["status"] == "uncertain"
    assert "人工核对" in row["reason"]
    assert source.exists() and not destination.exists()


def test_rename_template_executes_and_can_be_undone(workspace):
    source = write(workspace / "来源" / "旧名称.txt", b"rename")
    files, _ = scan([source.parent])
    rule = Rule("重命名", action="rename", extensions="txt", replace_from="旧", replace_to="新",
                prefix="归档_", suffix="_完成", sequence=True)
    plan = build_plan(files, [rule], workspace / "不使用的目标")
    store = Store(workspace / "内部")
    executor = Executor(store)

    batch = executor.execute(plan)

    renamed = source.with_name("归档_新名称_完成_001.txt")
    assert renamed.read_bytes() == b"rename"
    assert not source.exists()
    executor.undo(batch)
    assert source.read_bytes() == b"rename"
    assert not renamed.exists()


def test_size_and_date_conditions_leave_nonmatching_file_in_place(workspace):
    source = write(workspace / "来源" / "资料.bin", b"small")
    files, _ = scan([source.parent])
    rule = Rule("大文件", folder="大文件", min_size=1024, date_from="2099-01-01")

    plan = build_plan(files, [rule], workspace / "目标")

    assert plan[0].status == "unmatched"
    assert source.exists()

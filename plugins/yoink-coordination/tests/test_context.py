import context
from context import extract_task_issue, Context

def test_task_issue_feature_prefix():
    assert extract_task_issue("feature/123-foo", "kweiza/yoink") == "kweiza/yoink#123"

def test_task_issue_fix_prefix():
    assert extract_task_issue("fix-456", "o/r") == "o/r#456"

def test_task_issue_hotfix():
    assert extract_task_issue("hotfix/789-urgent", "o/r") == "o/r#789"

def test_task_issue_no_prefix_rejected():
    assert extract_task_issue("123-add-auth", "o/r") is None

def test_task_issue_release_rejected():
    assert extract_task_issue("release-2024", "o/r") is None

def test_task_issue_v2_rejected():
    assert extract_task_issue("v2-migration", "o/r") is None

def test_task_issue_trailing_digit_rejected():
    assert extract_task_issue("chore-foo-2", "o/r") is None

def test_task_issue_nested_prefix():
    assert extract_task_issue("kweiza/feature/42-cleanup", "o/r") == "o/r#42"

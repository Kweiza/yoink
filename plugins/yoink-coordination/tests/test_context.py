from unittest.mock import patch, MagicMock
import context
from context import extract_task_issue, Context, _origin_host, detect_login


def _mock_run(stdout="", returncode=0):
    r = MagicMock(); r.stdout = stdout; r.returncode = returncode; r.stderr = ""
    return r

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


def test_origin_host_scp_ssh():
    with patch("context.subprocess.run", return_value=_mock_run("git@github.com:user/repo.git\n")):
        assert _origin_host() == "github.com"


def test_origin_host_scp_ssh_ghe():
    with patch("context.subprocess.run", return_value=_mock_run("git@github.ecodesamsung.com:user/repo\n")):
        assert _origin_host() == "github.ecodesamsung.com"


def test_origin_host_https():
    with patch("context.subprocess.run", return_value=_mock_run("https://github.com/user/repo.git\n")):
        assert _origin_host() == "github.com"


def test_origin_host_https_with_user():
    with patch("context.subprocess.run", return_value=_mock_run("https://user@github.com/user/repo\n")):
        assert _origin_host() == "github.com"


def test_origin_host_ssh_url_with_port():
    with patch("context.subprocess.run", return_value=_mock_run("ssh://git@github.com:22/user/repo\n")):
        assert _origin_host() == "github.com"


def test_origin_host_no_origin():
    with patch("context.subprocess.run", return_value=_mock_run("", returncode=1)):
        assert _origin_host() is None


def test_origin_host_unparseable():
    with patch("context.subprocess.run", return_value=_mock_run("garbage-no-host\n")):
        assert _origin_host() is None


def test_detect_login_passes_hostname_from_origin():
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _mock_run("git@github.ecodesamsung.com:user/repo\n")
        if cmd[0] == "gh":
            return _mock_run("jabez-park\n")
        return _mock_run("", returncode=1)
    with patch("context.subprocess.run", side_effect=fake_run):
        assert detect_login() == "jabez-park"
    gh_call = next(c for c in calls if c[0] == "gh")
    assert "--hostname" in gh_call
    assert gh_call[gh_call.index("--hostname") + 1] == "github.ecodesamsung.com"


def test_detect_login_omits_hostname_when_origin_unknown():
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _mock_run("", returncode=1)
        if cmd[0] == "gh":
            return _mock_run("kweiza\n")
        return _mock_run("", returncode=1)
    with patch("context.subprocess.run", side_effect=fake_run):
        assert detect_login() == "kweiza"
    gh_call = next(c for c in calls if c[0] == "gh")
    assert "--hostname" not in gh_call

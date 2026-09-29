import importlib.util
import json
import pathlib

import pytest

LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "plugins" / "brainstem" / "launch.py"


@pytest.fixture
def launch(monkeypatch):
    spec = importlib.util.spec_from_file_location("launch", LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_env_ready", lambda: True)
    return module


def run_setup(launch, capsys, brainstem):
    launch._brainstem = lambda: brainstem
    code = launch.setup()
    return code, json.loads(capsys.readouterr().out)


def test_ready_when_bridge_built_and_brainstem_signed_in(launch, capsys):
    code, report = run_setup(launch, capsys, {"reachable": True, "url": "u", "version": "0.6.16",
                                              "signedIn": True, "model": "m", "agents": 3})
    assert code == 0 and report["ready"] and report["nextSteps"] == []


def test_missing_brainstem_points_at_the_public_installer(launch, capsys):
    code, report = run_setup(launch, capsys, {"reachable": False, "url": "u"})
    assert code == 1 and not report["ready"]
    assert launch.INSTALL_HINT in report["nextSteps"][0]


def test_signed_out_brainstem_says_to_sign_in(launch, capsys):
    code, report = run_setup(launch, capsys, {"reachable": True, "url": "u", "version": "0.6.16",
                                              "signedIn": False, "model": "m", "agents": 3})
    assert code == 1 and "sign in" in report["nextSteps"][0]


def test_failed_package_install_is_reported_not_raised(launch, capsys, monkeypatch):
    monkeypatch.setattr(launch, "_env_ready", lambda: False)

    def broken(log):
        raise RuntimeError("pip could not install")

    monkeypatch.setattr(launch, "_build_env", broken)
    code, report = run_setup(launch, capsys, {"reachable": True, "url": "u", "signedIn": True})
    assert code == 1 and report["bridge"].startswith("not ready")


def test_unreachable_url_is_reported(launch, monkeypatch):
    monkeypatch.setattr(launch, "BRAINSTEM_URL", "http://127.0.0.1:1")
    assert launch._brainstem() == {"reachable": False, "url": "http://127.0.0.1:1"}

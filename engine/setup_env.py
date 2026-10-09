# -*- coding: utf-8 -*-
"""다른 PC에서 이 도구를 쓸 수 있게 engine 폴더 안에 실행 환경을 만든다.

    python engine\\setup_env.py

무엇을 하는가
  1. 쓸 만한 Python 3.14 를 찾는다
  2. engine\\venv 를 만든다  (폴더 안이므로 도구를 옮기면 같이 간다)
  3. engine\\requirements.txt 로 패키지를 설치한다
  4. 변환 엔진 rhwp 를 GitHub 릴리스에서 받아 engine\\rhwp 에 둔다 (지문 대조)
  5. 핵심 import 를 확인하고, 합성 문서 하나를 실제로 끝까지 변환해 본다

왜 필요한가
  venv 는 이식되지 않는다 — pyvenv.cfg 의 `home =` 에 만들어진 PC의 파이썬
  절대경로가 박히기 때문이다. 폴더를 다른 PC로 복사하면 여기서 새로 만들어야 한다.
  ('HWP to PDF 실행.bat' 도 이 상황을 감지해 안내한다.)

★ 이 스크립트가 못 하는 것: Python 3.14 본체 설치 (venv 를 만들 바탕이 필요하다)
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
BASE = ENGINE_DIR.parent
VENV_DIR = ENGINE_DIR / "venv"
REQ = ENGINE_DIR / "requirements.txt"
VENV_PY = VENV_DIR / "Scripts" / "python.exe"
RHWP_DIR = ENGINE_DIR / "rhwp"
RHWP_EXE = RHWP_DIR / "rhwp.exe"

REQUIRED_IMPORTS = ["pikepdf", "pypdfium2", "numpy", "PIL"]

# 변환 엔진. 판을 고정하고 받은 파일의 지문을 대조한다 — 판이 바뀌면 그림이 달라질 수 있어
# 새 판은 검증(engine\\종단_검증.py) 을 거친 뒤에만 여기서 올린다.
RHWP_VERSION = "0.8.7"
RHWP_URL = (f"https://github.com/edwardkim/rhwp/releases/download/v{RHWP_VERSION}/"
            f"rhwp-v{RHWP_VERSION}-windows-x86_64.zip")
RHWP_SHA256 = "e8d5654b9040561e1cb07474db54ce09f0fe4def45777833b9d9cc6fb1fade3c"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def find_python() -> str | None:
    if sys.version_info[:2] == (3, 14):
        return sys.executable
    for cand in ("python", "python3", "py"):
        exe = shutil.which(cand)
        if not exe:
            continue
        try:
            args = [exe, "-3.14"] if cand == "py" else [exe]
            out = subprocess.run(args + ["-c", "import sys;print('%d.%d' % sys.version_info[:2]);print(sys.executable)"],
                                 capture_output=True, text=True, timeout=30)
            lines = out.stdout.split()
            if lines and lines[0] == "3.14":
                return lines[1]
        except Exception:
            pass
    return None


def run(args: list[str], desc: str) -> bool:
    say(f"  → {desc}")
    try:
        return subprocess.run(args, timeout=3600).returncode == 0
    except Exception as e:
        say(f"    실패: {type(e).__name__}: {e}")
        return False


def rhwp_version() -> str | None:
    if not RHWP_EXE.is_file():
        return None
    try:
        p = subprocess.run([str(RHWP_EXE), "--version"], capture_output=True, timeout=60)
        out = p.stdout.decode("utf-8", "replace")
        return out.strip().split()[-1].lstrip("v") if p.returncode == 0 and out.strip() else None
    except Exception:
        return None


def fetch_rhwp() -> bool:
    if rhwp_version() == RHWP_VERSION:
        say(f"\n[3/4] 변환 엔진 rhwp {RHWP_VERSION}: 이미 있습니다 — 건너뜁니다")
        return True
    say(f"\n[3/4] 변환 엔진 rhwp {RHWP_VERSION} 내려받기 (약 11MB)")
    zpath = ENGINE_DIR / "_rhwp.zip"
    try:
        urllib.request.urlretrieve(RHWP_URL, zpath)
        digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
        if digest != RHWP_SHA256:
            say("    [실패] 받은 파일의 지문이 다릅니다 — 쓰지 않고 지웁니다")
            say(f"           기대 {RHWP_SHA256}\n           실제 {digest}")
            return False
        RHWP_DIR.mkdir(exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                base = name.rsplit("/", 1)[-1]
                if base in ("rhwp.exe", "LICENSE"):
                    (RHWP_DIR / base).write_bytes(z.read(name))
        say(f"    완료 → {RHWP_EXE}")
        return rhwp_version() == RHWP_VERSION
    except Exception as e:
        say(f"    [실패] {type(e).__name__}: {e}")
        say(f"    직접 받으려면: {RHWP_URL}")
        say(f"    zip 안의 rhwp.exe 를 {RHWP_DIR} 에 두세요.")
        return False
    finally:
        zpath.unlink(missing_ok=True)


def main() -> int:
    say("=" * 62)
    say(" HWP to PDF 환경 설치")
    say("=" * 62)
    say(f" 도구 폴더 : {BASE}")

    if not REQ.is_file():
        say(f"\n[오류] 패키지 목록이 없습니다: {REQ}")
        return 1

    if VENV_PY.is_file():
        # 이 PC에서 실제로 기동되는지까지 확인한다 — 옮겨온 venv 는 파일은 있어도
        # pyvenv.cfg 가 가리키는 파이썬이 없어 실행되지 않는다.
        p = subprocess.run([str(VENV_PY), "-c", "import sys"], capture_output=True)
        if p.returncode == 0:
            say(f"\n이미 쓸 수 있는 파이썬 환경이 있습니다: {VENV_DIR}")
            run([str(VENV_PY), "-m", "pip", "install", "-q", "-r", str(REQ)], "패키지 판 맞추기")
            fetch_rhwp()
            return verify()
        say("\nvenv 가 있지만 이 PC에서 기동되지 않습니다 (다른 PC에서 만들어진 것).")
        say("  지우고 다시 만듭니다...")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    base = find_python()
    if not base:
        say("\n[오류] Python 3.14 를 찾지 못했습니다.")
        say("  python.org 에서 3.14 를 설치한 뒤 다시 실행하세요.")
        return 1
    say(f" 바탕 파이썬: {base}")

    say("\n[1/4] 가상환경 생성")
    if not run([base, "-m", "venv", str(VENV_DIR)], f"venv → {VENV_DIR}"):
        return 1

    say("\n[2/4] 패키지 설치 (1~2분)")
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "-q"], "pip 갱신")
    if not run([str(VENV_PY), "-m", "pip", "install", "-r", str(REQ)], "requirements.txt 설치"):
        say("    설치에 실패했습니다. 위의 오류를 확인하세요.")
        return 1

    fetch_rhwp()
    return verify()


def verify() -> int:
    say("\n[4/4] 검증")
    if not VENV_PY.is_file():
        say(f"  [오류] {VENV_PY} 가 없습니다.")
        return 1
    ok = True
    for mod in REQUIRED_IMPORTS:
        p = subprocess.run([str(VENV_PY), "-c", f"import {mod}"], capture_output=True)
        ok &= p.returncode == 0
        say(f"  {'OK ' if p.returncode == 0 else '실패'}  import {mod}")

    ver = rhwp_version()
    if ver == RHWP_VERSION:
        say(f"  OK   변환 엔진 rhwp {ver}")
    else:
        ok = False
        say(f"  실패  변환 엔진 rhwp — {'없음' if ver is None else ver + ' (기대 ' + RHWP_VERSION + ')'}")

    # import 가 된다고 도구가 도는 것은 아니다 — 합성 문서 하나를 끝까지 변환해 본다
    if ok:
        say("  …   합성 문서로 실제 변환 시험")
        p = subprocess.run([str(VENV_PY), str(ENGINE_DIR / "종단_검증.py"), "--smoke"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        last = (p.stdout.strip().splitlines() or ["(출력 없음)"])[-1]
        if p.returncode == 0:
            say(f"  OK   {last}")
        else:
            ok = False
            say(f"  실패  변환 시험: {last}")
            say(p.stdout[-1500:] + p.stderr[-1500:])

    say("")
    say("=" * 62)
    say(" 준비 완료 — 'HWP to PDF 실행.bat' 으로 시작하세요."
        if ok else " 위의 실패 항목을 해결한 뒤 다시 실행하세요.")
    say("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

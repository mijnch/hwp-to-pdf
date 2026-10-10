# -*- coding: utf-8 -*-
"""다른 PC에서 이 도구를 쓸 수 있게 engine 폴더 안에 실행 환경을 만든다.

    python engine\\setup_env.py

무엇을 하는가
  1. 쓸 만한 Python 3.14 를 찾는다
  2. engine\\venv 를 만든다  (폴더 안이므로 도구를 옮기면 같이 간다)
  3. engine\\requirements.txt 로 패키지를 설치한다
  4. 변환 엔진을 받아 engine\\rhwp 에 둔다 (지문 대조) — 이 저장소 릴리스의 고친 rhwp 를 먼저,
     받지 못하면 rhwp 원래 릴리스를. 같은 고친 판이 이미 있으면 그대로 둔다
     (고친 판은 engine\\build_rhwp.py 로 소스에서 다시 만들 수도 있다)
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

# 고친 엔진 — 위 판의 devel 소스에 engine\rhwp_patches 의 고침 8건을 넣어 build_rhwp.py 로 만든 것을
# 이 저장소의 릴리스 파일로 올려 두었다. 먼저 이것을 받고(지문 대조), 받지 못하면 원래 릴리스를 받는다.
# 고침을 바꾸면 build_rhwp.py 로 새로 빌드해 새 릴리스로 올리고 여기 세 값을 함께 바꾼다.
ENGINE_TAG = "21ed640.p5092a3"   # rhwp_cli.build_tag 형식: 바탕 커밋.p고침 지문
ENGINE_URL = ("https://github.com/mijnch/hwp-to-pdf/releases/download/engine-21ed640-p5092a3/"
              "rhwp-hwp2pdf-21ed640-p5092a3-windows-x86_64.zip")
ENGINE_SHA256 = "aade8cf30df291221fa1f347c6d598cf3dc9c43de4fde33f91be6e7ad7e3bb76"


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


def installed_tag() -> str | None:
    """설치된 rhwp 가 BUILD.json 의 고친 판이면 그 표지(예: 21ed640.p5092a3)."""
    sys.path.insert(0, str(ENGINE_DIR))
    from rhwp_cli import build_tag
    return build_tag(RHWP_EXE)


def install_zip(zpath: Path, members: set[str], rhwp_dir: Path = RHWP_DIR) -> bool:
    """zip 안의 members(파일 이름)를 rhwp_dir 에 둔다. rhwp.exe 가 없으면 아무것도 바꾸지 않는다.

    새 파일을 모두 읽은 뒤에 쓴다 — 읽다 실패하면 쓰던 엔진이 그대로 남는다. zip 에 BUILD.json 이
    없으면(원래 릴리스) 남아 있던 BUILD.json 을 지운다 — 다른 실행 파일을 가리키는 낡은 출처 표지다.
    """
    found: dict[str, bytes] = {}
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if base in members:
                found[base] = z.read(name)
    if "rhwp.exe" not in found:
        return False
    rhwp_dir.mkdir(exist_ok=True)
    if "BUILD.json" not in found:
        (rhwp_dir / "BUILD.json").unlink(missing_ok=True)
    for base, data in found.items():
        (rhwp_dir / base).write_bytes(data)
    return True


def download_engine(url: str, sha256: str, members: set[str]) -> bool:
    zpath = ENGINE_DIR / "_rhwp.zip"
    try:
        urllib.request.urlretrieve(url, zpath)
        digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
        if digest != sha256:
            say("    [실패] 받은 파일의 지문이 다릅니다 — 쓰지 않고 지웁니다")
            say(f"           기대 {sha256}")
            say(f"           실제 {digest}")
            return False
        if not install_zip(zpath, members):
            say("    [실패] zip 안에 rhwp.exe 가 없습니다")
            return False
        return True
    except Exception as e:
        say(f"    [실패] {type(e).__name__}: {e}")
        return False
    finally:
        zpath.unlink(missing_ok=True)


def fetch_rhwp() -> bool:
    if rhwp_version() == RHWP_VERSION and installed_tag() == ENGINE_TAG:
        say()
        say(f"[3/4] 변환 엔진: 고친 rhwp({ENGINE_TAG})가 이미 있습니다 — 건너뜁니다")
        return True
    say()
    say(f"[3/4] 변환 엔진 내려받기 — 고친 rhwp {ENGINE_TAG} (약 12MB, 이 저장소 릴리스)")
    if (download_engine(ENGINE_URL, ENGINE_SHA256, {"rhwp.exe", "LICENSE", "BUILD.json"})
            and rhwp_version() == RHWP_VERSION and installed_tag() == ENGINE_TAG):
        say(f"    완료 → {RHWP_EXE}")
        return True
    if rhwp_version() == RHWP_VERSION:
        say("    고친 판을 받지 못해 지금 있는 rhwp 를 그대로 씁니다. 다시 실행하면 다시 받습니다.")
        return True
    say(f"    고친 판을 받지 못해 rhwp 원래 릴리스 {RHWP_VERSION} 를 받습니다 — 한컴 일치율이 낮습니다")
    say("    (README 「5」: 시험지 1쪽 약 80% 대 98.6%). 나중에 이 설치를 다시 실행하세요.")
    if download_engine(RHWP_URL, RHWP_SHA256, {"rhwp.exe", "LICENSE"}) and rhwp_version() == RHWP_VERSION:
        say(f"    완료 → {RHWP_EXE}")
        return True
    say(f"    직접 받으려면: {ENGINE_URL}")
    say(f"    zip 안의 rhwp.exe·LICENSE·BUILD.json 을 {RHWP_DIR} 에 두세요.")
    return False


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
        tag = installed_tag()
        if tag == ENGINE_TAG:
            say(f"  OK   변환 엔진 rhwp {ver} — 고친 판 ({tag})")
        elif tag:
            say(f"  OK   변환 엔진 rhwp {ver} — 고친 판이지만 이 판이 기대하는 것과 다름 ({tag} ≠ {ENGINE_TAG})")
        else:
            say(f"  OK   변환 엔진 rhwp {ver} — 원래 릴리스 (한컴 일치율이 낮다)")
            say("       고친 판은 이 설치를 다시 실행하면 받고, engine\\build_rhwp.py 로 직접 만들 수도 있습니다")
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

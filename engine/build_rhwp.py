# -*- coding: utf-8 -*-
"""변환 엔진 rhwp 를 이 도구의 고침(engine\\rhwp_patches)을 넣어 원본 소스에서 새로 만든다.

    engine\\venv\\Scripts\\python.exe engine\\build_rhwp.py          만들어서 engine\\rhwp 에 설치
    engine\\venv\\Scripts\\python.exe engine\\build_rhwp.py --keep   빌드 폴더를 남긴다(다시 만들 때 빠르다)

왜 필요한가
  rhwp 0.8.7 릴리스는 한컴과 조판이 몇 화소~몇 줄 어긋나는 곳이 있다. 원본과 맞대 찾은
  여덟 가지를 고쳐(rhwp_patches\\README.md) 사용자 시험지의 한컴 미리보기 실루엣 일치율이
  평균 80 → 98.6% 로 올랐다. 고친 판은 릴리스에 없으므로 여기서 만든다.

무엇을 하는가 (모두 빌드 폴더 안에서 — 시스템 PATH 와 설정은 건드리지 않는다)
  1. Rust 1.93.1 (x86_64-pc-windows-gnu, 최소 구성)  — rustup-init, 지문 대조
  2. WinLibs MinGW-w64 GCC 16.2.0 (dlltool·gcc)      — 지문 대조
  3. rhwp 소스를 고정된 커밋에서 받고(git, 필요한 폴더만) 고침을 적용
  4. cargo build --release --bin rhwp  (처음엔 10~25분)
  5. 판 확인 + 합성 문서 변환 시험 뒤 engine\\rhwp\\rhwp.exe 를 바꾸고 BUILD.json 에 출처를 적는다
     (기존 실행 파일은 rhwp.exe.prev 로 남긴다)

필요한 것: git, 인터넷, 디스크 약 5GB(끝나면 --keep 이 없을 때 지운다).

빌드 폴더: engine\\.build. 단 그 경로에 빈칸이 있으면(도구 폴더 'HWP to PDF' 가 그렇다)
%LOCALAPPDATA%\\hwp2pdf-build 를 쓴다 — MinGW gcc 는 자기 설치 경로에 빈칸이 있으면 링크하지
못한다(사양 파일의 default-manifest.o 경로가 빈칸에서 잘린다. 이 PC 에서 실측).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent


def _build_root() -> Path:
    local = ENGINE_DIR / ".build"
    if " " not in str(local):
        return local
    alt = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hwp2pdf-build"
    if " " in str(alt):
        alt = Path(os.environ.get("SystemDrive", "C:") + "/") / "hwp2pdf-build"
    return alt


BUILD = _build_root()
RHWP_DIR = ENGINE_DIR / "rhwp"
PATCH = ENGINE_DIR / "rhwp_patches" / "hwp2pdf-rhwp.patch"

RUST_TOOLCHAIN = "1.93.1"
RUSTUP_URL = "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe"
WINLIBS_URL = ("https://github.com/brechtsanders/winlibs_mingw/releases/download/16.2.0posix-14.0.0-ucrt-r1/"
               "winlibs-x86_64-posix-seh-gcc-16.2.0-mingw-w64ucrt-14.0.0-r1.zip")
WINLIBS_SHA256 = "c1f52294597c0b73786b2a78eb5d176d89226d2f21875eab75e783a8b1cefcc4"

RHWP_REPO = "https://github.com/edwardkim/rhwp.git"
# 고침을 만든 바탕 — devel 2026-10-09 (릴리스 0.8.7 이후의 수식·crop 정합 고침 포함)
RHWP_BASE = "21ed64071f81f35f37618717693bd30f727392c9"
# 빌드에 필요한 폴더만 받는다(include_bytes·벤더 패치, 그리고 작업공간 구성원의 Cargo.toml —
# cargo 는 빌드하지 않는 구성원도 매니페스트를 읽는다). 시험 문서 수백 MB 는 받지 않는다.
SPARSE = ["src", "crates", "vendor", "saved", "templates", "ttfs", "assets/logo",
          "mydocs/manual", "mydocs/tech", "samples/hml", "samples/hwpx", "tests/fixtures",
          "bindings/Native", "tools/batch-convert", "tools/llm_verifier", "tools/rhwp-subsecond"]


def say(msg: str = "") -> None:
    print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, sha: str | None) -> None:
    say(f"  → 받기: {url.rsplit('/', 1)[-1]}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if sha and sha256(tmp) != sha:
        tmp.unlink()
        raise SystemExit(f"  [오류] 지문이 다릅니다: {url}")
    os.replace(tmp, dest)


def run(args, desc: str, env=None, cwd=None) -> None:
    say(f"  → {desc}")
    p = subprocess.run([str(a) for a in args], env=env, cwd=cwd)
    if p.returncode != 0:
        raise SystemExit(f"  [오류] 실패({p.returncode}): {desc}")


def tool_env() -> dict:
    env = dict(os.environ)
    env["RUSTUP_HOME"] = str(BUILD / "rustup")
    env["CARGO_HOME"] = str(BUILD / "cargo")
    env["CARGO_TARGET_DIR"] = str(BUILD / "target")
    # 이 프로세스 안에서만 앞에 붙인다 — 시스템 PATH 는 그대로
    env["PATH"] = os.pathsep.join([str(BUILD / "mingw64" / "bin"), str(BUILD / "cargo" / "bin"), env.get("PATH", "")])
    return env


def ensure_rust(env) -> None:
    if (BUILD / "cargo" / "bin" / "cargo.exe").is_file():
        return
    say("\n[1/5] Rust 설치 (engine\\.build 안)")
    init = BUILD / "rustup-init.exe"
    with urllib.request.urlopen(RUSTUP_URL + ".sha256", timeout=60) as r:
        want = r.read().decode().split()[0]
    download(RUSTUP_URL, init, want)
    run([init, "-y", "--default-host", "x86_64-pc-windows-gnu", "--default-toolchain", RUST_TOOLCHAIN,
         "--profile", "minimal", "--no-modify-path"], f"Rust {RUST_TOOLCHAIN} (gnu)", env=env)


def ensure_mingw() -> None:
    if (BUILD / "mingw64" / "bin" / "dlltool.exe").is_file():
        return
    say("\n[2/5] MinGW-w64 (dlltool·gcc — windows-sys 와 blake3 가 쓴다)")
    z = BUILD / "winlibs.zip"
    download(WINLIBS_URL, z, WINLIBS_SHA256)
    say("  → 풀기")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(BUILD)
    z.unlink()


def ensure_source() -> Path:
    src = BUILD / "rhwp"
    git = shutil.which("git")
    if not git:
        raise SystemExit("  [오류] git 이 필요합니다 (https://git-scm.com).")
    say("\n[3/5] rhwp 소스")
    if not (src / ".git").is_dir():
        run([git, "-c", "core.autocrlf=false", "clone", "--filter=blob:none", "--sparse", "--no-checkout",
             RHWP_REPO, src], "받기 (필요한 폴더만)")
    run([git, "-C", src, "sparse-checkout", "set", *SPARSE], "폴더 고르기")
    run([git, "-C", src, "-c", "core.autocrlf=false", "checkout", "--force", RHWP_BASE], f"커밋 {RHWP_BASE[:7]}")
    run([git, "-C", src, "reset", "--hard", "--quiet"], "깨끗이")
    run([git, "-C", src, "apply", "--check", PATCH], "고침 확인")
    run([git, "-C", src, "apply", PATCH], "고침 적용")
    return src


def build(src: Path, env) -> Path:
    say("\n[4/5] 빌드 (처음엔 10~25분)")
    t = time.monotonic()
    # 실행 파일은 절대 경로로 준다 — Windows 의 subprocess 는 env 의 PATH 가 아니라 이 프로세스의
    # PATH 에서 실행 파일을 찾는다(env 의 PATH 는 cargo 가 부르는 gcc·dlltool 에만 쓰인다).
    cargo = BUILD / "cargo" / "bin" / "cargo.exe"
    run([cargo, "build", "--release", "--locked", "--bin", "rhwp"], "cargo build --release --bin rhwp",
        env=env, cwd=src)
    say(f"    {time.monotonic() - t:.0f}초")
    exe = BUILD / "target" / "release" / "rhwp.exe"
    if not exe.is_file():
        raise SystemExit("  [오류] 빌드 결과가 없습니다.")
    return exe


def install(exe: Path) -> None:
    say("\n[5/5] 확인과 설치")
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True)
    say(f"  → {out.stdout.strip()}")
    RHWP_DIR.mkdir(exist_ok=True)
    target = RHWP_DIR / "rhwp.exe"
    prev = RHWP_DIR / "rhwp.exe.prev"
    if target.is_file():
        shutil.copy2(target, prev)
    shutil.copy2(exe, target)
    lic = BUILD / "rhwp" / "LICENSE"
    if lic.is_file():
        shutil.copy2(lic, RHWP_DIR / "LICENSE")
    info = dict(
        base=f"edwardkim/rhwp@{RHWP_BASE}",
        patch=PATCH.name,
        patch_sha256=sha256(PATCH),
        toolchain=f"rust {RUST_TOOLCHAIN} x86_64-pc-windows-gnu",
        exe_sha256=sha256(target),
        built=time.strftime("%Y-%m-%d %H:%M"),
    )
    build_json = RHWP_DIR / "BUILD.json"
    build_prev = RHWP_DIR / "BUILD.json.prev"
    build_prev.unlink(missing_ok=True)
    if build_json.is_file():          # 되돌릴 때 엔진 지문이 바뀌지 않게 출처 표지도 함께 둔다
        shutil.copy2(build_json, build_prev)
    build_json.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    say("  → 합성 문서로 변환 시험")
    py = ENGINE_DIR / "venv" / "Scripts" / "python.exe"
    p = subprocess.run([str(py if py.is_file() else sys.executable), str(ENGINE_DIR / "종단_검증.py"), "--smoke"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        if prev.is_file():
            shutil.copy2(prev, target)
        if build_prev.is_file():
            shutil.copy2(build_prev, build_json)
        else:
            build_json.unlink(missing_ok=True)
        raise SystemExit("  [오류] 새 판이 변환 시험을 통과하지 못해 되돌렸습니다.\n" + p.stdout[-1500:] + p.stderr[-800:])
    say(f"  OK  설치: {target}")


def main() -> int:
    ap = argparse.ArgumentParser(description="고친 rhwp 를 소스에서 만든다")
    ap.add_argument("--keep", action="store_true", help="빌드 폴더(약 5GB)를 지우지 않는다")
    args = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows 전용입니다.")
    BUILD.mkdir(parents=True, exist_ok=True)
    say(f"빌드 폴더: {BUILD}")
    env = tool_env()
    ensure_rust(env)
    ensure_mingw()
    src = ensure_source()
    exe = build(src, env)
    install(exe)
    if args.keep:
        say(f"\n빌드 폴더를 남겼습니다: {BUILD} (다시 만들 때 빠르다)")
    else:
        say(f"\n빌드 폴더 정리: {BUILD}")
        remove_tree(BUILD)
    return 0


def remove_tree(path: Path) -> None:
    """git 의 pack 파일은 읽기 전용이라 그냥 지우면 남는다 — 속성을 풀고 다시 지운다."""
    import stat

    def retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onexc=retry)
    if path.exists():
        say(f"  ⚠ 일부를 지우지 못했습니다: {path}")


if __name__ == "__main__":
    sys.exit(main())

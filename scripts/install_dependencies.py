"""Install core dependencies, or opt into the historical Python 3.10 lock."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


def announce(language: str, english: str, chinese: str) -> None:
    """Tell an interactive installer which network/install phase is active."""
    print(chinese if language == "zh" else english, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('core', 'locked'), default='core')
    parser.add_argument('--language', choices=('en', 'zh'), default='en')
    args = parser.parse_args()
    if args.profile == 'locked' and sys.version_info[:2] != (3, 10):
        parser.error('历史锁定依赖需要 Python 3.10；更新的 Python 请使用 --profile core。'
                     if args.language == 'zh' else
                     'The historical lock requires Python 3.10. Use --profile core with newer Python.')
    root = Path(__file__).resolve().parents[1]
    announce(args.language, "Preparing dependency installation; this may take a few minutes.", "正在准备依赖安装，可能需要几分钟。")
    env = dict(os.environ)
    for scheme, value in urllib.request.getproxies().items():
        if scheme in ("http", "https"):
            env.setdefault(scheme.upper() + "_PROXY", value)
    # Python 3.10's bundled pip predates Windows trust-store support. The wheel comes
    # from PyPI over verified TLS and is checked against PyPI's published SHA256.
    announce(args.language, "Checking the official pip installer metadata on PyPI…", "正在检查 PyPI 上官方 pip 安装包的元数据…")
    metadata = json.load(
        urllib.request.urlopen("https://pypi.org/pypi/pip/26.2.1/json", timeout=30)
    )
    wheel = next(f for f in metadata["urls"] if f["filename"].endswith("py3-none-any.whl"))
    announce(args.language, "Downloading and verifying the official pip installer…", "正在下载并校验官方 pip 安装包…")
    content = urllib.request.urlopen(wheel["url"], timeout=60).read()
    if hashlib.sha256(content).hexdigest() != wheel["digests"]["sha256"]:
        raise RuntimeError("Official pip wheel digest mismatch")
    with tempfile.TemporaryDirectory(prefix="filingcopilot-install-") as folder:
        target = Path(folder) / wheel["filename"]
        target.write_bytes(content)
        announce(args.language, "Updating pip from the verified download; please wait…", "正在使用已校验的安装包更新 pip，请稍候…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--no-index", str(target)], cwd=root, env=env
        )
    if args.profile == 'core':
        announce(args.language, "Installing core dependencies and FilingCopilot…", "正在安装核心依赖和 FilingCopilot…")
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple', '-e', '.'],
            cwd=root, env=env,
        )
        announce(args.language, "Core installation complete.", "核心安装完成。")
        return
    announce(args.language, "Installing the locked development and embedding dependencies…", "正在安装锁定的开发和向量检索依赖…")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--index-url",
            "https://pypi.org/simple",
            "-r",
            "requirements-lock.txt",
        ],
        cwd=root,
        env=env,
    )
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--no-build-isolation", "--no-deps", "-e", "."],
        cwd=root,
        env=env,
    )
    announce(args.language, "Locked installation complete.", "锁定依赖安装完成。")


if __name__ == "__main__":
    main()

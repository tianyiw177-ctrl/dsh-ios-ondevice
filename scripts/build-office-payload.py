#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble the Office payload for the DSHIOS guest (linux/arm64/musl).

Reads office-payload.lock.json, downloads the pinned python archive + wheels
into a cache directory, and builds:

  <output>/primary-runtime/
      runtime.json
      dependencies/python/{bin,lib/...}      (astral standalone python, musl)
  <output>/office-skills/                    (skill assets from the npm package)

Usage:
  build-office-payload.py --output DIR [--lock FILE] [--cache DIR]
                          [--node-modules DIR] [--python-sha256 PRINT]

The payload is consumed in the guest via the dsh tool
`@deepseek-ai/dsh-tool-workspace-dependencies` (source = .../primary-runtime)
and `@deepseek-ai/dsh-skill-office`. Office document authoring (docx/xlsx/pptx)
works; LibreOffice-dependent rendering/PDF stays disabled on this platform.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile


def log(msg):
    print('[office-payload] %s' % msg, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, sha256):
    if not os.path.exists(dest) or (sha256 and sha256_file(dest) != sha256):
        log('下载 %s' % os.path.basename(dest))
        req = urllib.request.Request(url, headers={'User-Agent': 'dshios-office-payload'})
        tmp = dest + '.part'
        with urllib.request.urlopen(req, timeout=600) as r, open(tmp, 'wb') as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(tmp, dest)
    if sha256:
        got = sha256_file(dest)
        if got != sha256:
            raise SystemExit('sha256 不匹配: %s\n  期望 %s\n  实际 %s' % (dest, sha256, got))
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    ap.add_argument('--lock', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'office-payload.lock.json'))
    ap.add_argument('--cache', default=None)
    ap.add_argument('--node-modules', default=None, help='staged node_modules dir to lift office skill assets from')
    args = ap.parse_args()

    lock = json.load(open(args.lock, encoding='utf-8'))
    out = os.path.abspath(args.output)
    cache = os.path.abspath(args.cache) if args.cache else out + '-cache'
    os.makedirs(cache, exist_ok=True)
    if os.path.exists(out):
        shutil.rmtree(out)

    # ---- python ----
    py = lock['python']
    py_archive = download(py['url'], os.path.join(cache, os.path.basename(py['url']).replace('%2B', '+')), py.get('sha256') or None)
    log('python 档案 sha256: %s' % sha256_file(py_archive))
    runtime = os.path.join(out, 'primary-runtime')
    deps = os.path.join(runtime, 'dependencies')
    os.makedirs(deps, exist_ok=True)
    log('解压 python ...')
    with tarfile.open(py_archive, 'r:gz') as t:
        # 只要运行时需要的 bin/ 与 lib/：include/（C 头文件）与 share/（terminfo 等）
        # 既不需要，还可能撞上 Windows 非法文件名（terminfo 里有这类条目）。
        members = [m for m in t.getmembers() if m.name.startswith(('python/bin/', 'python/lib/'))]
        log('  取 %d 个成员（跳过 include/share）' % len(members))
        try:
            t.extractall(deps, members=members, filter='data')
        except TypeError:  # py < 3.12 has no filter kwarg
            t.extractall(deps, members=members)
    py_bin = os.path.join(deps, 'python', 'bin', 'python3')
    if not os.path.exists(py_bin):
        raise SystemExit('解压后没有找到 dependencies/python/bin/python3')

    # ---- wheels — 直接解压进 site-packages（wheel 就是 zip）----
    mm = '.'.join(py['version'].split('.')[:2])
    site = os.path.join(deps, 'python', 'lib', 'python' + mm, 'site-packages')
    os.makedirs(site, exist_ok=True)
    for w in lock['wheels']:
        whl = download(w['url'], os.path.join(cache, os.path.basename(w['url'])), w['sha256'])
        log('解包 %s' % os.path.basename(whl))
        with zipfile.ZipFile(whl) as z:
            z.extractall(site)

    # ---- 修剪 tests 目录（对齐官方 prune，省体积）----
    for pkg in ('numpy', 'pandas'):
        root = os.path.join(site, pkg)
        for dirpath, dirnames, _ in os.walk(root):
            for d in list(dirnames):
                if d == 'tests' or d == 'test':
                    shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                    dirnames.remove(d)

    # ---- 修剪 CPython 自带测试套件与 Tcl/Tk（Office 栈用不到，约省 50MB）----
    stdlib = os.path.join(deps, 'python', 'lib', 'python' + mm)
    for p in [os.path.join(stdlib, 'test'), os.path.join(stdlib, 'tkinter'),
              os.path.join(stdlib, 'idlelib')]:
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)
    pylib = os.path.join(deps, 'python', 'lib')
    for d in os.listdir(pylib):
        if d.startswith(('tcl', 'tk', 'itcl', 'thread', 'libtcl', 'libtk', 'libitcl')):
            p = os.path.join(pylib, d)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass
    dynload = os.path.join(stdlib, 'lib-dynload')
    if os.path.isdir(dynload):
        for f in os.listdir(dynload):
            if f.startswith('_tkinter'):
                os.remove(os.path.join(dynload, f))

    # ---- runtime.json ----
    manifest = {
        'desktopVersion': '0.2.0-rc.2',
        'platform': 'linux',
        'arch': 'arm64',
        'python': py['version'],
        'pythonPackages': lock['pythonPackages'],
    }
    with open(os.path.join(runtime, 'runtime.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write('\n')

    # ---- office-skills（从 npm 包 assets 拷贝到 payload 旁边）----
    if args.node_modules:
        src = os.path.join(args.node_modules, '@deepseek-ai', 'dsh-skill-office', 'assets')
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(out, 'office-skills'))
            log('office-skills 已拷贝')
        else:
            log('警告: 没找到 skill-office assets: %s' % src)

    log('完成。payload 大小: %.1f MB' % (sum(
        os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs) / 1048576))


if __name__ == '__main__':
    main()

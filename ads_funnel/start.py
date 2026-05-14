"""
start.py — 一键启动广告漏斗分析 v2.0
用法: python start.py
"""

import os, sys, time, subprocess, webbrowser
from pathlib import Path

HOST = '127.0.0.1'
PORT = 5001
URL  = f'http://{HOST}:{PORT}'

def check_deps():
    missing = []
    for pkg in ['fastapi', 'uvicorn', 'pandas', 'openpyxl', 'numpy']:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f'缺少依赖，正在安装: {", ".join(missing)}')
        subprocess.run([sys.executable, '-m', 'pip', 'install', *missing, '--break-system-packages'], check=True)
    # python-multipart 用于文件上传
    try:
        import multipart
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'python-multipart', '--break-system-packages'])

def main():
    # 切换到 ads_funnel/ 目录
    ads_funnel_dir = Path(__file__).parent
    os.chdir(ads_funnel_dir)

    print('=' * 50)
    print('  广告漏斗分析 v2.0')
    print('=' * 50)

    check_deps()

    print(f'\n启动服务: {URL}')
    print('按 Ctrl+C 停止\n')

    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'api.main:app',
         '--host', HOST, '--port', str(PORT), '--reload'],
        cwd=str(ads_funnel_dir)
    )

    time.sleep(1.8)
    webbrowser.open(URL)

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print('\n服务已停止。')

if __name__ == '__main__':
    main()

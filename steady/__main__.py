import argparse
import sys

from . import APP_NAME, VERSION


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} — 本地任务与进展管理")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    parser.add_argument("--data-dir", help="使用独立数据目录（默认遵循 XDG_DATA_HOME）")
    arguments = parser.parse_args()
    try:
        from .application import Application
    except (ImportError, ValueError) as error:
        print(f"无法加载 GTK/libadwaita：{error}\nUbuntu/Debian 请安装 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1，并使用 /usr/bin/python3 运行。", file=sys.stderr)
        return 1
    return Application(arguments.data_dir).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())

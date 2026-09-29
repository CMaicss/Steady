#!/usr/bin/python3
import argparse
import json
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def measure(directory):
    started = time.perf_counter()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from steady import application
    from gi.repository import Gdk, GLib

    timings = {"imports_ms": (time.perf_counter() - started) * 1000}
    original_store = application.Store

    def timed_store(path):
        before = time.perf_counter()
        store = original_store(path)
        timings["database_ms"] = (time.perf_counter() - before) * 1000
        return store

    application.Store = timed_store

    class MeasuredApplication(application.Application):
        def do_startup(self):
            before = time.perf_counter()
            super().do_startup()
            timings["framework_ms"] = (time.perf_counter() - before) * 1000

        def do_activate(self):
            before = time.perf_counter()
            super().do_activate()
            timings["window_ms"] = (time.perf_counter() - before) * 1000
            if type(self.window).__name__ != "MainWindow":
                raise RuntimeError("应用未能打开主窗口")
            self.paint_handler = self.window.get_frame_clock().connect("after-paint", self.painted)

        def painted(self, clock):
            clock.disconnect(self.paint_handler)
            timings["first_frame_ms"] = (time.perf_counter() - started) * 1000
            timings["backend"] = type(Gdk.Display.get_default()).__name__
            timings["renderer"] = type(self.window.get_renderer()).__name__
            print(json.dumps(timings), flush=True)
            GLib.idle_add(lambda: (self.window.close(), False)[1])

    app = MeasuredApplication(directory, non_unique=True)
    GLib.timeout_add_seconds(30, lambda: (app.quit(), False)[1])
    app.run([])


def benchmark(runs, database):
    records = []
    with tempfile.TemporaryDirectory(prefix="steady-startup-") as directory:
        if database:
            source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            target = sqlite3.connect(Path(directory) / "tasks.sqlite3")
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        for number in range(runs):
            result = subprocess.run([sys.executable, __file__, "--child", directory], capture_output=True, text=True, timeout=40, check=True)
            record = json.loads(result.stdout)
            records.append(record)
            print(json.dumps({"run": number + 1, **record}), flush=True)
    medians = {key: round(statistics.median(record[key] for record in records), 1) for key in records[0] if key.endswith("_ms")}
    print(json.dumps({"median": medians, "backend": records[0]["backend"], "renderer": records[0]["renderer"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测量新进程到完整窗口首帧的耗时，数据库在隔离目录中运行")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--database", type=Path, help="只读复制指定数据库用于测试，不修改原数据库")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.child:
        measure(arguments.child)
    elif arguments.runs < 1:
        parser.error("--runs 必须大于零")
    else:
        benchmark(arguments.runs, arguments.database)

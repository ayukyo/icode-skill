"""DirLock 跨进程互斥验证(两个独立 Python 子进程,无 fd 继承关系)。

验证 _acquire_exclusive_lock 在 Windows msvcrt fallback 下:
  - 两个独立进程竞争同一锁文件,后者必须阻塞等待
  - 锁释放后后者立即获得锁
  - 整个过程不抛 Resource deadlock avoided

POSIX 路径(fcntl.flock)由 Linux/macOS CI 覆盖,本测试仅关注 Windows。
"""
import sys, os, time, subprocess, tempfile, pathlib

LOCK_DIR_NAME = '_dirlock_mutex_test'
SCRIPT = r'D:\AI_CODING\icode-skill\tests\test_dirlock_mutex_windows.py'
TOOLS = r'D:\AI_CODING\icode-skill\tools'


def child_acquire(lock_path_str: str, waited_file_str: str, hold_seconds: str) -> int:
    """子进程入口:acquire DirLock,持锁 hold_seconds 秒,记录自身等待时长。"""
    sys.path.insert(0, TOOLS)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'icode_control', os.path.join(TOOLS, 'icode_control.py')
    )
    ic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ic)
    start = time.time()
    with ic.DirLock(pathlib.Path(lock_path_str)):
        waited = time.time() - start
        # 在持锁期间 sleep(模拟 vNext 真实原子写入耗时)
        time.sleep(float(hold_seconds))
    pathlib.Path(waited_file_str).write_text(
        f'waited={waited:.3f}s', encoding='utf-8'
    )
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        lock_dir = os.path.join(td, LOCK_DIR_NAME)
        os.makedirs(lock_dir)
        # 启动子进程 A:它会先持锁 1 秒后释放
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        waited_file = os.path.join(td, '_a_wait.txt')
        # 子进程 A:acquire 锁后持锁 1.0 秒
        proc_a = subprocess.Popen(
            [sys.executable, SCRIPT, 'child', lock_dir, waited_file, '1.0'],
            env=env,
        )
        # 让 A 先 acquire(0.3s 已足够)
        time.sleep(0.3)
        # 子进程 B:应被 A 阻塞,等 A 释放后才能 acquire
        waited_file_b = os.path.join(td, '_b_wait.txt')
        start_b = time.time()
        proc_b = subprocess.Popen(
            [sys.executable, SCRIPT, 'child', lock_dir, waited_file_b, '0.3'],
            env=env,
        )
        proc_a.wait(timeout=15)
        proc_b.wait(timeout=15)
        elapsed_b = time.time() - start_b
        a_text = pathlib.Path(waited_file).read_text(encoding='utf-8')
        b_text = pathlib.Path(waited_file_b).read_text(encoding='utf-8')
        print(f'A waited: {a_text}')
        print(f'B waited: {b_text} (wall={elapsed_b:.3f}s)')
        # A 自己 acquire 应该几乎不等待(没人竞争)
        a_waited = float(a_text.split('=')[1].rstrip('s'))
        b_waited = float(b_text.split('=')[1].rstrip('s'))
        if a_waited > 0.5:
            print(f'FAIL: A waited {a_waited:.3f}s (should be ~0)')
            return 1
        # B 应该等 A 完成(1s)+ 自己短暂等待
        if b_waited < 0.5:
            print(f'FAIL: B waited {b_waited:.3f}s (should be ~1s+ to wait for A)')
            return 1
        print(f'PASS: A={a_waited:.3f}s B={b_waited:.3f}s (mutex enforced)')
        return 0


if __name__ == '__main__':
    if len(sys.argv) >= 5 and sys.argv[1] == 'child':
        sys.exit(child_acquire(sys.argv[2], sys.argv[3], sys.argv[4]))
    sys.exit(main())
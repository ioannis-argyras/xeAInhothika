"""Server δοκιμής: τρέχει την εφαρμογή τοπικά, χωρίς κωδικό, χωρίς κλειδιά, με ψεύτικο Claude.

Χρήση, από τη ρίζα του repo:
    .venv\\Scripts\\python.exe scripts\\test_server.py [--port 8090] [--fresh] [--keep] [--autorun]

Τι κάνει:
  * Ανοίγει την εφαρμογή στο http://127.0.0.1:<θύρα>/ και ΔΕΝ ζητά κωδικό ομάδας.
  * Δουλεύει σε ΑΝΤΙΓΡΑΦΟ της βάσης (data/xangx.sqlite) και των αρχείων του demo (φωτογραφίες, PDF),
    στον φάκελο .tmp/test-server-<θύρα>/. Η πραγματική βάση δεν αλλάζει ποτέ (ανοίγει μόνο για ανάγνωση).
  * Δεν διαβάζει το .env και αφαιρεί από το περιβάλλον κάθε κλειδί: δεν υπάρχει Claude, email,
    Telegram ή myDATA. Ο Claude είναι ψεύτικος (XANGX_FAKE_LLM=1).
  * Με Ctrl+C κλείνει ο server και σβήνεται ο φάκελος δοκιμής.

Επιλογές:
  --port N     θύρα (προεπιλογή 8090). Η 8010 είναι το demo και απαγορεύεται.
  --fresh      νέα βάση από το scripts/seed_demo.py, αντί για αντίγραφο της τρέχουσας.
  --keep       μετά το κλείσιμο ο φάκελος δοκιμής ΜΕΝΕΙ (π.χ. για να δεις τη βάση).
  --autorun    ανοίγει το ρολόι της εφαρμογής (έλεγχος 07:00, ξύπνημα του συνεργάτη). Χωρίς αυτό
               το ρολόι είναι κλειστό (XANGX_AUTORUN=0), ώστε να μη γράφονται γεγονότα μόνα τους.
"""

import argparse
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_PORT = 8010
SECRET_NAMES = (
    "ANTHROPIC_API_KEY",
    "XANGX_ACCESS_CODE",
    "XANGX_SESSION_SECRET",
    "XANGX_MCP_TOKEN",
    "XANGX_DEMO_SUPPLIER_EMAIL",
)
SECRET_PREFIXES = ("MYDATA_", "TELEGRAM_", "SMTP_", "IMAP_")
# Φάκελοι του data/ που αντιγράφονται (μόνο ανάγνωση της πηγής). Το inbox δεν αντιγράφεται:
# ο worker των τιμολογίων θα τα έβαζε ξανά στη βάση.
COPY_DIRS = ("files", "camera", "demo_invoices")


def build_env(base, port, db_path, data_dir, autorun=False):
    """Το περιβάλλον του server δοκιμής: το `base` χωρίς μυστικά, και οι ρυθμίσεις δοκιμής."""
    env = dict(base)
    for name in list(env):
        upper = name.upper()
        if upper in SECRET_NAMES or upper.startswith(SECRET_PREFIXES):
            del env[name]
    env["XANGX_FAKE_LLM"] = "1"
    env["XANGX_PORT"] = str(port)
    env["XANGX_DB"] = db_path
    env["XANGX_DATA_DIR"] = data_dir
    env["XANGX_FILES"] = os.path.join(data_dir, "files")
    env["XANGX_AUTORUN"] = "1" if autorun else "0"
    env["PYTHONPATH"] = ROOT
    return env


def check_port(port):
    """Σταματά αν η θύρα είναι η 8010 (το demo)."""
    if port == DEMO_PORT:
        raise SystemExit("Η θύρα 8010 είναι το demo και δεν επιτρέπεται. Διάλεξε άλλη, π.χ. --port 8090.")


def port_is_free(port):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def copy_db(src_path, dst_path):
    """Αντίγραφο της βάσης με το backup API της sqlite3 (σωστό και με WAL). Η πηγή μόνο για ανάγνωση."""
    uri = "file:" + src_path.replace("\\", "/") + "?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def copy_files(data_src, data_dst):
    for name in COPY_DIRS:
        source = os.path.join(data_src, name)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(data_dst, name), dirs_exist_ok=True)
    os.makedirs(os.path.join(data_dst, "files"), exist_ok=True)


def wait_until_up(port, proc, seconds=30):
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except urllib.error.HTTPError:
            pass
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="Server δοκιμής: η εφαρμογή τοπικά, χωρίς κωδικό, χωρίς κλειδιά, με ψεύτικο Claude, "
        "σε αντίγραφο της βάσης. Η πραγματική βάση και η θύρα 8010 δεν αγγίζονται.",
    )
    parser.add_argument("--port", type=int, default=8090, help="θύρα (προεπιλογή 8090· η 8010 απαγορεύεται)")
    parser.add_argument("--fresh", action="store_true", help="νέα βάση από το scripts/seed_demo.py αντί για αντίγραφο")
    parser.add_argument("--keep", action="store_true", help="μετά το κλείσιμο ο φάκελος δοκιμής μένει")
    parser.add_argument(
        "--autorun",
        action="store_true",
        help="ανοίγει το ρολόι της εφαρμογής (έλεγχος 07:00, ξύπνημα συνεργάτη)· χωρίς αυτό είναι κλειστό "
        "και δεν γράφονται γεγονότα μόνα τους",
    )
    args = parser.parse_args(argv)
    check_port(args.port)
    # Κλείσιμο και με Ctrl+Break / σήμα τερματισμού: ίδια διαδρομή με το Ctrl+C (σβήνει ο φάκελος).
    for sig_name in ("SIGBREAK", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, signal.default_int_handler)
            except ValueError:
                pass  # όχι στο κύριο thread (π.χ. μέσα σε τεστ)
    if not port_is_free(args.port):
        print(f"Η θύρα {args.port} είναι ήδη πιασμένη. Διάλεξε άλλη με --port.")
        return 1

    work = os.path.join(ROOT, ".tmp", f"test-server-{args.port}")
    if os.path.isdir(work):
        shutil.rmtree(work)  # δικός μας φάκελος από προηγούμενη δοκιμή στην ίδια θύρα
    os.makedirs(work)
    db_path = os.path.join(work, "xangx.sqlite")
    env = build_env(os.environ, args.port, db_path, work, autorun=args.autorun)
    proc = None
    log = None
    try:
        source_db = os.path.join(ROOT, "data", "xangx.sqlite")
        if args.fresh or not os.path.isfile(source_db):
            if not args.fresh:
                print("Δεν βρέθηκε η βάση data/xangx.sqlite: φτιάχνω νέα από το seed_demo.")
            seeded = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py")],
                cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if seeded.returncode != 0:
                print("Το seed_demo απέτυχε:\n" + (seeded.stdout + seeded.stderr).strip())
                return 1
        else:
            copy_db(source_db, db_path)
        copy_files(os.path.join(ROOT, "data"), work)

        log = open(os.path.join(work, "server.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-m", "xangx.server"],
            cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        if not wait_until_up(args.port, proc):
            print(f"Ο server δεν απάντησε σε 30 δευτερόλεπτα. Δες το {os.path.join(work, 'server.log')}")
            args.keep = True
            return 1
        print(
            f"Server δοκιμής στο http://127.0.0.1:{args.port}/ · χωρίς κωδικό, χωρίς κλειδιά, ψεύτικο Claude · "
            f"βάση: {'νέα' if args.fresh else 'αντίγραφο'} · Ctrl+C για κλείσιμο"
        )
        sys.stdout.flush()
        proc.wait()
        return proc.returncode or 0
    except KeyboardInterrupt:
        print("\nΚλείνω τον server δοκιμής.")
        return 0
    finally:
        if proc is not None:
            stop(proc)
        if log is not None:
            log.close()
        if args.keep:
            print(f"Ο φάκελος δοκιμής έμεινε: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

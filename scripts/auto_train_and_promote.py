import os
import json
import shutil
import subprocess
import argparse
from datetime import datetime


def read_metrics(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def copy_files(src_dir: str, dst_dir: str, files):
    os.makedirs(dst_dir, exist_ok=True)
    for fn in files:
        src = os.path.join(src_dir, fn)
        if not os.path.exists(src):
            raise FileNotFoundError(f"Brak pliku {src}")
        shutil.copy2(src, os.path.join(dst_dir, fn))


def promote(run_dir: str, current_dir: str, prev_dir: str):
    os.makedirs(current_dir, exist_ok=True)
    os.makedirs(prev_dir, exist_ok=True)

    artifacts = ["tcn_model.keras", "scaler.joblib", "metrics.json"]

    # backup current -> prev/timestamp
    if os.path.exists(os.path.join(current_dir, "tcn_model.keras")):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prev_target = os.path.join(prev_dir, stamp)
        copy_files(current_dir, prev_target, artifacts)

    # candidate -> current
    copy_files(run_dir, current_dir, artifacts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", required=True, help="Katalog z plikami boiler_*.csv")
    ap.add_argument("--output_dir", required=True, help="Katalog bazowy na runs/ i models/")
    ap.add_argument("--glob", default="boiler_*.csv", help="Wzorzec plików w input_dir")
    ap.add_argument("--last_hours", type=int, default=24 * 14, help="Ile ostatnich godzin (plików) użyć do treningu")
    ap.add_argument("--dt", default="30S", help="Resampling, np. 30S, 20S")
    ap.add_argument("--seq_minutes", type=int, default=60, help="Długość okna historii w minutach")
    ap.add_argument("--improvement_factor", type=float, default=0.98,
                    help="Kandydat promowany gdy rmse10 <= current_rmse10 * improvement_factor (np. 0.95 = 5% lepiej)")
    ap.add_argument("--max_abs_err_10m", type=float, default=2.5,
                    help="Bezpiecznik: jeśli max_abs_err_10m kandydata > próg, nie promuj")
    ap.add_argument("--train_script", default="train_tcn_boiler_dir.py",
                    help="Ścieżka do skryptu treningu (train_tcn_boiler_dir.py)")
    ap.add_argument("--python", default="python", help="Interpreter Pythona (np. /usr/bin/python3)")
    args = ap.parse_args()

    runs_dir = os.path.join(args.output_dir, "runs")
    current_dir = os.path.join(args.output_dir, "models", "current")
    prev_dir = os.path.join(args.output_dir, "models", "prev")

    os.makedirs(runs_dir, exist_ok=True)
    os.makedirs(current_dir, exist_ok=True)
    os.makedirs(prev_dir, exist_ok=True)

    run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(runs_dir, run_name)
    os.makedirs(outdir, exist_ok=True)

    # 1) trenuj kandydata
    cmd = [
        args.python, args.train_script,
        "--data_dir", args.input_dir,
        "--glob", args.glob,
        "--last_hours", str(args.last_hours),
        "--dt", args.dt,
        "--seq_minutes", str(args.seq_minutes),
        "--outdir", outdir,
    ]
    print("RUN:", " ".join(cmd))
    subprocess.check_call(cmd)

    cand = read_metrics(os.path.join(outdir, "metrics.json"))
    curr = read_metrics(os.path.join(current_dir, "metrics.json"))

    if cand is None:
        print("Brak metrics.json kandydata – nie promuję.")
        return

    # jeśli nie ma current, promuj od razu
    if curr is None:
        print("Brak current modelu – promuję pierwszego kandydata.")
        promote(outdir, current_dir, prev_dir)
        return

    cand_rmse10 = cand.get("rmse_10m")
    curr_rmse10 = curr.get("rmse_10m")
    cand_maxerr = cand.get("max_abs_err_10m", 999)

    if cand_rmse10 is None or curr_rmse10 is None:
        print("Brak rmse_10m w metrykach – nie promuję.")
        return

    if cand_maxerr > args.max_abs_err_10m:
        print(f"Nie promuję: cand max_abs_err_10m={cand_maxerr:.2f} > {args.max_abs_err_10m}")
        return

    if cand_rmse10 <= curr_rmse10 * args.improvement_factor:
        print(f"Promuję: rmse10 cand={cand_rmse10:.3f} vs curr={curr_rmse10:.3f}")
        promote(outdir, current_dir, prev_dir)
    else:
        print(f"Nie promuję: rmse10 cand={cand_rmse10:.3f} vs curr={curr_rmse10:.3f}")


if __name__ == "__main__":
    main()

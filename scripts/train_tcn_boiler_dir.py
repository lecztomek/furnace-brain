import argparse
import glob
import json
import os
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import joblib
import tensorflow as tf
from tensorflow.keras import layers, Model


@dataclass
class Config:
    dt: str = "30S"
    seq_minutes: int = 60
    horizons_minutes: tuple = (5, 10)
    train_frac: float = 0.70
    val_frac: float = 0.15
    batch_size: int = 64
    max_epochs: int = 50
    patience: int = 5
    lr: float = 1e-3

    # TCN
    n_filters: int = 32
    kernel_size: int = 3
    dilations: tuple = (1, 2, 4, 8, 16, 32, 64)
    dropout: float = 0.10


def load_one_csv(path: str) -> pd.DataFrame:
    # oczekiwane: data_czas;temp_pieca;power;temp_grzejnikow;temp_spalin;tryb_pracy
    df = pd.read_csv(path, sep=";")
    df["data_czas"] = pd.to_datetime(df["data_czas"])
    df = df.set_index("data_czas").sort_index()
    return df


def load_many(files: list[str]) -> pd.DataFrame:
    dfs = []
    for f in files:
        try:
            dfs.append(load_one_csv(f))
        except Exception as e:
            print(f"[WARN] Pomijam {f}: {e}")
    if not dfs:
        raise RuntimeError("Brak poprawnych plików CSV do wczytania.")
    df = pd.concat(dfs).sort_index()
    return df


def prepare(df: pd.DataFrame, dt: str) -> pd.DataFrame:
    # filtr trybu
    if "tryb_pracy" in df.columns:
        df = df[df["tryb_pracy"] == "WORK"].copy()

    # resampling
    df = df.resample(dt).mean(numeric_only=True)
    df = df.interpolate(limit=2)

    # pochodne
    base_cols = ["temp_pieca", "temp_spalin", "temp_grzejnikow", "power"]
    for col in base_cols:
        if col in df.columns:
            df[f"d_{col}"] = df[col].diff().fillna(0.0)

    df = df.dropna()

    # sanity
    required = {"temp_pieca", "temp_spalin", "temp_grzejnikow", "power"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Brakuje kolumn: {missing}. Masz: {list(df.columns)}")

    return df


def make_windows(X: np.ndarray, y: np.ndarray, seq_len: int, horizons: list[int], stride: int = 1):
    Xs, Ys = [], []
    max_h = max(horizons)
    N = len(X)
    for i in range(0, N - seq_len - max_h, stride):
        xw = X[i:i + seq_len]
        yw = [y[i + seq_len + h] for h in horizons]
        Xs.append(xw)
        Ys.append(yw)
    return np.asarray(Xs, dtype=np.float32), np.asarray(Ys, dtype=np.float32)


def tcn_block(x, n_filters, kernel_size, dilation, dropout):
    shortcut = x

    x = layers.Conv1D(n_filters, kernel_size, padding="causal", dilation_rate=dilation)(x)
    x = layers.ReLU()(x)
    x = layers.Dropout(dropout)(x)

    x = layers.Conv1D(n_filters, kernel_size, padding="causal", dilation_rate=dilation)(x)
    x = layers.ReLU()(x)
    x = layers.Dropout(dropout)(x)

    if shortcut.shape[-1] != n_filters:
        shortcut = layers.Conv1D(n_filters, 1, padding="same")(shortcut)

    return layers.Add()([x, shortcut])


def build_tcn(seq_len: int, n_features: int, out_dim: int, cfg: Config) -> Model:
    inp = layers.Input(shape=(seq_len, n_features))
    x = inp

    for d in cfg.dilations:
        x = tcn_block(x, cfg.n_filters, cfg.kernel_size, d, cfg.dropout)

    x = layers.Lambda(lambda t: t[:, -1, :])(x)
    out = layers.Dense(out_dim)(x)

    model = Model(inp, out)
    model.compile(optimizer=tf.keras.optimizers.Adam(cfg.lr),
                  loss=tf.keras.losses.Huber())
    return model


def rmse(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(np.sqrt(np.mean((a - b) ** 2)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data_dir", required=True, help="Katalog z boiler_YYYYMMDD_HH.csv")
    p.add_argument("--glob", default="boiler_*.csv", help="Wzorzec plików")
    p.add_argument("--last_hours", type=int, default=24*14, help="Ile ostatnich godzin wziąć (np. 336=14 dni)")
    p.add_argument("--outdir", required=True, help="Gdzie zapisać artefakty runu")
    p.add_argument("--dt", default="30S")
    p.add_argument("--seq_minutes", type=int, default=60)
    args = p.parse_args()

    cfg = Config(dt=args.dt, seq_minutes=args.seq_minutes)

    os.makedirs(args.outdir, exist_ok=True)

    # wybór ostatnich plików godzinnych
    files = sorted(glob.glob(os.path.join(args.data_dir, args.glob)))
    if not files:
        raise RuntimeError("Nie znaleziono plików w data_dir.")

    # bierzemy ostatnie N godzin = ostatnie N plików (zakładamy 1 plik = 1 godzina)
    files_sel = files[-args.last_hours:]
    print(f"Uczę na {len(files_sel)} plikach (ostatnie {args.last_hours}h).")

    df = load_many(files_sel)
    df = prepare(df, cfg.dt)

    features = [
        "temp_pieca", "temp_spalin", "power", "temp_grzejnikow",
        "d_temp_pieca", "d_temp_spalin", "d_power", "d_temp_grzejnikow",
    ]

    X_raw = df[features].values
    y_raw = df["temp_pieca"].values

    dt_seconds = int(pd.Timedelta(cfg.dt).total_seconds())
    seq_len = int(cfg.seq_minutes * 60 / dt_seconds)
    horizons = [int(m * 60 / dt_seconds) for m in cfg.horizons_minutes]

    n = len(df)
    i_train = int(cfg.train_frac * n)
    i_val = int((cfg.train_frac + cfg.val_frac) * n)

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_raw[:i_train])
    X_val = scaler.transform(X_raw[i_train:i_val])
    X_test = scaler.transform(X_raw[i_val:])

    joblib.dump(scaler, os.path.join(args.outdir, "scaler.joblib"))

    Xtr, Ytr = make_windows(X_train, y_raw[:i_train], seq_len, horizons, stride=1)
    Xva, Yva = make_windows(X_val, y_raw[i_train:i_val], seq_len, horizons, stride=1)
    Xte, Yte = make_windows(X_test, y_raw[i_val:], seq_len, horizons, stride=1)

    print(f"dt={cfg.dt} seq_len={seq_len} (~{cfg.seq_minutes}min) horizons={cfg.horizons_minutes} -> {horizons} kroków")
    print(f"Windows: train={len(Xtr)} val={len(Xva)} test={len(Xte)}")

    model = build_tcn(seq_len, len(features), out_dim=len(horizons), cfg=cfg)

    es = tf.keras.callbacks.EarlyStopping(patience=cfg.patience, restore_best_weights=True)
    model.fit(Xtr, Ytr, validation_data=(Xva, Yva),
              epochs=cfg.max_epochs, batch_size=cfg.batch_size,
              callbacks=[es], verbose=1)

    # metryki na teście
    Yhat = model.predict(Xte, verbose=0)
    rmse_5 = rmse(Yhat[:, 0], Yte[:, 0])
    rmse_10 = rmse(Yhat[:, 1], Yte[:, 1])
    max_abs_10 = float(np.max(np.abs(Yhat[:, 1] - Yte[:, 1])))

    metrics = {
        "dt": cfg.dt,
        "seq_minutes": cfg.seq_minutes,
        "horizons_minutes": list(cfg.horizons_minutes),
        "rmse_5m": rmse_5,
        "rmse_10m": rmse_10,
        "max_abs_err_10m": max_abs_10,
        "trained_on_files": len(files_sel),
        "trained_on_rows": int(len(df)),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(os.path.join(args.outdir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

    with open(os.path.join(args.outdir, "config.json"), "w") as f:
        json.dump(vars(cfg), f, indent=2)

    model.save(os.path.join(args.outdir, "tcn_model.keras"))
    print("Zapisano run:", args.outdir)
    print("METRYKI:", metrics)


if __name__ == "__main__":
    main()

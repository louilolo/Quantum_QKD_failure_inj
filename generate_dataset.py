"""
generate_dataset.py
====================
Consolida todos os CSVs de simulação em um único dataset para ML.

Uso:
    python generate_dataset.py [--data_dir ./data] [--output ./data/dataset_full.csv]
"""

import argparse
import glob
import pandas as pd
from pathlib import Path


FAULT_LABELS = {
    "normal":    0,
    "qber":      1,   # intercept-resend / eavesdropping
    "degrade":   2,   # degradação gradual do canal
    "node_fail": 3,   # falha de trusted node
    "blinding":  4,   # blinding attack
    "trojan":    5,   # trojan horse
}

# Arquivos esperados: exatamente um por tipo de falha
EXPECTED_FILES = {f"dataset_{name}.csv" for name in FAULT_LABELS}


def load_and_label(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    stem = Path(csv_path).stem  # ex: "dataset_qber"
    for fault_name, fault_id in FAULT_LABELS.items():
        if stem == f"dataset_{fault_name}":   # match exato, não substring
            df["fault_id"]   = fault_id
            df["fault_name"] = fault_name
            return df
    # fallback: normal
    df["fault_id"]   = 0
    df["fault_name"] = "normal"
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona features derivadas para detecção de falhas."""
    df = df.sort_values(["fault_name", "link", "timestamp_ps"]).reset_index(drop=True)

    grp = df.groupby(["fault_name", "link"])

    # QBER: delta, média móvel, variância local
    df["qber_delta"] = grp["qber"].diff().fillna(0)
    df["qber_ma5"]   = grp["qber"].transform(lambda x: x.rolling(5, min_periods=1).mean())
    df["qber_var5"]  = grp["qber"].transform(lambda x: x.rolling(5, min_periods=1).var().fillna(0))

    # Key rate: queda relativa
    df["key_rate_drop"] = grp["key_rate_sifted"].transform(
        lambda x: x.pct_change().fillna(0).clip(-1, 1)
    )

    # Detector: delta no dark count rate (assinatura blinding)
    df["dark_count_delta"] = grp["dark_count_rate"].diff().fillna(0)

    # Potência reversa: flag binária (assinatura trojan horse)
    df["back_reflection_alert"] = (df["back_reflection_power"] > 1e-6).astype(int)

    # QBER threshold clássico (Tokyo QKD KMS usa 5%)
    df["qber_alert"] = (df["qber"] > 0.05).astype(int)

    return df


def main(data_dir: str, output: str):
    # Coleta apenas os arquivos esperados — ignora dataset_full.csv e outros
    all_csvs  = glob.glob(f"{data_dir}/dataset_*.csv")
    valid_csvs = [c for c in all_csvs
                  if Path(c).name in EXPECTED_FILES]
    skipped    = [Path(c).name for c in all_csvs if Path(c).name not in EXPECTED_FILES]

    if skipped:
        print(f"[!] Ignorados (não esperados): {skipped}")
    if not valid_csvs:
        print(f"Nenhum CSV válido encontrado em: {data_dir}")
        print(f"Esperados: {sorted(EXPECTED_FILES)}")
        return

    print(f"Carregando {len(valid_csvs)} arquivos:")
    frames = []
    for c in sorted(valid_csvs):
        df = load_and_label(c)
        print(f"  {Path(c).name:35s} → {len(df):4d} registros  label={df['fault_name'].iloc[0]}")
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    print("\nEngenharia de features...")
    df = engineer_features(df)

    print("\n=== Distribuição de labels ===")
    print(df["fault_name"].value_counts())
    print(f"\nTotal de registros : {len(df)}")
    print(f"Features           : {list(df.columns)}")

    missing = EXPECTED_FILES - {Path(c).name for c in valid_csvs}
    if missing:
        print(f"\n[!] Faltando simulações: {sorted(missing)}")
        print("    Rode setup_and_run.sh para gerar todos os cenários.")

    df.to_csv(output, index=False)
    print(f"\n[✓] Dataset consolidado salvo em: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", default="./data")
    parser.add_argument("--output",   default="./data/dataset_full.csv")
    args = parser.parse_args()
    main(args.data_dir, args.output)

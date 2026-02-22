#!/bin/bash
# ==============================================
#  Tokyo QKD Network — Setup & Execução completa
#  Compatível com Python 3.11+
# ==============================================

set -e

echo " 1. Criar o Virtual environment "
python3 -m venv venv_qkd
source venv_qkd/bin/activate
echo "Python: $(python --version)"

echo " 2. Dependências "
pip install --upgrade pip
pip install -r requirements.txt
python -c "import sequence; print('[OK] SeQUeNCe', sequence.__version__)"

echo " 3. Estrutura de diretórios "
mkdir -p dataset/data
mkdir -p notebooks

echo " 4. Simulações "

run_sim() {
    local fault=$1
    local out="dataset/data/dataset_${fault}.csv"
    echo ""
    echo "--- fault: $fault ---"
    python simulation/tokyo_qkd_simulation.py \
        --fault "$fault" \
        --output "$out" \
        --duration 1e12 \
        --samples 100 \
        --ls-freq 1e6
}

run_sim normal
run_sim qber
run_sim degrade
run_sim node_fail
run_sim blinding
run_sim trojan

echo " 5. Consolidando dataset "
python dataset/generate_dataset.py \
    --data_dir dataset/data \
    --output dataset/data/dataset_full.csv

echo ""
echo "=== Concluído! ==="
ls -lh dataset/data/

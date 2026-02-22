# Tokyo QKD Network — Fault Detection Dataset

Simulação da rede **Tokyo QKD Network** (Sasaki et al., 2011) usando o simulador [SeQUeNCe](https://github.com/sequence-toolbox/SeQUeNCe) para geração de datasets de detecção de falhas em redes de distribuição quântica de chaves (QKD).

## Estrutura do projeto

```
tokyo-qkd-fault-detection/
│
├── simulation/
│   ├── tokyo_qkd_simulation.py     # Script principal de simulação
│   └── tokyo_qkd_topology.json     # Topologia da rede (metadados + configuração)
│
├── dataset/
│   ├── generate_dataset.py         # Consolida CSVs e aplica feature engineering
│   └── data/                       # CSVs gerados (ignorados pelo Git — ver .gitignore)
│       ├── dataset_normal.csv
│       ├── dataset_qber.csv
│       ├── dataset_degrade.csv
│       ├── dataset_node_fail.csv
│       ├── dataset_blinding.csv
│       ├── dataset_trojan.csv
│       └── dataset_full.csv        # Dataset consolidado final com 6 classes
│
├── notebooks/                      # Análise exploratória e modelos de detecção
│   └── adicionar futuramente notebooks com o modelo de ML 
│
├── setup_and_run.sh                # Script de setup e execução completa
├── requirements.txt
└── README.md
```

## Topologia

Baseada na rede real do **Tokyo QKD Network** (2010), com 5 nós e 4 links cobrindo ~30,2 km:

```
Koganei_A ──(7 km, aérea)──► Koganei_B ──(13 km, enterrada)──► Otemachi
                                                                      │
                                                               (6 km, enterrada)
                                                                      │
                                                                   Hakusan
                                                                      │
                                                               (4.2 km, enterrada)
                                                                      │
                                                                    Hongo
```

**Protocolo:** BB84 com Decoy State  
**Detector:** SSPD (η=0.80, dark count=100/s)  
**Canal:** SMF-28 ULL, 0.2 dB/km, 1290 nm (O-band)

## Cenários de falha

| ID | Label | Tipo | Assinatura principal | Detecção |
|---|---|---|---|---|
| 0 | `normal` | Baseline | — | — |
| 1 | `qber` | Intercept-resend | QBER 2.7% → 25% | `qber > 0.05` |
| 2 | `degrade` | Degradação gradual | Key rate cai progressivamente | Análise de tendência |
| 3 | `node_fail` | Falha de trusted node | Key rate → 0 em links afetados | Ausência de tráfego |
| 4 | `blinding` | Blinding attack | QBER normal, `dark_count_rate` explode | Contagens absolutas |
| 5 | `trojan` | Trojan Horse | `back_reflection_power` anômalo | Monitor de potência reversa |

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/Talytp/Quantum_QKD_failure_inj.git
cd tokyo-qkd-fault-detection

# 2. Crie o virtual environment
python3 -m venv venv_qkd
source venv_qkd/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt
```

## Uso

### Gerar todos os datasets 
```bash
bash setup_and_run.sh
```

### Gerar um cenário específico (demoram mais ou menos 15 min. cada um)
```bash
python simulation/tokyo_qkd_simulation.py --fault normal   --output dataset/data/dataset_normal.csv
python simulation/tokyo_qkd_simulation.py --fault qber     --output dataset/data/dataset_qber.csv
python simulation/tokyo_qkd_simulation.py --fault degrade  --output dataset/data/dataset_degrade.csv
python simulation/tokyo_qkd_simulation.py --fault node_fail --output dataset/data/dataset_node_fail.csv
python simulation/tokyo_qkd_simulation.py --fault blinding --output dataset/data/dataset_blinding.csv
python simulation/tokyo_qkd_simulation.py --fault trojan   --output dataset/data/dataset_trojan.csv
```

### Parâmetros opcionais
```bash
# Simulação rápida para testes
python simulation/tokyo_qkd_simulation.py --fault normal \
  --duration 1e10 --samples 10 --ls-freq 1e5

# Simulação completa (realista)
python simulation/tokyo_qkd_simulation.py --fault normal \
  --duration 1e12 --samples 100 --ls-freq 1e6
```

| Parâmetro | Default | Descrição |
|---|---|---|
| `--fault` | `normal` | Tipo de falha a simular |
| `--duration` | `1e12` | Duração em picossegundos padrão do Sequence (1e12 = 1 segundo simulado) |
| `--samples` | `100` | Número de amostras por simulação para cada link |
| `--ls-freq` | `1e6` | Frequência do LightSource em Hz |
| `--output` | auto | Caminho do CSV de saída |

### Consolidar o dataset
```bash
python dataset/generate_dataset.py --data_dir dataset/data --output dataset/data/dataset_full.csv
```

## Features do dataset

| Feature | Descrição | Falha relacionada |
|---|---|---|
| `qber` | Quantum Bit Error Rate | qber, degrade |
| `key_rate_sifted` | Taxa de chave após sifting (bits/s) | degrade, node_fail |
| `key_rate_final` | Taxa de chave segura após QEC + PA (bits/s) | degrade, node_fail |
| `detection_count` | Detecções no intervalo | blinding |
| `error_count` | Erros no intervalo | qber |
| `dark_count_rate` | Taxa de dark counts/s | blinding |
| `detector_efficiency` | Eficiência atual do detector | blinding |
| `back_reflection_power` | Potência óptica reversa (W) | trojan |
| `phase_error_rate` | Taxa de erros na base X | trojan |
| `qber_delta` | Derivada do QBER | qber, degrade |
| `qber_ma5` | Média móvel QBER (janela 5) | degrade |
| `qber_var5` | Variância local QBER (janela 5) | degrade |
| `key_rate_drop` | Queda relativa no key rate | degrade, node_fail |
| `dark_count_delta` | Variação no dark count rate | blinding |
| `back_reflection_alert` | Flag: potência reversa > 1µW | trojan |
| `qber_alert` | Flag: QBER > 5% (threshold Tokyo QKD KMS) | qber |

## Referências

- M. Sasaki et al., *"Field test of quantum key distribution in the Tokyo QKD Network"*, Optics Express, 2011
- P. Thomas et al., *"Teleportation of arbitrary quantum states across a metropolitan network"*, 2024  
- W. Wu et al., *"SeQUeNCe: A Customizable Discrete-Event Simulator of Quantum Networks"*, 2020
- [SeQUeNCe GitHub](https://github.com/sequence-toolbox/SeQUeNCe)

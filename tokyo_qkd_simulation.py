"""
Tokyo QKD Network Simulation — SeQUeNCe

Baseado na topologia do Tokyo QKD Network (Sasaki et al., 2011)
5 nós, 4 links, ~30.2 km total

Falhas disponíveis:
  normal      — operação baseline
  qber        — ataque intercept-resend (QBER spike ~25%)
  degrade     — degradação gradual do canal (atenuação 3×)
  node_fail   — falha de trusted node (Hakusan)
  blinding    — blinding attack: satura detector, QBER permanece normal
  trojan      — Trojan Horse: potência óptica reversa anômala

Uso:
    python tokyo_qkd_simulation.py --fault normal
    python tokyo_qkd_simulation.py --fault blinding --output data/blinding.csv
"""

import argparse
import csv
import time
from pathlib import Path

from sequence.kernel.timeline import Timeline
from sequence.kernel.event import Event
from sequence.kernel.process import Process
from sequence.topology.node import QKDNode
from sequence.components.optical_channel import QuantumChannel, ClassicalChannel
from sequence.qkd.BB84 import BB84, pair_bb84_protocols




# Parâmetros físicos — Tokyo QKD Network (Sasaki et al., 2011)
# ============================================================
DETECTOR_EFFICIENCY = 0.80    # SSPD ~80-90%
DARK_COUNT_RATE     = 100     # counts/s (baseline)
TIME_RESOLUTION     = 100     # ps
MEAN_PHOTON_NUM     = 0.1     # µ (decoy state: signal pulse)
ATTENUATION         = 0.0002  # dB/m = 0.2 dB/km (SMF-28 ULL)
LIGHT_SPEED         = 2e8     # m/s em fibra óptica

# Topologia (distâncias em metros)
LINKS = [
    ("Koganei_A", "Koganei_B",  7_000),
    ("Koganei_B", "Otemachi",  13_000),
    ("Otemachi",  "Hakusan",    6_000),
    ("Hakusan",   "Hongo",      4_200),
]

ALL_FAULT_TYPES = ["normal", "qber", "degrade", "node_fail", "blinding", "trojan"]

FAULT_LABELS = {
    "normal":    0,
    "qber":      1,
    "degrade":   2,
    "node_fail": 3,
    "blinding":  4,
    "trojan":    5,
}

# Utilidades
# ==========

def classical_delay(distance_m: float) -> int:
    """
    Delay do canal clássico em picosegundos.
    Propagação + overhead de processamento (~8 µs).
    Tokyo QKD mediu RTT de 0.79-6.755 ms entre sites.
    """
    prop_ps = int((distance_m / LIGHT_SPEED) * 1e12)
    return prop_ps + 8_000


def schedule_event(tl: Timeline, node, fn_name: str, fn, time_ps: int):
    """Agenda fn como evento na timeline via monkey-patch no nó."""
    setattr(node, fn_name, fn)
    process = Process(node, fn_name, [])
    event   = Event(time_ps, process)
    tl.schedule(event)



# Coletor de métricas
# ===================

class QKDMetricsCollector:
    """
    Coleta métricas por amostra e salva em CSV.

    Colunas:
      timestamp_ps          — tempo simulado em picosegundos
      link                  — nome do link (ex: Koganei_A_Koganei_B)
      node                  — nó transmissor (Alice do par)
      qber                  — QBER instantâneo
      key_rate_sifted       — taxa após sifting (bps)
      key_rate_final        — taxa segura após QEC + PA (bps)
      detection_count       — detecções no intervalo
      error_count           — erros no intervalo
      dark_count_rate       — dark counts/s atuais  ← assinatura blinding
      detector_efficiency   — eficiência atual       ← assinatura blinding
      back_reflection_power — potência reversa (W)   ← assinatura Trojan Horse
      phase_error_rate      — erros na base X        ← assinatura Trojan Horse / phase-remapping
      label                 — rótulo string da falha
      fault_id              — rótulo numérico para ML
    """

    def __init__(self):
        self.records = []

    def record(self, timestamp_ps, link, node,
               qber, key_rate_sifted, key_rate_final,
               detection_count, error_count,
               dark_count_rate, detector_efficiency,
               back_reflection_power, phase_error_rate,
               label):
        self.records.append({
            "timestamp_ps":          timestamp_ps,
            "link":                  link,
            "node":                  node,
            "qber":                  round(qber, 6),
            "key_rate_sifted":       round(key_rate_sifted, 2),
            "key_rate_final":        round(key_rate_final, 2),
            "detection_count":       detection_count,
            "error_count":           error_count,
            "dark_count_rate":       round(dark_count_rate, 2),
            "detector_efficiency":   round(detector_efficiency, 4),
            "back_reflection_power": round(back_reflection_power, 9),
            "phase_error_rate":      round(phase_error_rate, 6),
            "label":                 label,
            "fault_id":              FAULT_LABELS.get(label, 0),
        })

    def save(self, path: str):
        if not self.records:
            print("[!] Nenhuma métrica coletada.")
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(self.records[0].keys())
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.records)
        print(f"[✓] Dataset salvo em: {path}  ({len(self.records)} registros)")



# Construção da rede
# ==================

def build_network(tl: Timeline, fault_type: str = "normal"):
    """
    Instancia nós, canais quânticos e clássicos.
    Retorna (nodes_dict, link_pairs).
    link_pairs: lista de (n1, n2, link_name, quantum_channel)
    """
    nodes = {}
    for name in ["Koganei_A", "Koganei_B", "Otemachi", "Hakusan", "Hongo"]:
        node = QKDNode(name, tl)

        # LightSource: acessado via "{name}.lightsource"
        lightsource = node.components[f"{name}.lightsource"]
        lightsource.mean_photon_num = MEAN_PHOTON_NUM

        # QSDetectorPolarization: acessado via "{name}.qsdetector"
        # Internamente tem uma lista .detectors com os Detector individuais
        qsdetector = node.components[f"{name}.qsdetector"]
        for det in qsdetector.detectors:
            det.efficiency      = DETECTOR_EFFICIENCY
            det.dark_count_rate = DARK_COUNT_RATE
            if hasattr(det, "time_resolution"):
                det.time_resolution = TIME_RESOLUTION

        nodes[name] = node

    link_pairs = []
    for n1_name, n2_name, dist in LINKS:
        n1        = nodes[n1_name]
        n2        = nodes[n2_name]
        link_name = f"{n1_name}_{n2_name}"

        # Degradação gradual: atenuação 3× no link Koganei_A → Koganei_B
        attn = ATTENUATION
        if fault_type == "degrade" and n1_name == "Koganei_A":
            attn = ATTENUATION * 3.0

        # QuantumChannel: set_ends(sender_node, receiver_name_str)
        qc = QuantumChannel(
            name        = f"qc_{link_name}",
            timeline    = tl,
            attenuation = attn,
            distance    = dist,
        )
        qc.set_ends(n1, n2_name)

        # ClassicalChannel: set_ends(sender_node, receiver_name_str)
        delay  = classical_delay(dist)
        cc_fwd = ClassicalChannel(f"cc_{link_name}_fwd", tl, distance=dist, delay=delay)
        cc_bwd = ClassicalChannel(f"cc_{link_name}_bwd", tl, distance=dist, delay=delay)
        cc_fwd.set_ends(n1, n2_name)
        cc_bwd.set_ends(n2, n1_name)

        # BB84.start_protocol() acessa cchannels/qchannels com string (nome do nó).
        # assign_cchannel registra com objeto como chave — adicionamos também a chave string.
        n1.cchannels[n2_name] = cc_fwd
        n2.cchannels[n1_name] = cc_bwd
        n1.qchannels[n2_name] = qc

        link_pairs.append((n1, n2, link_name, qc))

    # Rota alternativa KMS: Koganei_A ↔ Otemachi (direto, para rerouting)
    d = 20_000
    cc_ka_ote = ClassicalChannel("cc_KogA_Ote_fwd", tl, distance=d, delay=classical_delay(d))
    cc_ote_ka = ClassicalChannel("cc_KogA_Ote_bwd", tl, distance=d, delay=classical_delay(d))
    cc_ka_ote.set_ends(nodes["Koganei_A"], "Otemachi")
    cc_ote_ka.set_ends(nodes["Otemachi"],  "Koganei_A")
    nodes["Koganei_A"].cchannels["Otemachi"]  = cc_ka_ote
    nodes["Otemachi"].cchannels["Koganei_A"]  = cc_ote_ka

    return nodes, link_pairs



# Injeção de falhas
# =================

def inject_fault(tl: Timeline, nodes: dict, link_pairs: list,
                 fault_type: str, start_ps: int):
    """
    Agenda a falha na timeline do SeQUeNCe.

    Cada falha modifica parâmetros internos para produzir
    a assinatura física correta nas métricas coletadas.
    """

    # -- Intercept-Resend 
    # Físico: Eve mede cada qubit e reenvia um novo estado.
    # Assinatura: QBER dispara para ~25% (Tokyo QKD observou 2.4% → 49.7%).
    # Detecção: threshold simples em QBER (KMS do Tokyo QKD usa 5%).
    if fault_type == "qber":
        target = nodes["Otemachi"]

        def _attack():
            print(f"[FAULT qber] Intercept-resend em t={tl.now():.2e} ps")
            # Injeta QBER de ~25% diretamente na lista error_rates de Alice e Bob
            # O BB84 usa error_rates[-1] para reportar o último round
            bb84_otemachi = target.protocol_stack[0]
            if bb84_otemachi is not None:
                bb84_otemachi.error_rates.append(0.25)
                if bb84_otemachi.another is not None:
                    bb84_otemachi.another.error_rates.append(0.25)

        schedule_event(tl, target, "_fault_qber", _attack, start_ps)

    # -- Blinding Attack
    # Físico: Eve injeta luz CW intensa (mW) no detector de Bob para saturá-lo
    # em modo linear (threshold detector). Eve então controla as detecções
    # enviando pulsos calibrados, efetivamente determinando o resultado.
    #
    # Assinatura (PERIGOSO — QBER PERMANECE NORMAL):
    #   • QBER não sobe (Eve só dispara o detector quando convém)
    #   • dark_count_rate explode: 100/s → ~5.000.000/s
    #   • detector_efficiency sobe para 1.0 (saturado)
    #   • detection_count absoluto anormalmente alto mesmo com pouco sinal
    #
    # Detecção: NÃO usar QBER. Monitorar:
    #   1. dark_count_rate / detection_count absolutos
    #   2. Razão detecções_com_sinal / detecções_total (cai drasticamente)
    #   3. Correlação temporal entre pulsos (padrão anômalo)
    elif fault_type == "blinding":
        target = nodes["Otemachi"]   # Bob

        def _blind():
            print(f"[FAULT blinding] Detector blinding em t={tl.now():.2e} ps")
            # Satura cada Detector dentro do QSDetectorPolarization
            qsdet_key = f"{target.name}.qsdetector"
            if qsdet_key in target.components:
                for det in target.components[qsdet_key].detectors:
                    det.efficiency      = 1.0
                    det.dark_count_rate = 5_000_000
            # Cache no nó para extract_metrics
            target._dark_count_rate     = 5_000_000
            target._detector_efficiency = 1.0

        schedule_event(tl, target, "_fault_blind", _blind, start_ps)

    # -- Trojan Horse Attack 
    # Físico: Eve envia pulsos intensos DE VOLTA pelo canal quântico (sentido
    # inverso) para iluminar o modulador de fase de Alice e extrair informação
    # sobre as bases escolhidas via reflexão/retro-espalhamento.
    #
    # Assinatura:
    #   • QBER inalterado (ataque passivo na linha quântica)
    #   • back_reflection_power: sobe de ~fW (normal) para ~mW (ataque)
    #   • phase_error_rate: pequena elevação por perturbação no modulador
    #
    # Detecção:
    #   1. Monitor de potência óptica unidirecional (isolador óptico + fotodetector)
    #   2. Qualquer potência > 1 µW no sentido Alice→Eve é anômala
    elif fault_type == "trojan":
        target = nodes["Koganei_B"]  # Alice deste link

        def _trojan():
            print(f"[FAULT trojan] Trojan Horse em t={tl.now():.2e} ps")
            # Potência reversa anômala: 1 mW >> normal (~fW)
            target._back_reflection_power = 1e-3
            # Perturbação leve no modulador de fase
            for p in target.protocols:
                if hasattr(p, "phase_error_rate"):
                    p.phase_error_rate = 0.02

        schedule_event(tl, target, "_fault_trojan", _trojan, start_ps)

    # -- Node Failure 
    # Trusted node (Hakusan) vai offline.
    # KMS deve detectar e reroutar via Otemachi → Hongo diretamente.
    elif fault_type == "node_fail":
        target = nodes["Hakusan"]

        def _fail():
            print(f"[FAULT node_fail] Hakusan offline em t={tl.now():.2e} ps")
            target.is_offline = True
            for p in target.protocols:
                if hasattr(p, "stop"):
                    p.stop()

        schedule_event(tl, target, "_fault_node", _fail, start_ps)



# Extração de métricas
# ====================

def extract_metrics(node, link_name: str, qc: QuantumChannel,
                    bb84_alice=None, bb84_bob=None) -> dict:
    """
    Extrai métricas do BB84 usando os atributos reais do source:
    
      throughputs  (list[float]) — bits/s de cada chave completada
      error_rates  (list[float]) — QBER de cada chave completada (erros/key_length)
      latency      (float)       — latência da última chave (segundos)
      key_bits     (list[int])   — bits em construção da chave atual
      working      (bool)        — True se protocolo está ativo

    Métricas de detector (para blinding attack):
      dark_count_rate, detector_efficiency — lidos do QSDetectorPolarization.detectors[0]

    Métricas de potência reversa (para Trojan Horse):
      back_reflection_power — injetado via _back_reflection_power no nó
    """
    bb84 = bb84_alice

    # -- Métricas de chave (acumuladas em listas pelo BB84) 
    # error_rates: QBER de cada chave concluída neste round
    # Usamos a média das últimas entradas (novas desde o último sample)
    error_rates = getattr(bb84, "error_rates", []) if bb84 else []
    throughputs = getattr(bb84, "throughputs", []) if bb84 else []

    qber            = float(error_rates[-1])   if error_rates  else 0.0
    key_rate_final  = float(throughputs[-1])   if throughputs  else 0.0

    # key_bits em construção: sifted count parcial do round atual
    key_bits_alice  = getattr(bb84, "key_bits", None) if bb84 else None
    sifted_count    = len(key_bits_alice) if key_bits_alice else 0
    key_bits_bob    = getattr(bb84_bob, "key_bits", None) if bb84_bob else None
    sifted_count_b  = len(key_bits_bob) if key_bits_bob else 0
    key_rate_sifted = float(max(sifted_count, sifted_count_b))

    # error_count e detection_count para compatibilidade com features downstream
    # QBER = error_count / detection_count  →  error_count = QBER * key_length
    KEY_LENGTH      = 256
    detection_count = int(key_rate_sifted) if key_rate_sifted > 0 else KEY_LENGTH
    error_count     = int(round(qber * detection_count))
    phase_error_rate = 0.0  # não exposto pelo BB84 nativo; usado só pelo Trojan Horse

    # -- Parâmetros do detector 
    dark_count_rate     = DARK_COUNT_RATE
    detector_efficiency = DETECTOR_EFFICIENCY
    qsdet_key = f"{node.name}.qsdetector"
    if qsdet_key in node.components:
        dets = node.components[qsdet_key].detectors
        if dets:
            dark_count_rate     = getattr(dets[0], "dark_count_rate", DARK_COUNT_RATE)
            detector_efficiency = getattr(dets[0], "efficiency",      DETECTOR_EFFICIENCY)

    # Valores sobrepostos por fault injection (blinding attack)
    dark_count_rate     = getattr(node, "_dark_count_rate",     dark_count_rate)
    detector_efficiency = getattr(node, "_detector_efficiency", detector_efficiency)

    # -- Potência reversa (Trojan Horse) 
    back_reflection_power = getattr(node, "_back_reflection_power", 0.0)

    return {
        "qber":                  max(0.0, min(1.0, qber)),
        "key_rate_sifted":       key_rate_sifted,
        "key_rate_final":        key_rate_final,
        "detection_count":       detection_count,
        "error_count":           error_count,
        "dark_count_rate":       dark_count_rate,
        "detector_efficiency":   detector_efficiency,
        "back_reflection_power": back_reflection_power,
        "phase_error_rate":      phase_error_rate,
    }


# Runner principal
# ================

def run_simulation(fault_type: str = "normal", output_csv: str = None):
    print(f"\n{'═'*55}")
    print(f"  Tokyo QKD Simulation  |  fault: {fault_type}")
    print(f"{'═'*55}")

    DURATION_PS     = int(1e12)   # 1 segundo simulado
    FAULT_START_PS  = int(5e11)   # falha inicia em t = 0.5 s
    SAMPLE_INTERVAL = int(1e10)   # amostragem a cada 10 ms simulados

    tl = Timeline(DURATION_PS)
    tl.seed(42)

    nodes, link_pairs = build_network(tl, fault_type=fault_type)
    collector         = QKDMetricsCollector()

    # Instancia e emparelha BB84 para cada link
    # BB84.__init__(owner, name, lightsource, qsdetector, role=-1)
    # Os nomes dos componentes seguem o padrão "{node_name}.lightsource" e "{node_name}.qsdetector"
    bb84_map = {}   # link_name → (bb84_alice, bb84_bob) para extract_metrics
    for n1, n2, link_name, qc in link_pairs:
        bb84_alice = BB84(
            owner       = n1,
            name        = f"bb84_{link_name}_alice",
            lightsource = f"{n1.name}.lightsource",
            qsdetector  = f"{n1.name}.qsdetector",
        )
        bb84_bob = BB84(
            owner       = n2,
            name        = f"bb84_{link_name}_bob",
            lightsource = f"{n2.name}.lightsource",
            qsdetector  = f"{n2.name}.qsdetector",
        )

        # Adiciona na protocol_stack[0] de cada nó (posição reservada para BB84)
        n1.protocol_stack[0] = bb84_alice
        n2.protocol_stack[0] = bb84_bob

        # pair_bb84_protocols define .another e os roles (0=sender, 1=receiver)
        pair_bb84_protocols(bb84_alice, bb84_bob)

        bb84_map[link_name] = (bb84_alice, bb84_bob)

    # Agenda injeção de falha
    if fault_type != "normal":
        inject_fault(tl, nodes, link_pairs, fault_type, FAULT_START_PS)

    # -- Coleta de métricas via MetricsSampler
    # Timeline.run() executa todos os eventos até o fim — não tem run_to().
    # Criamos uma entidade dedicada (padrão SeQUeNCe) que agenda o próprio
    # próximo evento ao final de cada coleta, formando uma cadeia periódica.
    label_fn     = lambda t_ps: fault_type if t_ps >= FAULT_START_PS else "normal"
    sample_count = int(DURATION_PS / SAMPLE_INTERVAL)
    print(f"Amostras agendadas: {sample_count}  |  Intervalo: {SAMPLE_INTERVAL/1e9:.0f} ms simulados")

    class MetricsSampler:
        """
        Funcao do SeQUeNCe que coleta métricas periodicamente.
        Agenda o próprio próximo evento ao final de cada sample,
        formando uma cadeia até DURATION_PS.
        """
        def __init__(self, timeline, interval, duration, lp, bmap, col, lfn):
            self.timeline    = timeline
            self.interval    = interval
            self.duration    = duration
            self.link_pairs  = lp
            self.bb84_map    = bmap
            self.collector   = col
            self.label_fn    = lfn
            self.name        = "metrics_sampler"

        def init(self):
            self._schedule_next(self.interval)

        def _schedule_next(self, t):
            if t <= self.duration:
                process = Process(self, "sample", [t])
                event   = Event(t, process)
                self.timeline.schedule(event)

        def sample(self, t):
            for n1, n2, lname, qc in self.link_pairs:
                bb84_alice, bb84_bob = self.bb84_map[lname]
                m = extract_metrics(n1, lname, qc, bb84_alice, bb84_bob)
                self.collector.record(
                    timestamp_ps = t,
                    link         = lname,
                    node         = n1.name,
                    label        = self.label_fn(t),
                    **m,
                )
            self._schedule_next(t + self.interval)

    sampler = MetricsSampler(tl, SAMPLE_INTERVAL, DURATION_PS,
                              link_pairs, bb84_map, collector, label_fn)

    tl.init()

    # BB84.push(length, key_num, run_time) é o ponto de entrada correto.
    # start_protocol() sozinho não faz nada se key_lengths estiver vazio.
    # push() popula key_lengths e chama start_protocol() internamente.
    # Pedimos chaves de 256 bits, em loop contínuo (key_num grande), pelo
    # tempo total da simulação.
    KEY_LENGTH  = 256
    KEY_NUM     = 9999          # número de chaves a gerar (cobre 1s facilmente)
    for n1, n2, link_name, qc in link_pairs:
        bb84_alice, _ = bb84_map[link_name]
        bb84_alice.push(length=KEY_LENGTH, key_num=KEY_NUM, run_time=DURATION_PS)

    sampler.init()   # agenda o primeiro evento de coleta

    t_wall = time.time()
    tl.run()
    print(f"Concluído em {time.time() - t_wall:.1f}s (wall time)")

    if output_csv is None:
        output_csv = f"dataset_tokyo_qkd_{fault_type}.csv"
    collector.save(output_csv)

    return collector.records


# Entry point
# ===========

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tokyo QKD Network Simulation — SeQUeNCe",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--fault",
        choices=ALL_FAULT_TYPES,
        default="normal",
        help=(
            "Tipo de falha:\n"
            "  normal    — baseline sem falha\n"
            "  qber      — intercept-resend (QBER spike ~25%%)\n"
            "  degrade   — degradação gradual do canal\n"
            "  node_fail — falha de trusted node (Hakusan)\n"
            "  blinding  — blinding attack (detector saturado, QBER normal)\n"
            "  trojan    — Trojan Horse (potência reversa anômala)\n"
        )
    )
    parser.add_argument("--output", default=None, help="Caminho do CSV de saída")
    args = parser.parse_args()

    run_simulation(fault_type=args.fault, output_csv=args.output)

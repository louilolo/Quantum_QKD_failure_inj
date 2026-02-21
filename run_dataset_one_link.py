import csv
from statistics import mean

from sequence.topology.qkd_topo import QKDTopo
from sequence.qkd.BB84 import pair_bb84_protocols
from sequence.qkd.cascade import pair_cascade_protocols

from sequence.kernel.event import Event
from sequence.kernel.process import Process


JSON_PATH = r"D:\configs_seq\tokyo_like_qkd.json"
OUT_CSV   = r"D:\configs_seq\dataset_siteA_siteB.csv"

LINK_A = "SiteA"
LINK_B = "SiteB"

# tempos em picosegundos (ps)
PS = 1
MS = 1_000_000_000 * PS  # 1 ms = 1e9 ps
S  = 1_000_000_000_000 * PS

STOP_TIME = 2 * S
SAMPLE_EVERY = 20 * MS

KEYLEN = 128
FRAMES_PER_REQUEST = 5
REQUEST_EVERY = 100 * MS
CONSUME_EVERY = 80 * MS
CONSUME_KEYS = 2  # quantas chaves “gastar” por consumo


class ScenarioState:
    def __init__(self):
        self.label = "normal"
        self.starvation = 0

    def set_label(self, label: str):
        self.label = label


class TelemetryProbe:
    def __init__(self, tl, state, bb84, cascade):
        self.tl = tl
        self.state = state
        self.bb84 = bb84
        self.cascade = cascade
        self.rows = []

    def sample(self):
        t = self.tl.now()
        qber_proxy = None
        if getattr(self.bb84, "error_rates", None):
            qber_proxy = mean(self.bb84.error_rates[-50:])  # janela recente

        keys_buffer = len(getattr(self.cascade, "valid_keys", [])) if self.cascade else 0
        throughput = getattr(self.cascade, "throughput", None) if self.cascade else None
        latency = getattr(self.cascade, "latency", None) if self.cascade else None
        disclosed = getattr(self.cascade, "disclosed_bits_counter", None) if self.cascade else None

        self.rows.append({
            "t_ps": t,
            "label": self.state.label,
            "keys_buffer": keys_buffer,
            "qber_proxy": qber_proxy,
            "throughput_bits_s": throughput,
            "latency_s": latency,
            "disclosed_bits": disclosed,
            "starvation_events": self.state.starvation,
        })


class Traffic:
    def __init__(self, tl, state, bb84, cascade):
        self.tl = tl
        self.state = state
        self.bb84 = bb84
        self.cascade = cascade

    def request_keys(self):
        # Gera tráfego: “pedido de chaves”
        if self.cascade:
            self.cascade.push(keylen=KEYLEN, frame_num=FRAMES_PER_REQUEST, run_time=REQUEST_EVERY)
        else:
            self.bb84.push(KEYLEN, FRAMES_PER_REQUEST, run_time=REQUEST_EVERY)

    def consume(self):
        # Consumo da aplicação: gasta chaves do buffer
        if not self.cascade:
            return
        vk = getattr(self.cascade, "valid_keys", [])
        if len(vk) < CONSUME_KEYS:
            self.state.starvation += 1
            return
        # “gasta” removendo do buffer (simula uso de OTP/AES keys)
        for _ in range(CONSUME_KEYS):
            vk.pop(0)


class FnRunner:
    def __init__(self, fn):
        self.fn = fn
    def run(self):
        self.fn()

def schedule_at(tl, time_ps, obj, method_name, *args):
    p = Process(obj, method_name, list(args))
    e = Event(time_ps, p)
    tl.schedule(e)

def schedule_every(tl, start_ps, interval_ps, end_ps, obj, method_name):
    t = start_ps
    while t <= end_ps:
        schedule_at(tl, t, obj, method_name)
        t += interval_ps


def main():
    topo = QKDTopo(JSON_PATH)
    tl = topo.get_timeline()

    # pega entidades do link principal
    A = tl.get_entity_by_name(LINK_A)
    B = tl.get_entity_by_name(LINK_B)

    # ---- FIX: parear todos os nós do JSON antes do tl.init() ----
    # O SeQUeNCe inicializa TODOS os QKDNode do JSON; se algum não tiver role definido, dá AssertionError.
    # Então além do link A-B, pareamos também C-D (dummy) só pra não quebrar o init.
    dummy_pairs = [("SiteC", "SiteD")]  # ajuste aqui se mudar nomes no JSON

    # 1) pareia link principal A-B
    pair_bb84_protocols(A.protocol_stack[0], B.protocol_stack[0])
    if len(A.protocol_stack) > 1 and A.protocol_stack[1] and B.protocol_stack[1]:
        pair_cascade_protocols(A.protocol_stack[1], B.protocol_stack[1])

    # 2) pareia pares "sobrando" (dummy)
    for n1, n2 in dummy_pairs:
        N1 = tl.get_entity_by_name(n1)
        N2 = tl.get_entity_by_name(n2)
        if N1 is None or N2 is None:
            raise RuntimeError(f"Dummy pair inválido: {n1} / {n2} (verifique os nomes no JSON).")

        pair_bb84_protocols(N1.protocol_stack[0], N2.protocol_stack[0])
        if len(N1.protocol_stack) > 1 and N1.protocol_stack[1] and N2.protocol_stack[1]:
            pair_cascade_protocols(N1.protocol_stack[1], N2.protocol_stack[1])
    # ------------------------------------------------------------

    bb84 = A.protocol_stack[0]
    cascade = A.protocol_stack[1] if len(A.protocol_stack) > 1 else None

    # Ajuste de detector (exemplo) no link principal
    qsdA = A.components[f"{A.name}.qsdetector"]
    qsdB = B.components[f"{B.name}.qsdetector"]
    for i in (0, 1):
        qsdA.set_detector(i, efficiency=0.15, dark_count=1000)
        qsdB.set_detector(i, efficiency=0.15, dark_count=1000)

    state = ScenarioState()
    probe = TelemetryProbe(tl, state, bb84, cascade)
    traffic = Traffic(tl, state, bb84, cascade)

    # agenda telemetria + tráfego + consumo
    schedule_every(tl, start_ps=0, interval_ps=SAMPLE_EVERY, end_ps=STOP_TIME, obj=probe, method_name="sample")
    schedule_every(tl, start_ps=0, interval_ps=REQUEST_EVERY, end_ps=STOP_TIME, obj=traffic, method_name="request_keys")
    schedule_every(tl, start_ps=0, interval_ps=CONSUME_EVERY, end_ps=STOP_TIME, obj=traffic, method_name="consume")

    # ========= INJEÇÃO DE FALHAS (exemplos) =========
    qc_AB = A.qchannels[LINK_B]

    def fault_attenuation_jump():
        state.set_label("attenuation_jump")
        qc_AB.attenuation *= 3.0

    schedule_at(tl, time_ps=800*MS,  obj=FnRunner(fault_attenuation_jump), method_name="run")

    def fault_darkcount_spike():
        state.set_label("darkcount_spike")
        for i in (0, 1):
            qsdA.set_detector(i, efficiency=0.15, dark_count=50_000)

        
    schedule_at(tl, time_ps=1200*MS, obj=FnRunner(fault_darkcount_spike), method_name="run")

    lsA = A.components[f"{A.name}.lightsource"]

    def fault_phase_noise():
        state.set_label("phase_noise")
        lsA.phase_error = 0.08


    schedule_at(tl, time_ps=1500*MS, obj=FnRunner(fault_phase_noise), method_name="run")
    # ===============================================

    tl.init()
    tl.run()

    # salva dataset
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=probe.rows[0].keys())
        w.writeheader()
        w.writerows(probe.rows)

    print("Dataset salvo em:", OUT_CSV)
    print("Linhas:", len(probe.rows))

if __name__ == "__main__":
    main()
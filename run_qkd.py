from sequence.topology.qkd_topo import QKDTopo
from sequence.qkd.BB84 import pair_bb84_protocols
from sequence.qkd.cascade import pair_cascade_protocols

topo = QKDTopo(r"D:\configs_seq\tokyo_like_qkd.json")
tl = topo.get_timeline()

def node(name: str):
    n = tl.get_entity_by_name(name)
    if n is None:
        raise RuntimeError(f"Nó '{name}' não encontrado no timeline.")
    return n

# Pareie TODOS os nós que existem no JSON (senão tl.init() dá assert)
pairs = [
    ("SiteA", "SiteB"),  # link que você quer testar
    ("SiteC", "SiteD"),  # par "dummy" só pra não quebrar o init
]

for a, b in pairs:
    A = node(a)
    B = node(b)
    pair_bb84_protocols(A.protocol_stack[0], B.protocol_stack[0])

    # cascade pode estar no stack[1] dependendo da sua versão/config
    if len(A.protocol_stack) > 1 and A.protocol_stack[1] and B.protocol_stack[1]:
        pair_cascade_protocols(A.protocol_stack[1], B.protocol_stack[1])

tl.init()

# Disparar geração de chaves no link A-B
A = node("SiteA")
if len(A.protocol_stack) > 1 and A.protocol_stack[1]:
    A.protocol_stack[1].push(128, 10)   # 10 chaves de 128 bits
else:
    A.protocol_stack[0].push(128, 10)

tl.run()
print("OK: rodou sem AssertionError")
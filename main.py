"""
IonQ All-to-All Advantage Visualizer Engine
===========================================
Submitted to: Qollab x IonQ Global Quantum Hackathon 2026
Category: Quantum Unlocked
License: MIT

This script generates non-local entangling benchmark circuits (QFT & QAOA),
transpiles them for both 2D Grid and All-to-All architectures, compares routing
overhead metrics, executes the circuit on the simulator, and exports empirical metrics
to benchmark_data.json for index.html.
"""

import os
import json
import math
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import QFTGate
from qiskit.transpiler import CouplingMap

# Optional: Import qiskit-ionq provider if available in Qollab environment
try:
    from qiskit_ionq import IonQProvider
    HAS_IONQ_PROVIDER = True
except ImportError:
    HAS_IONQ_PROVIDER = False


# =====================================================================
# 1. ARCHITECTURE COUPLING MAP DEFINITIONS
# =====================================================================

def build_grid_coupling_map(num_qubits: int) -> CouplingMap:
    """
    Constructs a 1D/2D Line/Grid CouplingMap (Nearest-Neighbor).
    Forces SWAP insertion for non-adjacent qubit interactions.
    """
    edges = []
    cols = int(math.ceil(math.sqrt(num_qubits)))
    for i in range(num_qubits):
        # Connect to right neighbor
        if (i + 1) % cols != 0 and (i + 1) < num_qubits:
            edges.extend([(i, i + 1), (i + 1, i)])
        # Connect to bottom neighbor
        if (i + cols) < num_qubits:
            edges.extend([(i, i + cols), (i + cols, i)])
            
    # Fallback to linear chain if grid construction produces isolated nodes
    if not edges and num_qubits > 1:
        for i in range(num_qubits - 1):
            edges.extend([(i, i + 1), (i + 1, i)])

    return CouplingMap(edges)


def build_ionq_coupling_map(num_qubits: int) -> CouplingMap:
    """
    Constructs a Fully Connected CouplingMap (All-to-All).
    Represents trapped-ion laser addressing capabilities.
    """
    edges = [(i, j) for i in range(num_qubits) for j in range(num_qubits) if i != j]
    return CouplingMap(edges)


# =====================================================================
# 2. BENCHMARK CIRCUIT GENERATORS (Pure Quantum Circuits)
# =====================================================================

def generate_qft_circuit(num_qubits: int) -> QuantumCircuit:
    """Generates an N-qubit Quantum Fourier Transform circuit using modern QFTGate."""
    qc = QuantumCircuit(num_qubits)
    qc.append(QFTGate(num_qubits), range(num_qubits))
    qc = qc.decompose()
    qc.name = f"QFT_{num_qubits}Q"
    return qc


def generate_qaoa_maxcut_circuit(num_qubits: int, edge_list: list = None) -> QuantumCircuit:
    """
    Generates a 1-step QAOA circuit for Max-Cut on a non-planar graph.
    Omits measurements in definition to allow pure statevector handling.
    """
    if edge_list is None:
        # Complete Graph K_N: Every qubit interacts with every other qubit
        edge_list = [(i, j) for i in range(num_qubits) for j in range(i + 1, num_qubits)]

    qc = QuantumCircuit(num_qubits)
    gamma, beta = 0.5, 0.25

    # Initial state preparation: Hadamard on all qubits
    qc.h(range(num_qubits))

    # Problem Hamiltonian: Phase separator for each graph edge
    for u, v in edge_list:
        qc.cx(u, v)
        qc.rz(2 * gamma, v)
        qc.cx(u, v)

    # Mixer Hamiltonian: RX rotations on all qubits
    qc.rx(2 * beta, range(num_qubits))
    qc.name = f"QAOA_MaxCut_{num_qubits}Q"
    return qc


# =====================================================================
# 3. TRANSPILATION & OVERHEAD EVALUATION ENGINE
# =====================================================================

def evaluate_routing_overhead(qc: QuantumCircuit):
    """
    Transpiles the circuit against Grid vs. IonQ All-to-All topologies
    and returns a structured metric breakdown.
    """
    num_qubits = qc.num_qubits
    grid_map = build_grid_coupling_map(num_qubits)
    ionq_map = build_ionq_coupling_map(num_qubits)

    # Transpile for Constrained 2D Grid (Uses SABRE routing & SWAP insertion)
    transpiled_grid = transpile(
        qc,
        coupling_map=grid_map,
        layout_method='sabre',
        routing_method='sabre',
        optimization_level=3,
        seed_transpiler=42
    )

    # Transpile for IonQ Trapped-Ion (Zero SWAP overhead)
    transpiled_ionq = transpile(
        qc,
        coupling_map=ionq_map,
        optimization_level=3,
        seed_transpiler=42
    )

    # Count operations
    grid_ops = transpiled_grid.count_ops()
    ionq_ops = transpiled_ionq.count_ops()

    # Hardware Error Model Assumptions
    GRID_2Q_ERROR = 0.004
    IONQ_2Q_ERROR = 0.003

    grid_2q_count = grid_ops.get('cx', 0) + grid_ops.get('ecr', 0) + (grid_ops.get('swap', 0) * 3)
    ionq_2q_count = ionq_ops.get('ms', 0) + ionq_ops.get('cx', 0)

    grid_fidelity = (1 - GRID_2Q_ERROR) ** grid_2q_count
    ionq_fidelity = (1 - IONQ_2Q_ERROR) ** ionq_2q_count

    results = {
        "circuit_name": qc.name,
        "num_qubits": num_qubits,
        "grid_architecture": {
            "depth": transpiled_grid.depth(),
            "total_gates": sum(grid_ops.values()),
            "swap_gates": grid_ops.get('swap', 0),
            "two_qubit_gates": grid_2q_count,
            "estimated_fidelity_pct": round(grid_fidelity * 100, 2)
        },
        "ionq_architecture": {
            "depth": transpiled_ionq.depth(),
            "total_gates": sum(ionq_ops.values()),
            "swap_gates": 0,
            "two_qubit_gates": ionq_2q_count,
            "estimated_fidelity_pct": round(ionq_fidelity * 100, 2)
        },
        "comparison": {
            "depth_reduction_factor": round(transpiled_grid.depth() / max(1, transpiled_ionq.depth()), 2),
            "swaps_eliminated": grid_ops.get('swap', 0),
            "fidelity_advantage_pct": round((ionq_fidelity - grid_fidelity) * 100, 2)
        }
    }

    return results, transpiled_grid, transpiled_ionq


# =====================================================================
# 4. SIMULATION EXECUTION ENGINE (With Fallback)
# =====================================================================

def run_on_ionq_simulator(qc: QuantumCircuit, shots: int = 1000):
    """
    Executes the circuit on Qollab's IonQ Simulator instance,
    or falls back to Qiskit's built-in StatevectorSampler primitive.
    """
    print(f"\n--- Submitting {qc.name} to IonQ Quantum Simulator ---")

    meas_qc = qc.copy()
    meas_qc.measure_all()

    api_key = os.getenv("IONQ_API_KEY") or os.getenv("QOLLAB_IONQ_TOKEN")

    if HAS_IONQ_PROVIDER and api_key:
        try:
            provider = IonQProvider(token=api_key)
            backend = provider.get_backend("ionq.simulator")
            job = backend.run(meas_qc, shots=shots)
            result = job.result()
            print("IonQ Simulator Execution Successful!")
            return result.get_counts()
        except Exception as e:
            print(f"Notice: IonQ API submission skipped ({e}). Falling back to Qiskit StatevectorSampler.")

    # Fallback: Standard Qiskit Primitives StatevectorSampler
    from qiskit.primitives import StatevectorSampler

    sampler = StatevectorSampler()
    job = sampler.run([(meas_qc,)], shots=shots)
    pub_result = job.result()[0]

    counts = pub_result.data.meas.get_counts()
    print("Local Qiskit StatevectorSampler Execution Successful!")
    return counts


# =====================================================================
# 5. ENTRY POINTS & RUNNERS
# =====================================================================

def run_benchmarks():
    print("==========================================================")
    print("  IONQ ALL-TO-ALL ADVANTAGE BENCHMARKING ENGINE")
    print("==========================================================")

    # 1. Run 5-Qubit QFT Benchmark
    qft_qc = generate_qft_circuit(num_qubits=5)
    qft_metrics, grid_qft, ionq_qft = evaluate_routing_overhead(qft_qc)

    print("\n[Benchmark 1: 5-Qubit QFT]")
    print(json.dumps(qft_metrics, indent=2))

    # 2. Run 5-Qubit QAOA Max-Cut on Complete Graph K_5
    qaoa_qc = generate_qaoa_maxcut_circuit(num_qubits=5)
    qaoa_metrics, grid_qaoa, ionq_qaoa = evaluate_routing_overhead(qaoa_qc)

    print("\n[Benchmark 2: 5-Qubit QAOA Max-Cut (Complete Graph K5)]")
    print(json.dumps(qaoa_metrics, indent=2))

    # 3. Export empirical Qiskit transpilation metrics to benchmark_data.json
    benchmark_payload = {
        "qft": qft_metrics,
        "qaoa": qaoa_metrics
    }

    with open("benchmark_data.json", "w") as f:
        json.dump(benchmark_payload, f, indent=2)

    print("\n[SUCCESS] Saved Qiskit transpilation metrics to benchmark_data.json!")

    # 4. Execute QAOA circuit simulation
    counts = run_on_ionq_simulator(qaoa_qc, shots=1000)
    print(f"\nSimulation Measurement Counts (Top 5 States):")
    sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]
    for state, count in sorted_counts:
        print(f"  |{state}⟩: {count} counts")


def main():
    run_benchmarks()


if __name__ == "__main__":
    main()
"""Static contract checks and arithmetic golden; NOT a network simulator.

Only the project's serial, ideal-service example is supported. No router,
queue, buffer occupancy, credit, Chakra conversion or tool integration is run.
Usage: python validate_contract.py [contract_example.json] [--self-test]
"""

import argparse
import copy
import json
from pathlib import Path


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def integer(value, name, minimum=0):
    require(type(value) is int and value >= minimum,
            f"{name}: expected integer >= {minimum}")
    return value


def ceil_div(a, b):
    return (a + b - 1) // b


def validate(data):
    require(data["schema_version"] == "0.1", "unsupported schema_version")
    require(data["example_only"] is True, "validator supports examples only")
    require(data["fidelity"] == "ideal_serial_contract_golden", "unsupported fidelity")
    require(data["units"] == {"time": "ps", "bandwidth": "B/s", "payload": "byte"},
            "unit contract mismatch")
    release = integer(data["workload_release_ps"], "workload_release_ps")
    endpoints, resources, owners = data["endpoints"], data["resources"], data["ownership"]
    require(bool(endpoints) and bool(resources), "empty endpoints/resources")
    for eid, endpoint in endpoints.items():
        integer(endpoint["buffer_capacity_bytes"], f"{eid}.capacity", 1)
        require(endpoint["die"] in data["rank_map"].values(), f"unmapped die for {eid}")
    for rid, resource in resources.items():
        integer(resource["capacity"], f"{rid}.capacity", 1)
        require(owners.get(resource["scope_id"]) == resource["owner"],
                f"resource owner mismatch: {rid}")
    nodes = data["nodes"]
    require(bool(nodes), "empty node list")
    ids = [n["node_id"] for n in nodes]
    require(all(isinstance(x, str) and x for x in ids), "invalid node ID")
    require(len(ids) == len(set(ids)), "duplicate node ID")
    by_id = {n["node_id"]: n for n in nodes}
    duration, transfer_signatures = {}, {}
    for n in nodes:
        nid, kind = n["node_id"], n["kind"]
        require(kind in {"compute", "local_transfer", "fabric_transfer"},
                f"unsupported kind: {kind}")
        integer(n["release_ps"], f"{nid}.release_ps")
        require(len(n["deps"]) == len(set(n["deps"])), f"duplicate dependency: {nid}")
        require(all(d in by_id and d != nid for d in n["deps"]),
                f"unknown/self dependency: {nid}")
        require(n["resource_id"] in resources, f"unknown resource: {nid}")
        resource = resources[n["resource_id"]]
        require(n["owner"] == resource["owner"] == owners.get(n["scope_id"]),
                f"owner mismatch: {nid}")
        require(n["scope_id"] == resource["scope_id"], f"scope mismatch: {nid}")
        if kind == "compute":
            require(n["owner"] == "timeloop_core", f"compute owner: {nid}")
            require(n["endpoint_id"] in endpoints, f"unknown compute endpoint: {nid}")
            require(endpoints[n["endpoint_id"]]["kind"] == "compute", f"not compute: {nid}")
            require(n["service_time_scope"] == "core_private_only", f"compute scope: {nid}")
            require(n["included_components"] == [n["scope_id"]], f"included scope: {nid}")
            require(not set(n["included_components"]) & set(n["excluded_components"]),
                    f"included/excluded scope overlap: {nid}")
            cycles = integer(n["service_cycles"], f"{nid}.cycles", 1)
            hz = integer(n["clock_hz"], f"{nid}.clock_hz", 1)
            duration[nid] = ceil_div(cycles * 10**12, hz)
        else:
            require(n["src"] in endpoints and n["dst"] in endpoints, f"unknown endpoint: {nid}")
            src, dst = endpoints[n["src"]], endpoints[n["dst"]]
            payload = integer(n["payload_bytes"], f"{nid}.payload_bytes", 1)
            integer(n["chunk_id"], f"{nid}.chunk_id")
            require(payload <= dst["buffer_capacity_bytes"], f"chunk exceeds destination: {nid}")
            require(n["completion_semantics"] == "destination_buffer_accepted", f"completion: {nid}")
            if kind == "local_transfer":
                require(n["owner"] == "gem5" and src["die"] == dst["die"], f"local boundary: {nid}")
            else:
                require(n["owner"] == "astra_fabric" and src["die"] != dst["die"], f"fabric boundary: {nid}")
                require(src["kind"] == "boundary_tx" and dst["kind"] == "boundary_rx", f"fabric ports: {nid}")
            key = (n["transfer_id"], n["chunk_id"])
            signature = (payload, n["tensor_id"])
            require(key not in transfer_signatures or transfer_signatures[key] == signature,
                    f"transfer byte/tensor mismatch: {key}")
            transfer_signatures[key] = signature
            bw = integer(n["bandwidth_Bps"], f"{nid}.bandwidth_Bps", 1)
            latency = integer(n["fixed_latency_ps"], f"{nid}.fixed_latency_ps")
            duration[nid] = latency + ceil_div(payload * 10**12, bw)
        integer(n["golden_duration_ps"], f"{nid}.golden_duration_ps", 1)
        require(duration[nid] == n["golden_duration_ps"], f"duration mismatch: {nid}")

    # Topological ordering also verifies this fixture is completely serialized.
    order, pending = [], set(ids)
    while pending:
        ready = sorted(nid for nid in pending if set(by_id[nid]["deps"]) <= set(order))
        require(bool(ready), "dependency cycle")
        require(len(ready) == 1, "nonserial DAG: this validator cannot model concurrency")
        order.append(ready[0])
        pending.remove(ready[0])

    # Verify the example's egress -> external fabric -> ingress path.
    for n in nodes:
        if n["kind"] != "fabric_transfer":
            continue
        key = (n["transfer_id"], n["chunk_id"])
        predecessors = [by_id[d] for d in n["deps"]]
        require(any(p["kind"] == "local_transfer" and p["dst"] == n["src"]
                    and (p["transfer_id"], p["chunk_id"]) == key for p in predecessors),
                f"missing egress: {n['node_id']}")
        require(any(p["kind"] == "local_transfer" and p["src"] == n["dst"]
                    and n["node_id"] in p["deps"]
                    and (p["transfer_id"], p["chunk_id"]) == key for p in nodes),
                f"missing ingress: {n['node_id']}")
    completed = {}
    for nid in order:
        node = by_id[nid]
        start = max([release, node["release_ps"]] + [completed[d] for d in node["deps"]])
        completed[nid] = start + duration[nid]
    makespan = max(completed.values()) - release
    cross_bytes = sum(n["payload_bytes"] for n in nodes if n["kind"] == "fabric_transfer")
    require(completed == data["expected"]["completed_ps"], "completion timestamps differ from golden")
    require(makespan == data["expected"]["makespan_ps"], "makespan differs from golden")
    require(cross_bytes == data["expected"]["cross_die_payload_bytes"], "cross-die bytes differ")
    return {"status": "PASS", "checked_nodes": len(nodes), "makespan_ps": makespan,
            "cross_die_payload_bytes": cross_bytes, "completed_ps": completed,
            "scope": "static contract and ideal serial arithmetic only"}


def self_test(valid):
    # Deliberate corruptions exercise meaningful interface failure conditions.
    cases = {
        "duplicate_id": lambda d: d["nodes"][1].update(node_id="load0"),
        "unknown_dependency": lambda d: d["nodes"][1].update(deps=["missing"]),
        "cycle": lambda d: d["nodes"][0].update(deps=["store1"]),
        "wrong_owner": lambda d: d["nodes"][3].update(owner="gem5"),
        "wrong_unit": lambda d: d["units"].update(time="ns"),
        "bad_clock": lambda d: d["nodes"][1].update(clock_hz=0),
        "oversize_chunk": lambda d: d["endpoints"]["die1.rx"].update(buffer_capacity_bytes=128),
        "payload_mismatch": lambda d: d["nodes"][3].update(payload_bytes=128),
        "premature_completion": lambda d: d["nodes"][3].update(completion_semantics="send_accepted"),
        "nonserial_graph": lambda d: d["nodes"][1].update(deps=[]),
        "incorrect_golden": lambda d: d["expected"].update(makespan_ps=1),
        "unknown_endpoint": lambda d: d["nodes"][0].update(dst="missing"),
    }
    for name, mutate in cases.items():
        candidate = copy.deepcopy(valid)
        mutate(candidate)
        try:
            validate(candidate)
        except (ContractError, KeyError, TypeError):
            continue
        raise ContractError(f"self-test accepted invalid case: {name}")
    return {"negative_cases_rejected": len(cases)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path,
                        default=Path(__file__).with_name("contract_example.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.path.read_text(encoding="utf-8"))
        result = validate(data)
        if args.self_test:
            result["self_test"] = self_test(data)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ContractError, KeyError, TypeError, ValueError, OSError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")


if __name__ == "__main__":
    main()

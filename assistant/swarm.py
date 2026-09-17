import logging
logger = logging.getLogger(__name__)

import json
import re
import threading
from assistant.config import get_setting
from assistant import guard
from assistant import call_context
from assistant import events

# Role-based specialization. A known role name expands to its system prompt;
# any other string passes through untouched as a custom prompt, so every
# existing caller keeps working.
ROLES = {
    "Researcher": (
        "You are a research specialist. Gather specific, factual information "
        "with your tools and cite where each fact came from. You observe and "
        "report; you never change anything on the machine."
    ),
    "Planner": (
        "You are a planning specialist. Break the goal into a short ordered "
        "list of concrete steps with their dependencies. Be specific enough "
        "that each step could be handed to another agent untouched."
    ),
    "Coder": (
        "You are a coding specialist. Write or edit code with the developer "
        "tools, add or update tests for what you changed, and run them. "
        "Report what you changed, what the tests said, and what remains."
    ),
    "Verifier": (
        "You are a verification specialist. Check a result against the goal "
        "it was supposed to achieve and against these safety rules: no "
        "passwords or secrets in output, no destructive commands, no invented "
        "tool results, claims backed by tool output. Reply with exactly one "
        "line first: APPROVED if it passes everything, otherwise REJECTED "
        "followed by what must be fixed."
    ),
}


def role_prompt_for(role):
    """Expand a known role name, or pass a custom prompt through."""
    return ROLES.get(str(role or ""), str(role or ""))

def _parse_swarm_args(raw_args):
    if isinstance(raw_args, dict):
        return raw_args
    if not raw_args:
        return {}
    if isinstance(raw_args, str):
        cleaned = raw_args.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            val = json.loads(cleaned)
            if isinstance(val, dict):
                return val
        except Exception:
            pass
        try:
            import ast
            val = ast.literal_eval(cleaned)
            if isinstance(val, dict):
                return val
        except Exception:
            pass
    return {}

def run_sub_agent(role_prompt, task, max_steps=15, event_bus=None,
                  agent_id=""):
    from assistant.ai_brain import query_local_llm_chat, AVAILABLE_FUNCTIONS
    role_prompt = role_prompt_for(role_prompt)
    name = agent_id or str(task)[:40]
    if event_bus is not None:
        try:
            event_bus.emit(events.EVENT_STATUS, f"Sub-agent started: {name}",
                           scope="swarm", agent=agent_id, task=str(task)[:200])
        except Exception:
            pass
    history = [
        {"role": "system", "content": role_prompt},
        {"role": "user", "content": f"YOUR TASK: {task}\nExecute it using your tools. Once finished, return your final answer in plain text."}
    ]
    outcome = None
    for step in range(max_steps):
        response = query_local_llm_chat(history, model=get_setting("llm_model", "qwen2.5:3b"))
        if not response:
            outcome = "Agent failed."
            break
        history.append(response)
        if "tool_calls" in response and response["tool_calls"]:
            for tool in response["tool_calls"]:
                func_name = tool["function"]["name"]
                args_dict = _parse_swarm_args(tool.get("function", {}).get("arguments", {}))
                if func_name in AVAILABLE_FUNCTIONS:
                    func_to_call = AVAILABLE_FUNCTIONS[func_name]
                    logger.info(f"[Sub-Agent Executing] {func_name}({args_dict})")
                    try:
                        args_dict = guard.coerce_args(func_to_call, args_dict)
                        result = str(guard.call(func_to_call,
                                                _tool_name=func_name,
                                                **args_dict))[:2000]
                        history.append({"role": "tool", "content": result, "name": func_name})
                    except guard.ToolDenied as e:
                        history.append({"role": "tool", "content": f"Denied by safety guard: {e}", "name": func_name})
                    except Exception as e:

                        history.append({"role": "tool", "content": f"Failed: {e}", "name": func_name})
                else:
                    history.append({"role": "tool", "content": "Tool not found.", "name": func_name})
            continue
        else:
            outcome = response.get("content", "No output generated.")
            break
    else:
        outcome = "Agent reached maximum steps."
    if event_bus is not None:
        try:
            event_bus.emit(events.EVENT_STATUS, f"Sub-agent finished: {name}",
                           scope="swarm", agent=agent_id,
                           ok=not _swarm_failed(outcome))
        except Exception:
            pass
    return outcome

def _swarm_failed(output):
    """Whether an agent result means the work did not happen."""
    text = str(output or "")
    return text in ("Agent failed.", "Agent reached maximum steps.") \
        or text.startswith("Error:")


def topological_levels(nodes):
    """Order node ids into waves where every dependency ran first.

    Returns (levels, error): levels is a list of waves (lists of ids);
    on bad input error names the problem and levels is empty. A node runs
    only after every id in its `needs` list, so independent nodes share a
    wave and run together.
    """
    by_id = {}
    for node in nodes or []:
        node_id = str((node or {}).get("id", "")).strip()
        if not node_id:
            return [], "Every node needs an 'id'."
        if node_id in by_id:
            return [], f"Duplicate node id: '{node_id}'."
        by_id[node_id] = node
    if not by_id:
        return [], "No nodes given."

    needs = {}
    for node_id, node in by_id.items():
        deps = (node or {}).get("needs", []) or []
        if isinstance(deps, str):
            deps = [deps]
        deps = [str(dep).strip() for dep in deps if str(dep).strip()]
        unknown = [dep for dep in deps if dep not in by_id]
        if unknown:
            return [], (f"Node '{node_id}' needs unknown "
                        f"{'node' if len(unknown) == 1 else 'nodes'}: "
                        f"{', '.join(unknown)}.")
        if node_id in deps:
            return [], f"Node '{node_id}' depends on itself."
        needs[node_id] = deps

    levels, done = [], set()
    while len(done) < len(by_id):
        wave = sorted(node_id for node_id in by_id
                      if node_id not in done
                      and all(dep in done for dep in needs[node_id]))
        if not wave:
            stuck = sorted(set(by_id) - done)
            return [], (f"Circular dependency among: {', '.join(stuck)}.")
        levels.append(wave)
        done.update(wave)
    return levels, ""


def run_swarm_dag(nodes, max_steps: int = 15, event_bus=None):
    """Run composite work as a dependency graph of sub-agents.

    Each node: {"id": ..., "role": ..., "task": ..., "needs": [...]}. Waves
    run together; a node sees its dependencies' outputs as context; a node
    whose dependency failed is skipped, never run blind. Returns
    {"ok", "results", "report"} where results maps each id to
    {"ok", "output"}. Accepts a JSON string or a list (models send text).
    """
    if isinstance(nodes, str):
        cleaned = nodes.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            nodes = json.loads(cleaned)
        except Exception:
            try:
                import ast
                nodes = ast.literal_eval(cleaned)
            except Exception:
                pass
    if not isinstance(nodes, list):
        return {"ok": False, "results": {},
                "report": "Error: nodes must be a list of node dicts."}
    nodes = list(nodes)
    levels, error = topological_levels(nodes)
    if error:
        return {"ok": False, "results": {},
                "report": f"Error: {error}"}
    by_id = {str(node.get("id", "")).strip(): node for node in nodes}

    results = {}
    results_lock = threading.Lock()

    def _deps_of(node):
        raw = (node or {}).get("needs", []) or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(dep).strip() for dep in raw if str(dep).strip()]

    def worker(node_id):
        node = by_id[node_id]
        deps = _deps_of(node)
        with results_lock:
            failed_deps = [dep for dep in deps
                           if not results.get(dep, {}).get("ok", False)]
        if failed_deps:
            outcome = {"ok": False,
                       "output": ("Skipped: dependency "
                                  f"'{failed_deps[0]}' did not succeed.")}
        else:
            context_lines = []
            with results_lock:
                for dep in deps:
                    context_lines.append(
                        f"- {dep}: {results[dep]['output']}")
            prompt_task = str(node.get("task", ""))
            if context_lines:
                prompt_task = ("Completed dependencies:\n"
                               + "\n".join(context_lines)
                               + f"\n\nYOUR TASK: {prompt_task}")
            output = run_sub_agent(node.get("role", "Helpful AI"), prompt_task,
                                   max_steps=max_steps, event_bus=event_bus,
                                   agent_id=node_id)
            outcome = {"ok": not _swarm_failed(output), "output": output}
        with results_lock:
            results[node_id] = outcome

    for wave in levels:
        threads = []
        for node_id in wave:
            thread = call_context.spawn_thread(
                target=worker, args=(node_id,),
                name=f"swarm-{node_id[:16]}")
            threads.append(thread)
        for thread in threads:
            thread.join()

    lines = ["Swarm Task Results:"]
    for node_id in by_id:
        outcome = results.get(node_id, {"ok": False, "output": "Not run."})
        state = "done" if outcome["ok"] else "FAILED"
        lines.append(f"\n--- {node_id} [{state}] ---\n{outcome['output']}")
    return {"ok": all(item["ok"] for item in results.values()),
            "results": results, "report": "\n".join(lines)}


def _verdict_approved(evaluation):
    """The shared verdict rule: APPROVED present, REJECTED absent."""
    upper = str(evaluation or "").upper()
    return "APPROVED" in upper and "REJECTED" not in upper


def run_actor_critic(goal, actor_role="Coder", critic_role="Verifier",
                     max_iterations: int = 3, max_steps: int = 15,
                     event_bus=None):
    """Generator proposes, critic disposes, until approval or the cap.

    The critic judges against the goal and the safety rules in its prompt
    (no secrets, nothing destructive, claims backed by tool output). Returns
    {"approved", "output", "iterations", "verdicts"}.
    """
    if event_bus is not None:
        try:
            event_bus.emit(events.EVENT_STATUS,
                           f"Actor-critic started: {str(goal)[:120]}",
                           scope="swarm")
        except Exception:
            pass
    draft = run_sub_agent(actor_role, f"Produce a first attempt at this goal:\n{goal}",
                          max_steps=max_steps, event_bus=event_bus,
                          agent_id="actor")
    verdicts = []
    approved = False
    for _ in range(max(1, max_iterations)):
        evaluation = run_sub_agent(
            critic_role,
            f"Goal:\n{goal}\n\nAttempt to judge:\n{draft}\n\n"
            f"Reply with your verdict for the goal above.",
            max_steps=5, event_bus=event_bus, agent_id="critic")
        verdicts.append(evaluation)
        if _verdict_approved(evaluation):
            approved = True
            break
        logger.info("[Swarm] Critic rejected the attempt; sending back.")
        draft = run_sub_agent(
            actor_role,
            f"Your attempt was REJECTED. Critic feedback:\n{evaluation}\n\n"
            f"Fix it for this goal:\n{goal}\n\nPrevious attempt:\n{draft}",
            max_steps=max_steps, event_bus=event_bus, agent_id="actor")
    if event_bus is not None:
        try:
            event_bus.emit(events.EVENT_STATUS,
                           f"Actor-critic finished: {'approved' if approved else 'not approved'}",
                           scope="swarm", approved=approved)
        except Exception:
            pass
    return {"approved": approved, "output": draft,
            "iterations": len(verdicts), "verdicts": verdicts}


def spawn_parallel_agents(task_list):
    import ast
    if isinstance(task_list, str):
        cleaned = task_list.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        parsed = None
        try:
            parsed = json.loads(cleaned)
        except Exception:
            try:
                parsed = ast.literal_eval(cleaned)
            except Exception:
                pass
        if isinstance(parsed, list):
            task_list = parsed
        else:
            return "Error: task_list must be a valid list of dictionaries."
    if not isinstance(task_list, list):
        return "Error: task_list must be a valid list of dictionaries."
    logger.info(f"[Swarm] Spawning {len(task_list)} parallel agents...")
    results = {}
    def worker(index, role, task):
        res = run_sub_agent(role, task)
        results[f"Agent_{index}_{role}"] = res
    threads = []
    for i, t in enumerate(task_list):
        role = t.get("role", "Helpful AI")
        task_desc = t.get("task", "")
        thread = call_context.spawn_thread(target=worker, args=(i, role, task_desc))
        threads.append(thread)

    for thread in threads: thread.join()
    combined_report = "Parallel Execution Results:\n"
    for name, res in results.items():
        combined_report += f"\n--- {name} ---\n{res}\n"
    return combined_report

def run_actor_critic_research(topic, max_iterations=3):
    logger.info(f"[Swarm] Initiating Actor-Critic Research on: {topic}")
    actor_prompt = "You are an elite Deep Web Researcher. Use tools like search_web, read_file, scroll, and click to find specific, factual information on the requested topic. Write a highly detailed, perfect report."
    critic_prompt = "You are a harsh Critic. If the research report lacks details, specific facts, or citations, reply REJECTED and list exactly what must be fixed. If it is flawless and fully detailed, reply APPROVED."
    current_draft = run_sub_agent(actor_prompt, f"Research this thoroughly: {topic}")
    for i in range(max_iterations):
        logger.info(f"[Swarm] Critic Evaluation {i+1}...")
        evaluation = run_sub_agent(critic_prompt, f"Evaluate this research draft:\n{current_draft}\n\nIf perfect say APPROVED, else REJECTED and list fixes.", max_steps=5)
        if "APPROVED" in evaluation.upper() and "REJECTED" not in evaluation.upper():
            break
        else:
            logger.info(f"[Swarm] Rejected. Resending to Actor.")
            current_draft = run_sub_agent(actor_prompt, f"Your draft was REJECTED. Feedback:\n{evaluation}\n\nFix this draft:\n{current_draft}\n\nUse tools to find the missing information if necessary.")
    return current_draft

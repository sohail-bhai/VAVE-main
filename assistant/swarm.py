import logging
logger = logging.getLogger(__name__)

import json
import re
import threading
from assistant.config import get_setting
from assistant import guard
from assistant import call_context

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

def run_sub_agent(role_prompt, task, max_steps=15):
    from assistant.ai_brain import query_local_llm_chat, AVAILABLE_FUNCTIONS
    history = [
        {"role": "system", "content": role_prompt},
        {"role": "user", "content": f"YOUR TASK: {task}\nExecute it using your tools. Once finished, return your final answer in plain text."}
    ]
    for step in range(max_steps):
        response = query_local_llm_chat(history, model=get_setting("llm_model", "qwen2.5:3b"))
        if not response: return "Agent failed."
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
            return response.get("content", "No output generated.")
    return "Agent reached maximum steps."

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

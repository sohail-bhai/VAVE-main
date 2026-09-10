import os
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from assistant.speech import speak
from assistant.config import get_setting, update_setting

def _run_shell(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def git_auto_commit_and_push(push=False):
    """
    Automates git add, commit, and push with an AI-generated commit message.
    """
    from assistant.ai_brain import query_local_llm_chat
    
    speak("Analyzing your code changes.")
    
    # 1. Add all changes first so we can diff cached properly
    _run_shell("git add .")
    
    # Get diff
    diff_cached, _, _ = _run_shell("git diff --cached")
    
    total_diff = diff_cached
    if not total_diff.strip():
        speak("No changes found to commit.")
        return "No changes to commit."
        
    # Generate commit message
    prompt = f"Write a very concise, professional Git commit message for the following diff. Only output the commit message string, nothing else.\n\n{total_diff[:3000]}"
    
    response = query_local_llm_chat([{"role": "user", "content": prompt}], model=get_setting("llm_model", "qwen2.5:3b"))
    commit_msg = response.get("content", "") if isinstance(response, dict) else str(response)
    
    if not commit_msg:
        commit_msg = "Update project files"
        
    # Clean quotes
    commit_msg = commit_msg.replace('"', '').replace('`', '').strip()
    
    _, err, code = _run_shell(f'git commit -m "{commit_msg}"')
    
    if code == 0:
        speak("Changes committed successfully.")
        res_str = f"Committed: {commit_msg}"
        if push:
            speak("Pushing to remote.")
            _run_shell("git push")
            res_str += " and pushed."
        return res_str
    else:
        speak("Failed to commit changes.")
        return f"Commit failed: {err}"

def scaffold_code(prompt, filename):
    """
    Uses the LLM to generate code based on a prompt and saves it to a file.
    """
    from assistant.ai_brain import query_local_llm_chat
    
    speak(f"Scaffolding code for {filename}.")
    
    system_prompt = "You are an expert developer. The user will ask you to scaffold code. ONLY return the raw code. Do not use markdown blocks like ```python, just return the raw text that can be saved directly to the file."
    
    response = query_local_llm_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ], model=get_setting("llm_model", "qwen2.5:3b"))
    
    code = response.get("content", "") if isinstance(response, dict) else str(response)
    
    if not code:
        speak("I couldn't generate the code.")
        return "Failed to generate code."
        
    try:
        # Strip markdown if the LLM hallucinated it anyway
        if code.startswith("```"):
            lines = code.split("\\n")
            if len(lines) > 2:
                code = "\\n".join(lines[1:-1])
                
        with open(filename, "w", encoding="utf-8") as f:
            f.write(code.strip())
        speak(f"File {filename} has been created.")
        return f"Scaffolded {filename} successfully."
    except Exception as e:
        speak("Error saving the file.")
        return str(e)

def scrape_project_ideas(project_path="."):
    """
    Scans the local project to understand it, then uses the LLM to synthesize open source templates and ideas.
    """
    from assistant.ai_brain import query_local_llm_chat
    import os
    
    speak("Scraping for project ideas.")
    
    # Try to read README or package.json
    context = ""
    readme_path = os.path.join(project_path, "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            context += f.read()[:2000]
            
    package_path = os.path.join(project_path, "package.json")
    if os.path.exists(package_path):
        with open(package_path, "r", encoding="utf-8") as f:
            context += f.read()[:1000]
            
    if not context:
        context = "No documentation found in the current directory. Assume a generic python/web hackathon project."
        
    prompt = f"Based on this project context:\\n{context}\\n\\nAct as a senior architect. Suggest 3 high-impact open-source templates, boilerplates, or architectural references the user should look at to improve this project. Format it nicely."
    
    response = query_local_llm_chat([{"role": "user", "content": prompt}], model=get_setting("llm_model", "qwen2.5:3b"))
    ideas = response.get("content", "") if isinstance(response, dict) else str(response)
    
    if ideas:
        speak("I have found some excellent ideas. Check the chat.")
        return ideas
    else:
        speak("I couldn't generate ideas.")
        return "Failed to scrape ideas."

def deep_test_project(start_command="npm run dev", url="http://localhost:3000"):
    """
    The Autonomous QA Agent. Boots the project, opens chrome, takes vision screenshots, and generates a report.
    """
    from assistant.ai_brain import query_local_llm_chat
    from assistant.vision import analyze_screen
    import pyautogui
    import os
    
    speak("Initiating deep test. Upgrading brain to qwen 3 point 5 9 B.")
    
    # 1. Hot swap model
    original_model = get_setting("llm_model", "qwen2.5:3b")
    update_setting("llm_model", "qwen3.5:9b")
    process = None
    
    try:
        speak(f"Starting server with command: {start_command}")
        
        # 2. Boot server
        process = subprocess.Popen(start_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        time.sleep(8) # Wait for boot
        
        # 3. Open Chrome
        speak("Opening browser to inspect the UI.")
        webbrowser.open(url)
        time.sleep(5)
        
        # 4. Vision Audit
        speak("Analyzing screen for visual bugs.")
        vision_prompt = "You are a strict UI/UX QA Tester. Look at this screen and identify any visual bugs, overflowing text, broken CSS, or visible error messages. List them clearly."
        vision_report = analyze_screen(vision_prompt)
        
        # 5. Read terminal logs
        speak("Checking terminal logs for crashes.")
        logs = ""
        
        import psutil
        try:
            # Gracefully kill all children of the process so we don't orphan Node
            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
        except Exception:
            pass
            
        try:
            logs, _ = process.communicate(timeout=3)
        except Exception:
            logs = "Could not fetch logs."
            
        speak("Compiling deep test report.")
        
        # 6. Synthesize Report
        prompt = f"You are an autonomous QA agent. Synthesize this test data into a 'Deep Test Report'.\\n\\nVisual Audit:\\n{vision_report}\\n\\nTerminal Logs:\\n{logs[-2000:]}"
        
        response = query_local_llm_chat([{"role": "user", "content": prompt}], model="qwen3.5:9b")
        final_report = response.get("content", "") if isinstance(response, dict) else str(response)
        
        with open("deep_test_report.md", "w", encoding="utf-8") as f:
            f.write(final_report if final_report else "Report generation failed.")
            
        speak("Deep test complete. Model reverted. Report saved to deep test report dot m d.")
        return "Deep Test completed successfully. Please read deep_test_report.md for details."
    finally:
        if process and process.poll() is None:
            try:
                import psutil
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    child.terminate()
                parent.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        update_setting("llm_model", original_model)

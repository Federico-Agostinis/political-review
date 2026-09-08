import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Detect Project Root
PROJECT_ROOT = str(Path(__file__).parent.parent)
sys.path.append(PROJECT_ROOT)

# Bootstrap: Ubuntu 24.04+ (PEP 668) blocks system-wide pip installs, so the
# pipeline needs its own virtualenv. If we're not already running inside it,
# create it, install the bare minimum to load .env, then re-exec this script
# under the venv's Python so every subprocess below inherits it via sys.executable.
VENV_DIR = os.path.join(PROJECT_ROOT, ".venv")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")

if os.path.abspath(sys.executable) != os.path.abspath(VENV_PYTHON):
    if not os.path.exists(VENV_PYTHON):
        print(f"[INFO] Creating virtualenv at {VENV_DIR}...")
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True, cwd=PROJECT_ROOT)
        subprocess.run([VENV_PYTHON, "-m", "pip", "install", "python-dotenv"], check=True, cwd=PROJECT_ROOT)
    os.execv(VENV_PYTHON, [VENV_PYTHON, __file__] + sys.argv[1:])

from config import project_settings

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

if not os.environ.get("GEMINI_API_KEY"):
    print("[WARN] GEMINI_API_KEY non impostata (env o .env). L'arricchimento LLM verrà saltato.")

def run_command(command, cwd=PROJECT_ROOT, ignore_errors=False):
    print(f"\n[RUN] {command} (cwd: {cwd})")
    try:
        subprocess.run(command, shell=True, check=True, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed with exit code {e.returncode}")
        if not ignore_errors:
            sys.exit(1)
        return False

def main():
    print("=== Starting Local Pipeline Execution ===")
    print(f"[INFO] Using Python: {sys.executable}")

    # 1. Install Python Dependencies
    print("\n=== Installing Python Dependencies ===")
    run_command(f"{sys.executable} -m pip install -r config/requirements.txt")

    # 2. Documenti istituzionali (Veneto Lavoro, Consiglio comunale di Padova)
    print("\n=== Running Institutional Documents Pipeline ===")
    run_command(f"{sys.executable} scripts/docs_scraper.py", ignore_errors=True)
    run_command(f"{sys.executable} scripts/enrich_docs.py --limit 10", ignore_errors=True)

    # Notifica email dei documenti nuovi. Senza MAIL_TO/SMTP_USER/SMTP_PASSWORD
    # (env o .env) esce senza fare nulla. Attenzione: il registro degli invii
    # locale (data/notified_docs.json del working tree) e' distinto da quello
    # che gira in CI dentro data.tar.gz, quindi con SMTP configurato in locale
    # si possono ricevere documenti gia' notificati dalla pipeline su Actions.
    run_command(f"{sys.executable} scripts/notify_email.py", ignore_errors=True)

    # 3. Git Commit & Push
    print("\n=== Git Operations ===")
    run_command("git add .")
    
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    status = subprocess.run("git diff --staged --quiet", shell=True, cwd=PROJECT_ROOT)
    
    if status.returncode != 0:
        print(f"Changes detected for {project_settings.REGION_NAME}. Committing...")
        run_command(f'git commit -m "Local Pipeline Run: Update {project_settings.REGION_NAME} data {date_str}"')
        print("Pushing...")
        run_command("git push")
        print("\n✅ Pipeline completed and pushed successfully!")
    else:
        print("\n✅ Pipeline completed. No changes to commit.")

if __name__ == "__main__":
    main()


import os
import argparse
import shutil
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Inizializza il progetto per una nuova regione.")
    parser.add_argument("--name", required=True, help="Nome della regione (es. 'Lombardia')")
    parser.add_argument("--user", required=True, help="Tuo username GitHub")
    parser.add_argument("--repo", default="press-review", help="Nome del repository (default: press-review)")
    
    args = parser.parse_args()
    
    new_region = args.name
    new_id = new_region.lower().replace(" ", "_")
    new_user = args.user
    new_repo = args.repo
    
    base_dir = Path(__file__).parent.parent
    config_file = base_dir / "config" / "project_settings.py"
    
    print(f"🚀 Inizializzazione per {new_region}...")
    
    # 1. Aggiorna project_settings.py
    with open(config_file, "r") as f:
        content = f.read()
        
    content = content.replace('REGION_NAME = "Piemonte"', f'REGION_NAME = "{new_region}"')
    content = content.replace('REGION_ID = "piemonte"', f'REGION_ID = "{new_id}"')
    content = content.replace('REPO_NAME = "press-review"', f'REPO_NAME = "{new_repo}"')
    content = content.replace('GH_USERNAME = "fabrizio-gabellini"', f'GH_USERNAME = "{new_user}"')
    
    with open(config_file, "w") as f:
        f.write(content)
        
    # 2. Rinomina CSV
    old_csv = base_dir / "data" / "giornali_piemonte_v2.csv"
    new_csv = base_dir / "data" / f"giornali_{new_id}_v2.csv"
    if old_csv.exists() and not new_csv.exists():
        os.rename(old_csv, new_csv)
        print(f"✅ CSV rinominato in {new_csv.name}")
        
    # 3. Resetta Dati
    for file_name in ["search_index.json", "enriched_index.json"]:
        file_path = base_dir / "data" / file_name
        with open(file_path, "w") as f:
            json.dump([], f)
        print(f"✅ Reset {file_name}")
            
    stats_path = base_dir / "data" / "dashboard_stats.json"
    with open(stats_path, "w") as f:
        json.dump({}, f)
    print(f"✅ Reset dashboard_stats.json")
    
    # 4. Pulisci Reports & Archives
    reports_dir = base_dir / "reports"
    if reports_dir.exists():
        shutil.rmtree(reports_dir)
        os.makedirs(reports_dir)
        print(f"✅ Pulita cartella reports/")
        
    archive_dir = base_dir / "data" / "archive"
    if archive_dir.exists():
        for f in archive_dir.glob("*.json"):
            os.remove(f)
        with open(archive_dir / "index.json", "w") as f:
            json.dump({}, f)
        print(f"✅ Pulito archivio dati")
        
    # 5. Pulisci Dashboard-v2 (v2 compilata)
    v2_dir = base_dir / "v2"
    if v2_dir.exists():
        shutil.rmtree(v2_dir)
        os.makedirs(v2_dir)
        print(f"✅ Pulita cartella dashboard compilata (v2/)")

    # 5. Aggiorna riferimenti HTML (base)
    index_html = base_dir / "index.html"
    if index_html.exists():
        with open(index_html, "r") as f:
            html = f.read()
        html = html.replace("Piemonte", new_region)
        with open(index_html, "w") as f:
            f.write(html)
        print(f"✅ Aggiornata Landing Page (Piemonte -> {new_region})")

    print(f"\n✨ Operazione completata! Ora puoi aggiungere i feed RSS in data/giornali_{new_id}_v2.csv")

if __name__ == "__main__":
    main()

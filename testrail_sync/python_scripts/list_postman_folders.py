
import json
import os

# PowerShell-style hardcoded paths for Newman command
BASE_PATH = "$env:USERPROFILE\\workspace\\testrail_sync\\newman\\"
COLLECTION_FILE = f"{BASE_PATH}BPXAPI.postman_collection.json"
ENVIRONMENT_FILE = f"{BASE_PATH}BPXAPI.postman_environment.json"
REPORT_OUTPUT = f"{BASE_PATH}newman-report.json"

# Resolve project paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_collection(full_path):
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_folders(collection):
    return [item["name"] for item in collection.get("item", []) if "item" in item]

def filter_folders(folders, test_type, happy_keywords, negative_keywords):
    test_type = test_type.lower()
    if test_type == "happy":
        return [f for f in folders if any(k in f.lower() for k in happy_keywords)]
    if test_type == "negative":
        return [f for f in folders if any(k in f.lower() for k in negative_keywords)]
    if test_type == "all":
        return folders
    return []

def generate_full_newman_command(selected_folders):
    base_cmd = f'newman run "{COLLECTION_FILE}" `\n' \
               f'  -e "{ENVIRONMENT_FILE}" `\n' \
               f'  --reporters json,cli `\n' \
               f'  --reporter-json-export "{REPORT_OUTPUT}"'
    for folder in selected_folders:
        base_cmd += f' `\n  --folder "{folder}"'
    return base_cmd

def preview_folders(all_folders, selected_folders):
    print("\n📋 Folder Preview:")
    for folder in all_folders:
        mark = "✅" if folder in selected_folders else "❌"
        print(f"{mark} {folder}")
    print(f"\nTotal folders: {len(all_folders)}")
    print(f"Included in run: {len(selected_folders)}")

def main():
    config = load_config()
    postman_cfg = config.get("postman", {})

    relative_collection_path = postman_cfg.get("collection_path")
    full_collection_path = os.path.join(PROJECT_ROOT, relative_collection_path)

    default_test_type = postman_cfg.get("default_test_type", "all")
    happy_keywords = postman_cfg.get("happy_keywords", ["happy"])
    negative_keywords = postman_cfg.get("negative_keywords", ["negative"])

    collection = load_collection(full_collection_path)
    all_folders = get_folders(collection)

    print("\nWhich type of tests do you want to run?")
    print("1. Happy Path")
    print("2. Negative Test")
    print("3. All")

    option = input(f"Select an option (1/2/3) [default: {default_test_type}]: ").strip()
    test_type = {
        "1": "happy",
        "2": "negative",
        "3": "all"
    }.get(option, default_test_type)

    selected_folders = filter_folders(all_folders, test_type, happy_keywords, negative_keywords)

    if not selected_folders:
        print("⚠️  No folders matched the selected test type.")
        return

    preview_folders(all_folders, selected_folders)

    full_command = generate_full_newman_command(selected_folders)
    print("\n📦 Full Newman Command:\n")
    print(full_command)

if __name__ == "__main__":
    main()

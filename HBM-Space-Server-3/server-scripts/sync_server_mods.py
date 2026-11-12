import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

SERVER_DIR = Path(__file__).resolve().parent.parent
TARGET_MODS = SERVER_DIR / "mods"
BACKUP_MODS = SERVER_DIR / "mods-old"
SOURCE_MODS = Path(r"/home/louis/.local/share/ModrinthApp/profiles/HBM Space 1.1.0/mods/")

EXCLUDED_PATTERNS = (
    "angelica",
    "blur",
    "mousetweaks",
    "armoredarms",
    "soundfilters",
    "shaderfixer",
    "overloadedarmorbar",
    "NEIIntegration",
    "Controlling",
)


def should_exclude(filename: str) -> bool:
    lower = filename.lower()
    return any(pattern in lower for pattern in EXCLUDED_PATTERNS)


def get_mod_names(directory: Path) -> set[str]:
    if not directory.exists():
        return set()

    return {item.name for item in directory.iterdir() if item.name}


def detect_mod_changes() -> tuple[list[str], list[str], list[str]]:
    source_names = {name for name in get_mod_names(SOURCE_MODS) if not should_exclude(name)}
    target_names = {name for name in get_mod_names(TARGET_MODS) if not should_exclude(name)}
    excluded_target = sorted(name for name in get_mod_names(TARGET_MODS) if should_exclude(name))

    added = sorted(source_names - target_names)
    removed = sorted(target_names - source_names)
    return added, removed, excluded_target


def backup_existing_mods():
    if not TARGET_MODS.exists():
        print(f"ℹ️ No existing mods folder at {TARGET_MODS}; nothing to back up.")
        return

    if BACKUP_MODS.exists():
        shutil.rmtree(BACKUP_MODS)

    shutil.copytree(TARGET_MODS, BACKUP_MODS)
    print(f"📦 Backed up existing mods folder to: {BACKUP_MODS}")


def copy_added_mods(added: list[str]):
    if not SOURCE_MODS.exists():
        raise FileNotFoundError(f"Source mods folder not found: {SOURCE_MODS}")

    TARGET_MODS.mkdir(parents=True, exist_ok=True)

    copied = []
    for mod_name in added:
        source_item = SOURCE_MODS / mod_name
        dest = TARGET_MODS / mod_name

        if source_item.is_dir():
            shutil.copytree(source_item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(source_item, dest)

        copied.append(mod_name)

    if copied:
        print("✅ Added new mods:")
        for name in sorted(copied):
            print(f"   - {name}")
    else:
        print("ℹ️ No new mods to add.")


def remove_missing_mods(removed: list[str]):
    if not TARGET_MODS.exists():
        return

    deleted = []
    for mod_name in removed:
        mod_path = TARGET_MODS / mod_name
        if mod_path.exists():
            if mod_path.is_dir():
                shutil.rmtree(mod_path)
            else:
                mod_path.unlink()
            deleted.append(mod_name)

    if deleted:
        print("🧹 Removed missing mods:")
        for name in sorted(deleted):
            print(f"   - {name}")
    else:
        print("ℹ️ No missing mods to remove.")


def remove_excluded_mods():
    removed = []
    if not TARGET_MODS.exists():
        print("ℹ️ No target mods folder present; nothing to clean up.")
        return

    for item in TARGET_MODS.iterdir():
        if should_exclude(item.name):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed.append(item.name)

    if removed:
        print("🧹 Removed excluded client-side mods:")
        for name in sorted(removed):
            print(f"   - {name}")
    else:
        print("ℹ️ No excluded client-side mods were found in the target folder.")


def confirm_sync(added: list[str], removed: list[str], excluded: list[str]) -> bool:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    def format_list(items: list[str]) -> str:
        if not items:
            return "None"
        return "\n  - ".join(["", *sorted(items)])

    details = []
    if added:
        details.append(f"Add:{format_list(added)}")
    if removed:
        details.append(f"Remove:{format_list(removed)}")
    if excluded:
        details.append(f"Delete excluded:{format_list(excluded)}")

    section = "\n\n".join(details) if details else "No changes detected."
    message = (
        "A mod sync is ready to run.\n\n"
        f"{section}\n\n"
        "A backup will be created before changes are made.\n"
        "Do you want to continue?"
    )

    result = messagebox.askyesno("Confirm mod sync", message, parent=root)
    root.destroy()
    return result


def main():
    print("==================================================")
    print(" SERVER MODS SYNC")
    print("==================================================")
    print(f"Source: {SOURCE_MODS}")
    print(f"Target: {TARGET_MODS}")
    print(f"Backup: {BACKUP_MODS}")
    print()

    added, removed, excluded = detect_mod_changes()

    if not added and not removed and not excluded:
        print(f"ℹ️ No mod additions/removals detected for {SOURCE_MODS}; nothing to sync.")
        print()
        print("==================================================")
        print("✅ No changes needed")
        print("==================================================")
        return

    if not confirm_sync(added, removed, excluded):
        print("⚠️ Mod sync cancelled by user.")
        print()
        print("==================================================")
        print("❌ Sync cancelled")
        print("==================================================")
        return

    backup_existing_mods()
    remove_missing_mods(removed)
    copy_added_mods(added)
    remove_excluded_mods()

    print()
    print("==================================================")
    print("✅ Sync complete")
    print("==================================================")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Antigravity Universal Conversation Transplanter & Backup Utility
================================================================
Transplants full trajectory steps, execution metadata, transcripts, and artifacts
from any source conversation into a destination conversation in another workspace,
or exports/imports complete conversations as portable .zip archives across devices.

Supports both Antigravity Desktop App and Antigravity IDE.
Zero Dependencies: Uses Python standard library only.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

TABLES_TO_COPY = [
    "steps",
    "gen_metadata",
    "executor_metadata",
    "parent_references",
    "battle_mode_infos",
]

APP_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity")
IDE_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-ide")


def get_base_dir(is_ide=False):
    """Returns the base directory for Antigravity user data (Desktop App or IDE)."""
    return IDE_DIR if is_ide else APP_DIR


def locate_conversation(cid, prefer_ide=False):
    """
    Finds the directory containing the conversation DB, auto-detecting between
    Desktop App and IDE workspaces.
    """
    primary = IDE_DIR if prefer_ide else APP_DIR
    secondary = APP_DIR if prefer_ide else IDE_DIR

    if os.path.exists(os.path.join(primary, "conversations", f"{cid}.db")):
        return primary, "IDE" if prefer_ide else "Desktop App"
    if os.path.exists(os.path.join(secondary, "conversations", f"{cid}.db")):
        return secondary, "Desktop App" if prefer_ide else "IDE"

    return primary, "IDE" if prefer_ide else "Desktop App"


def get_conversation_paths(cid, is_ide=False, auto_detect=True):
    """Returns standard directory and file paths for a conversation UUID."""
    if auto_detect:
        base_dir, surface = locate_conversation(cid, prefer_ide=is_ide)
    else:
        base_dir = get_base_dir(is_ide=is_ide)
        surface = "IDE" if is_ide else "Desktop App"

    return {
        "db": os.path.join(base_dir, "conversations", f"{cid}.db"),
        "brain": os.path.join(base_dir, "brain", cid),
        "annot": os.path.join(base_dir, "annotations", f"{cid}.pbtxt"),
        "conv_dir": os.path.join(base_dir, "conversations"),
        "brain_dir": os.path.join(base_dir, "brain"),
        "annot_dir": os.path.join(base_dir, "annotations"),
        "base_dir": base_dir,
        "surface": surface,
    }


def sanitize_filename(name):
    """Sanitizes a string so it is safe to use in file paths across Windows, macOS, and Linux."""
    clean = re.sub(r'[\\/*?:"<>|]', "_", name)
    clean = re.sub(r"\s+", "_", clean)
    clean = re.sub(r"_+", "_", clean)
    clean = clean.strip("._- ")
    return clean[:40] or "chat"


def extract_title_from_pbtxt(path):
    """Extracts conversation title from Antigravity protobuf annotation text."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                match = re.search(r'title:\s*"([^"]*)"', f.read())
                if match:
                    return match.group(1)
        except Exception:
            pass
    return None


def update_title_in_pbtxt(path, title):
    """Updates or creates sidebar title in Antigravity .pbtxt annotation file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            updated_content = re.sub(r'title:\s*"[^"]*"', f'title:"{title}"', content)
            if updated_content == content:
                updated_content = f'title:"{title}"  ' + content

            with open(path, "w", encoding="utf-8") as f:
                f.write(updated_content)
            print(f"    - Sidebar title set to: '{title}'")
            return True
        except Exception as e:
            print(f"    [!] Note on title update: {e}")
    else:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f'title:"{title}"\n')
            print(f"    - Sidebar title created as: '{title}'")
            return True
        except Exception as e:
            print(f"    [!] Note on creating title: {e}")
    return False


def get_step_count(db_path):
    """Returns number of rows in steps table, or 0."""
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT count(*) FROM steps").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def transplant_database_tables(src_conn, dst_conn):
    """Copies trajectory rows from source SQLite connection into target SQLite connection."""
    copied_counts = {}
    for table in TABLES_TO_COPY:
        src_table_check = src_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not src_table_check:
            continue

        cols = [c[1] for c in src_conn.execute(f"PRAGMA table_info({table})").fetchall()]
        cols_joined = ", ".join(f'"{col}"' for col in cols)
        placeholders = ", ".join("?" for _ in cols)

        rows = src_conn.execute(f"SELECT {cols_joined} FROM {table}").fetchall()

        dst_conn.execute(f"DELETE FROM {table}")
        dst_conn.executemany(
            f"INSERT INTO {table} ({cols_joined}) VALUES ({placeholders})", rows
        )
        copied_counts[table] = len(rows)
        print(f"    - {table:18}: copied {len(rows):,} rows")

    dst_conn.commit()
    return copied_counts


def transplant_brain_folder(src_brain_dir, dst_brain_dir):
    """Recursively copies brain directory contents (artifacts, plans, transcripts)."""
    if not os.path.exists(src_brain_dir):
        print("    - No brain directory found in source.")
        return 0

    os.makedirs(dst_brain_dir, exist_ok=True)
    # Clear existing items in destination brain so placeholder files don't linger
    for item in os.listdir(dst_brain_dir):
        p = os.path.join(dst_brain_dir, item)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
        except Exception:
            pass

    count = 0
    for item in os.listdir(src_brain_dir):
        s = os.path.join(src_brain_dir, item)
        d = os.path.join(dst_brain_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        count += 1

    print(f"    - Brain contents copied to: {dst_brain_dir} ({count} items)")
    return count


def list_conversations(limit=15, is_ide=False):
    """Lists recent conversations sorted by modification time to help find UUIDs."""
    base_dir = get_base_dir(is_ide=is_ide)
    env_name = "Antigravity IDE" if is_ide else "Antigravity Desktop App"
    conv_dir = os.path.join(base_dir, "conversations")
    annot_dir = os.path.join(base_dir, "annotations")

    if not os.path.exists(conv_dir):
        print(f"[-] Conversations directory not found at: {conv_dir}")
        return

    db_files = [f for f in os.listdir(conv_dir) if f.endswith(".db") and not f.startswith(".")]
    if not db_files:
        print(f"[-] No conversation databases found in {conv_dir}")
        return

    entries = []
    for fname in db_files:
        full_path = os.path.join(conv_dir, fname)
        try:
            mtime = os.path.getmtime(full_path)
            cid = os.path.splitext(fname)[0]
            pbtxt_path = os.path.join(annot_dir, f"{cid}.pbtxt")
            title = extract_title_from_pbtxt(pbtxt_path) or "(Untitled Conversation)"
            entries.append((mtime, cid, title))
        except OSError:
            continue

    entries.sort(key=lambda x: x[0], reverse=True)

    print("=" * 85)
    print(f" Recent {env_name} Conversations (Top {min(limit, len(entries))})")
    print("=" * 85)
    print(f"{'Last Modified':<20} | {'Conversation ID (UUID)':<38} | {'Title'}")
    print("-" * 85)

    for mtime, cid, title in entries[:limit]:
        time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        display_title = (title[:28] + "...") if len(title) > 31 else title
        print(f"{time_str:<20} | {cid:<38} | {display_title}")

    print("=" * 85)
    print("\nNext Steps:")
    print("  - Export to zip:   python transplant_chat.py --export <UUID>")
    print("  - Move on same PC: python transplant_chat.py <SRC_UUID> <DST_UUID> [\"Title\"]")
    print("  - Import from zip: python transplant_chat.py --import <ARCHIVE.zip> <DST_UUID>")
    if is_ide:
        print("  - Switch target:   Omit '--ide' to view Desktop App conversations.\n")
    else:
        print("  - Switch target:   Add '--ide' to view Antigravity IDE conversations.\n")


def export_chat(src_id, output_path=None, is_ide=False):
    """Exports a conversation into a portable .zip archive containing DB, brain, and manifest."""
    src = get_conversation_paths(src_id, is_ide=is_ide, auto_detect=True)

    if not os.path.exists(src["db"]):
        sys.exit(
            f"[-] Source conversation DB not found: {src['db']}\n"
            f"    Run 'python transplant_chat.py --list' to find valid conversation UUIDs."
        )

    title = extract_title_from_pbtxt(src["annot"]) or "(Untitled Conversation)"
    step_count = get_step_count(src["db"])

    # Determine destination zip path
    if not output_path:
        clean_title = sanitize_filename(title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"antigravity_chat_{clean_title}_{src_id[:8]}_{timestamp}.zip"
    elif os.path.isdir(output_path):
        clean_title = sanitize_filename(title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(
            output_path, f"antigravity_chat_{clean_title}_{src_id[:8]}_{timestamp}.zip"
        )
    elif not output_path.lower().endswith(".zip"):
        output_path += ".zip"

    output_abs_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_abs_path) or ".", exist_ok=True)

    print("=" * 65)
    print(" Antigravity Chat Exporter (.zip)")
    print("=" * 65)
    print(f" Source ID    : {src_id} [{src['surface']}]")
    print(f" Chat Title   : {title}")
    print(f" Total Steps  : {step_count:,}")
    print(f" Target Zip   : {output_abs_path}")
    print("=" * 65)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Snapshot SQLite DB using sqlite3.backup to ensure transactional consistency
        temp_db = os.path.join(temp_dir, "conversation.db")
        print("\n[+] Taking snapshot of conversation database...")
        src_conn = sqlite3.connect(src["db"])
        temp_conn = sqlite3.connect(temp_db)
        src_conn.backup(temp_conn)
        src_conn.close()
        temp_conn.close()

        # Count brain files
        brain_count = 0
        if os.path.exists(src["brain"]):
            for root, dirs, files in os.walk(src["brain"]):
                brain_count += len(files)

        manifest = {
            "schema_version": 1,
            "tool": "antigravity-move-chat-to-projects",
            "exported_at": datetime.now().isoformat(),
            "source_id": src_id,
            "surface": src["surface"],
            "title": title,
            "step_count": step_count,
            "brain_files_count": brain_count,
        }

        print("[+] Compressing database, brain artifacts, and logs into zip...")
        with zipfile.ZipFile(output_abs_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.write(temp_db, arcname="conversation.db")

            if os.path.exists(src["annot"]):
                zf.write(src["annot"], arcname="annotations.pbtxt")

            if os.path.exists(src["brain"]):
                for root, dirs, files in os.walk(src["brain"]):
                    for file in files:
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, src["brain"])
                        arcname = os.path.join("brain", rel_path).replace("\\", "/")
                        zf.write(full_path, arcname=arcname)

    zip_size_mb = os.path.getsize(output_abs_path) / (1024 * 1024)
    print(f"[+] Export successful! Archive size: {zip_size_mb:.2f} MB")
    print(f"[+] Saved to: {output_abs_path}")
    print("\nNext Steps on your Office/Target Computer:")
    print("  1. Copy this .zip file to your target computer.")
    print("  2. In Antigravity (or IDE) on that computer, create a new chat in your target project and say 'hi'.")
    print("  3. Close the application.")
    print(f'  4. Run: python transplant_chat.py --import "{os.path.basename(output_abs_path)}" <TARGET_UUID>\n')


def inspect_archive(archive_path):
    """Displays metadata and contents of an exported .zip archive without extracting."""
    if not os.path.exists(archive_path):
        sys.exit(f"[-] Archive file not found: {archive_path}")
    if not zipfile.is_zipfile(archive_path):
        sys.exit(f"[-] Not a valid zip archive: {archive_path}")

    size_mb = os.path.getsize(archive_path) / (1024 * 1024)
    manifest = None

    with zipfile.ZipFile(archive_path, "r") as zf:
        namelist = zf.namelist()
        if "manifest.json" in namelist:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception:
                pass

        brain_files = [n for n in namelist if n.startswith("brain/") and not n.endswith("/")]

    print("=" * 65)
    print(" Antigravity Chat Archive Info")
    print("=" * 65)
    print(f" Archive File   : {os.path.abspath(archive_path)} ({size_mb:.2f} MB)")
    if manifest:
        print(f" Chat Title     : {manifest.get('title', '(Untitled)')}")
        print(f" Source Surface : {manifest.get('surface', 'Antigravity')}")
        print(f" Original UUID  : {manifest.get('source_id', 'Unknown')}")
        print(f" Exported At    : {manifest.get('exported_at', 'Unknown')}")
        print(f" Total Steps    : {manifest.get('step_count', 0):,}")
        print(f" Brain Files    : {len(brain_files)} artifacts & logs")
    else:
        print(f" Contains DB    : {'conversation.db' in namelist}")
        print(f" Brain Files    : {len(brain_files)} files")
    print("=" * 65)
    print("\nTo import this chat into an Antigravity workspace:")
    print("  1. In target workspace, create a chat and send 'hi' to initialize bindings.")
    print("  2. Close Antigravity (or Antigravity IDE).")
    print(f'  3. Run: python transplant_chat.py --import "{archive_path}" <TARGET_UUID>\n')


def import_chat(archive_path, dst_id, new_title=None, is_ide=False):
    """Imports an exported .zip archive into a target conversation on the current machine."""
    if not os.path.exists(archive_path):
        sys.exit(f"[-] Archive file not found: {archive_path}")
    if not zipfile.is_zipfile(archive_path):
        sys.exit(f"[-] Not a valid zip archive: {archive_path}")

    dst = get_conversation_paths(dst_id, is_ide=is_ide, auto_detect=True)

    if not os.path.exists(dst["db"]):
        sys.exit(
            f"[-] Target conversation DB not found: {dst['db']}\n"
            f"[!] Rule: Open your target workspace in {dst['surface']} and send a 1-word message (like 'hi') first so Antigravity creates this database!"
        )

    print("=" * 65)
    print(" Antigravity Chat Importer")
    print("=" * 65)
    print(f" Archive File   : {archive_path}")
    print(f" Target Chat ID : {dst_id} [{dst['surface']}]")
    print("=" * 65)

    # 1. Target Safety Backups
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_db = os.path.join(dst["conv_dir"], f"{dst_id}.db.backup_{timestamp}")
    backup_brain = os.path.join(dst["brain_dir"], f"{dst_id}_backup_{timestamp}")

    print(f"[+] Creating safety backup of target DB -> {os.path.basename(backup_db)}")
    shutil.copy2(dst["db"], backup_db)

    if os.path.exists(dst["brain"]):
        print(f"[+] Creating safety backup of target Brain -> {os.path.basename(backup_brain)}")
        shutil.copytree(dst["brain"], backup_brain)

    with tempfile.TemporaryDirectory() as temp_dir:
        print("\n[+] Extracting archive safely...")
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Zip Slip security check: ensure all filepaths extract inside temp_dir
            for member in zf.infolist():
                target_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not target_path.startswith(os.path.abspath(temp_dir)):
                    sys.exit(f"[-] Security error: archive contains unsafe relative path: {member.filename}")
            zf.extractall(temp_dir)

        manifest = None
        manifest_path = os.path.join(temp_dir, "manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                pass

        extracted_db = os.path.join(temp_dir, "conversation.db")
        if not os.path.exists(extracted_db):
            sys.exit("[-] Archive does not contain 'conversation.db'. Invalid backup.")

        # 2. Transplant SQLite tables
        print("\n[+] Transplanting trajectory tables in SQLite...")
        src_conn = sqlite3.connect(extracted_db)
        dst_conn = sqlite3.connect(dst["db"])
        transplant_database_tables(src_conn, dst_conn)
        src_conn.close()
        dst_conn.close()
        print("[+] Database steps & execution metadata transplanted successfully!")

        # 3. Transplant Brain folder
        print("\n[+] Copying artifacts, plans, and transcript logs...")
        extracted_brain = os.path.join(temp_dir, "brain")
        if os.path.exists(extracted_brain):
            transplant_brain_folder(extracted_brain, dst["brain"])
        else:
            print("    [!] Note: No brain directory found in archive.")

        # 4. Annotation / Sidebar Title Update
        extracted_annot = os.path.join(temp_dir, "annotations.pbtxt")
        title_to_set = (
            new_title
            or (manifest and manifest.get("title"))
            or extract_title_from_pbtxt(extracted_annot)
            or "Imported Conversation"
        )
        print(f"\n[+] Updating conversation title in sidebar...")
        update_title_in_pbtxt(dst["annot"], title_to_set)

    print("\n" + "=" * 65)
    print(" IMPORT COMPLETED SUCCESSFULLY!")
    print("=" * 65)
    print(f" Chat '{title_to_set}' is now bound to {dst['surface']} conversation: {dst_id}")
    print(f" You can now reopen {dst['surface']} and continue working!")


def transplant(src_id, dst_id, new_title=None, is_ide=False):
    """Transplants conversation directly between two local chats on the same machine."""
    src = get_conversation_paths(src_id, is_ide=is_ide, auto_detect=True)
    dst = get_conversation_paths(dst_id, is_ide=is_ide, auto_detect=True)

    print("=" * 65)
    print(" Antigravity Universal Conversation Transplanter")
    print("=" * 65)
    print(f" Source Conversation ID : {src_id} [{src['surface']}]")
    print(f" Target Conversation ID : {dst_id} [{dst['surface']}]")
    if src["surface"] != dst["surface"]:
        print(f" Cross-App Transplant   : {src['surface']} -> {dst['surface']}")
    print("=" * 65)

    # 1. Verify existence
    if not os.path.exists(src["db"]):
        sys.exit(f"[-] Source DB not found: {src['db']}")
    if not os.path.exists(dst["db"]):
        sys.exit(
            f"[-] Target DB not found: {dst['db']}\n"
            f"[!] Rule: Open your target workspace in {dst['surface']} and send a 1-word message (like 'hi') first so Antigravity creates this database!"
        )

    # 2. Safety Backups
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_db = os.path.join(dst["conv_dir"], f"{dst_id}.db.backup_{timestamp}")
    backup_brain = os.path.join(dst["brain_dir"], f"{dst_id}_backup_{timestamp}")

    print(f"[+] Creating safety backup of target DB -> {os.path.basename(backup_db)}")
    shutil.copy2(dst["db"], backup_db)

    if os.path.exists(dst["brain"]):
        print(f"[+] Creating safety backup of target Brain -> {os.path.basename(backup_brain)}")
        shutil.copytree(dst["brain"], backup_brain)

    # 3. Database Transplant
    print("\n[+] Transplanting trajectory tables in SQLite...")
    src_conn = sqlite3.connect(src["db"])
    dst_conn = sqlite3.connect(dst["db"])
    transplant_database_tables(src_conn, dst_conn)
    src_conn.close()
    dst_conn.close()
    print("[+] Database steps & execution metadata transplanted successfully!")

    # 4. Brain & Artifacts Transplant
    print("\n[+] Copying artifacts, plans, and transcript logs...")
    transplant_brain_folder(src["brain"], dst["brain"])

    # 5. Annotation / Sidebar Title Update
    title_to_set = (
        new_title
        or extract_title_from_pbtxt(src["annot"])
        or "Transplanted Conversation"
    )
    print(f"\n[+] Updating conversation title in sidebar...")
    update_title_in_pbtxt(dst["annot"], title_to_set)

    print("\n" + "=" * 65)
    print(" TRANSPLANT COMPLETED SUCCESSFULLY!")
    print("=" * 65)
    print(f" Target conversation {dst_id} [{dst['surface']}] has been updated with full history.")
    print(f" You can now reopen {dst['surface']}!")


def main():
    parser = argparse.ArgumentParser(
        description="Antigravity Universal Conversation Transplanter & Backup Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List recent conversations:
  python transplant_chat.py --list
  python transplant_chat.py --ide --list

  # Export a chat to a portable .zip backup (for moving to another PC):
  python transplant_chat.py --export <SRC_ID>
  python transplant_chat.py --export <SRC_ID> my_backup.zip

  # Import a .zip chat backup into a placeholder chat on another PC:
  python transplant_chat.py --import my_backup.zip <DST_ID>
  python transplant_chat.py --import my_backup.zip <DST_ID> "Custom Title"

  # Inspect a .zip chat backup:
  python transplant_chat.py --info my_backup.zip

  # Transplant directly between two chats on the same PC (Desktop or IDE):
  python transplant_chat.py <SRC_ID> <DST_ID> ["Optional Custom Title"]
""",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List recent conversations with UUIDs, titles, and timestamps",
    )
    parser.add_argument(
        "--ide",
        action="store_true",
        help="Target Antigravity IDE workspaces (~/.gemini/antigravity-ide) instead of desktop app",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Maximum conversations to display with --list (default: 15)",
    )
    parser.add_argument(
        "-e",
        "--export",
        nargs="+",
        metavar=("SRC_ID", "OUTPUT_ZIP"),
        help="Export conversation to a portable .zip backup file",
    )
    parser.add_argument(
        "-i",
        "--import",
        dest="import_args",
        nargs="+",
        metavar=("ARCHIVE", "DST_ID"),
        help="Import .zip backup into target placeholder chat",
    )
    parser.add_argument(
        "--info",
        metavar="ARCHIVE_ZIP",
        help="Inspect a .zip chat backup without extracting",
    )
    parser.add_argument(
        "pos_args",
        nargs="*",
        help="Positional arguments for transplant, export, or import shortcuts",
    )

    args = parser.parse_args()

    # 1. Handle --list
    if args.list:
        list_conversations(limit=args.limit, is_ide=args.ide)
        return

    # 2. Handle --info
    if args.info:
        inspect_archive(args.info)
        return

    # 3. Handle --export
    if args.export:
        src_id = args.export[0]
        out_zip = args.export[1] if len(args.export) > 1 else None
        export_chat(src_id, out_zip, is_ide=args.ide)
        return

    # 4. Handle --import
    if args.import_args:
        archive = args.import_args[0]
        if len(args.import_args) < 2:
            print("[!] Error: Destination conversation UUID is required for --import.")
            print("    Usage: python transplant_chat.py --import <ARCHIVE.zip> <DST_ID> [\"Title\"]")
            sys.exit(1)
        dst_id = args.import_args[1]
        title = args.import_args[2] if len(args.import_args) > 2 else None
        import_chat(archive, dst_id, title, is_ide=args.ide)
        return

    # 5. Handle positional arguments
    if args.pos_args:
        first_arg = args.pos_args[0]
        # Check if first argument is a zip file
        is_zip = first_arg.lower().endswith(".zip") or zipfile.is_zipfile(first_arg)

        if is_zip:
            if len(args.pos_args) == 1:
                # e.g. python transplant_chat.py backup.zip -> show info
                inspect_archive(first_arg)
                return
            else:
                # e.g. python transplant_chat.py backup.zip <DST_ID> [title] -> import
                dst_id = args.pos_args[1]
                title = args.pos_args[2] if len(args.pos_args) > 2 else None
                import_chat(first_arg, dst_id, title, is_ide=args.ide)
                return
        else:
            # Direct transplant between two UUIDs
            if len(args.pos_args) >= 2:
                src_id = args.pos_args[0]
                dst_id = args.pos_args[1]
                title = args.pos_args[2] if len(args.pos_args) > 2 else None
                transplant(src_id, dst_id, title, is_ide=args.ide)
                return
            elif len(args.pos_args) == 1:
                # Only 1 argument provided and it's not a zip: check if it's a valid source ID
                src_paths = get_conversation_paths(first_arg, is_ide=args.ide, auto_detect=True)
                if os.path.exists(src_paths["db"]):
                    print(f"[!] Only one conversation ID provided: {first_arg} [{src_paths['surface']}]")
                    print("    Did you want to export it? Running export:")
                    export_chat(first_arg, is_ide=args.ide)
                    return
                else:
                    parser.print_help()
                    sys.exit(1)

    # 6. No arguments provided
    parser.print_help()
    print("\n[!] No action specified. Run 'python transplant_chat.py --list' to view recent chats.")
    sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Antigravity Universal Conversation Transplanter
================================================
Transplants full trajectory steps, execution metadata, transcripts, and artifacts
from any source conversation into a destination conversation in another workspace,
preserving the destination's native Protobuf workspace bindings and hashes.

Usage:
  1. In your target workspace, open a new chat and send a 1-word message (e.g. "hi").
  2. Close Antigravity completely.
  3. Run via CLI:
       python transplant_chat.py --list
       python transplant_chat.py <SRC_ID> <DST_ID> ["Optional New Title"]
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime


def get_base_dir():
    """Returns the platform-independent base directory for Antigravity user data."""
    return os.path.join(os.path.expanduser("~"), ".gemini", "antigravity")


def get_paths(src_id, dst_id):
    base_dir = get_base_dir()
    return {
        "src_db": os.path.join(base_dir, "conversations", f"{src_id}.db"),
        "dst_db": os.path.join(base_dir, "conversations", f"{dst_id}.db"),
        "src_brain": os.path.join(base_dir, "brain", src_id),
        "dst_brain": os.path.join(base_dir, "brain", dst_id),
        "src_annot": os.path.join(base_dir, "annotations", f"{src_id}.pbtxt"),
        "dst_annot": os.path.join(base_dir, "annotations", f"{dst_id}.pbtxt"),
        "conv_dir": os.path.join(base_dir, "conversations"),
        "brain_dir": os.path.join(base_dir, "brain"),
    }


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


def list_conversations(limit=15):
    """Lists recent conversations sorted by modification time to help find UUIDs."""
    base_dir = get_base_dir()
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
    print(f" Recent Antigravity Conversations (Top {min(limit, len(entries))})")
    print("=" * 85)
    print(f"{'Last Modified':<20} | {'Conversation ID (UUID)':<38} | {'Title'}")
    print("-" * 85)

    for mtime, cid, title in entries[:limit]:
        time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        display_title = (title[:28] + "...") if len(title) > 31 else title
        print(f"{time_str:<20} | {cid:<38} | {display_title}")

    print("=" * 85)
    print("\nNext Steps:")
    print("  1. Identify your Source conversation UUID (the chat to move)")
    print("  2. Identify your Target conversation UUID (the placeholder chat in the new workspace)")
    print('  3. Run: python transplant_chat.py <SRC_ID> <DST_ID> ["Optional Custom Title"]\n')


def transplant(src_id, dst_id, new_title=None):
    paths = get_paths(src_id, dst_id)

    print("=" * 65)
    print(" Antigravity Universal Conversation Transplanter")
    print("=" * 65)
    print(f" Source Conversation ID : {src_id}")
    print(f" Target Conversation ID : {dst_id}")
    print("=" * 65)

    # 1. Verify existence
    if not os.path.exists(paths["src_db"]):
        sys.exit(f"[-] Source DB not found: {paths['src_db']}")
    if not os.path.exists(paths["dst_db"]):
        sys.exit(
            f"[-] Target DB not found: {paths['dst_db']}\n"
            f"[!] Rule: Open your target workspace in Antigravity and send a 1-word message (like 'hi') first so Antigravity creates this database!"
        )

    # 2. Safety Backups
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_db = os.path.join(paths["conv_dir"], f"{dst_id}.db.backup_{timestamp}")
    backup_brain = os.path.join(paths["brain_dir"], f"{dst_id}_backup_{timestamp}")

    print(f"[+] Creating safety backup of target DB -> {os.path.basename(backup_db)}")
    shutil.copy2(paths["dst_db"], backup_db)

    if os.path.exists(paths["dst_brain"]):
        print(f"[+] Creating safety backup of target Brain -> {os.path.basename(backup_brain)}")
        shutil.copytree(paths["dst_brain"], backup_brain)

    # 3. Database Transplant
    print("\n[+] Transplanting trajectory tables in SQLite...")
    src_conn = sqlite3.connect(paths["src_db"])
    dst_conn = sqlite3.connect(paths["dst_db"])

    tables_to_copy = [
        "steps",
        "gen_metadata",
        "executor_metadata",
        "parent_references",
        "battle_mode_infos",
    ]

    for table in tables_to_copy:
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
        print(f"    - {table:18}: copied {len(rows)} rows")

    dst_conn.commit()
    src_conn.close()
    dst_conn.close()
    print("[+] Database steps & execution metadata transplanted successfully!")

    # 4. Brain & Artifacts Transplant
    print("\n[+] Copying artifacts, plans, and transcript logs...")
    if os.path.exists(paths["src_brain"]):
        os.makedirs(paths["dst_brain"], exist_ok=True)
        for item in os.listdir(paths["src_brain"]):
            s = os.path.join(paths["src_brain"], item)
            d = os.path.join(paths["dst_brain"], item)
            if os.path.isdir(s):
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        print(f"    - Full brain contents copied to: {paths['dst_brain']}")

    # 5. Annotation / Sidebar Title Update
    title_to_set = new_title or extract_title_from_pbtxt(paths["src_annot"]) or "Transplanted Conversation"
    if os.path.exists(paths["dst_annot"]):
        print(f"\n[+] Updating conversation title in sidebar...")
        try:
            with open(paths["dst_annot"], "r", encoding="utf-8") as f:
                content = f.read()

            updated_content = re.sub(r'title:\s*"[^"]*"', f'title:"{title_to_set}"', content)
            if updated_content == content:
                updated_content = f'title:"{title_to_set}"  ' + content

            with open(paths["dst_annot"], "w", encoding="utf-8") as f:
                f.write(updated_content)
            print(f"    - Sidebar title set to: '{title_to_set}'")
        except Exception as e:
            print(f"    [!] Note on title update: {e}")

    print("\n" + "=" * 65)
    print(" TRANSPLANT COMPLETED SUCCESSFULLY!")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(
        description="Transplant any Antigravity conversation into another workspace.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List recent conversations to find IDs:
  python transplant_chat.py --list

  # Move conversation to target workspace:
  python transplant_chat.py 7b7c0bee-****-****-****-************ 21e82bc8-****-****-****-************

  # Move conversation and specify a custom title:
  python transplant_chat.py 7b7c0bee-****-****-****-************ 21e82bc8-****-****-****-************ "Refactored Auth Flow"
""",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List recent conversations with UUIDs, titles, and timestamps",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Maximum conversations to display with --list (default: 15)",
    )
    parser.add_argument(
        "src",
        nargs="?",
        help="Source conversation UUID (the chat you want to move)",
    )
    parser.add_argument(
        "dst",
        nargs="?",
        help="Destination conversation UUID (the placeholder chat in target workspace)",
    )
    parser.add_argument(
        "title",
        nargs="?",
        default=None,
        help="Optional title to display in sidebar (defaults to source conversation title)",
    )

    args = parser.parse_args()

    if args.list:
        list_conversations(limit=args.limit)
        return

    if not args.src or not args.dst:
        parser.print_help()
        print("\n[!] Error: Both <src> and <dst> conversation UUIDs are required.")
        print("    Run 'python transplant_chat.py --list' to find your conversation UUIDs.")
        sys.exit(1)

    transplant(args.src, args.dst, args.title)


if __name__ == "__main__":
    main()

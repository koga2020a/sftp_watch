import argparse
import yaml
import configparser
import base64
import os
import time
import sys
import socket
import json
import csv
from paramiko import Transport, SFTPClient, RSAKey
from stat import S_ISDIR
from collections import defaultdict
from datetime import datetime

# カラー出力（Git Bash 用）
RESET = '\033[0m'
BLUE = '\033[94m'
WHITE = '\033[97m'
CYAN = '\033[96m'  # 水色
ORANGE = '\033[93m'  # オレンジ色

def parse_args():
    parser = argparse.ArgumentParser(description='SFTP Directory Monitor with tree, color, logging')
    parser.add_argument('--config', type=str, help='Path to config file (yaml or ini)')
    return parser.parse_args()

def load_config(path):
    if path.endswith(('.yaml', '.yml')):
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    elif path.endswith('.ini'):
        cfg = configparser.ConfigParser()
        cfg.read(path, encoding='utf-8')
        return {s: dict(cfg.items(s)) for s in cfg.sections()}
    else:
        raise ValueError('Unsupported config format')

def create_proxy_socket(proxy_host, proxy_port, target_host, target_port):
    sock = socket.create_connection((proxy_host, proxy_port))
    req = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n\r\n"
    sock.sendall(req.encode())
    resp = b''
    while b'\r\n\r\n' not in resp:
        chunk = sock.recv(4096)
        if not chunk: break
        resp += chunk
    if b'200 Connection established' not in resp:
        raise ConnectionError(f"Proxy CONNECT failed: {resp!r}")
    return sock

def connect_sftp(cfg):
    use_proxy = cfg.get('proxy_host') and cfg.get('proxy_port')
    if use_proxy:
        print(f"[INFO] Connecting via proxy {cfg['proxy_host']}:{cfg['proxy_port']}")
        sock = create_proxy_socket(cfg['proxy_host'], int(cfg['proxy_port']), cfg['host'], int(cfg['port']))
        transport = Transport(sock)
    else:
        print(f"[INFO] Connecting directly to {cfg['host']}:{cfg['port']}")
        transport = Transport((cfg['host'], int(cfg['port'])))

    if cfg['auth_type'] == 'pkey':
        key_data = base64.b64decode(cfg['pkey_base64'])
        with open('tmp_key.pem', 'wb') as f:
            f.write(key_data)
        pkey = RSAKey.from_private_key_file('tmp_key.pem')
        os.remove('tmp_key.pem')
        transport.connect(username=cfg['user'], pkey=pkey)
    else:
        transport.connect(username=cfg['user'], password=cfg['password'])

    return SFTPClient.from_transport(transport)

def list_files_recursive(sftp, directory):
    entries = {}
    try:
        for entry in sftp.listdir_attr(directory):
            name = entry.filename
            if name.startswith('.'):
                continue
            path = os.path.join(directory, name).replace('\\', '/')
            if S_ISDIR(entry.st_mode):
                entries[path + '/'] = {
                    'is_dir': True
                }
                entries.update(list_files_recursive(sftp, path))
            else:
                entries[path] = {
                    'size': entry.st_size,
                    'mtime': entry.st_mtime,
                    'is_dir': False
                }
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[ERROR] Listing failed in {directory}: {e}")
    return entries

def print_tree(entries):
    tree = defaultdict(list)
    for p in sorted(entries):
        parts = p.strip('/').split('/')
        parent = '/'.join(parts[:-1])
        tree[parent].append(p)

    def _recurse(parent, level):
        for p in tree.get(parent, []):
            indent = '  ' * level
            name = p.rstrip('/').split('/')[-1]
            is_dir = entries[p]['is_dir']
            color = BLUE if is_dir else WHITE
            size = entries[p].get('size')
            size_str = '' if is_dir else f' size={size:,}'
            print(f"{indent}{color}{name}{'/' if is_dir else ''}{size_str}{RESET}")
            if is_dir:
                _recurse(p.strip('/'), level+1)

    _recurse('', 0)

def write_logs_csv(changes):
    with open('log.csv', 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        for row in changes:
            w.writerow(row)

def write_display_log(timestamp, messages):
    with open('display_log.txt', 'a', encoding='utf-8') as f:
        f.write(f"\n------------------\n")
        f.write(f"-----  {timestamp}  変更検知  -----\n")
        for msg in messages:
            f.write(f"{msg}\n")
        f.write("\n")

def write_logs_json(state):
    with open('log.json', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def get_dir_paths(path_dict):
    """
    Extract only directory paths from the entries dictionary
    """
    return {p.rstrip('/'): True for p in path_dict if path_dict[p]['is_dir']}

def main():
    args = parse_args()
    cfg_path = args.config or 'config.yaml'
    if not os.path.exists(cfg_path):
        print(f"[ERROR] Config not found: {cfg_path}")
        sys.exit(1)
    cfg = load_config(cfg_path)
    if isinstance(cfg, dict) and 'default' in cfg:
        cfg = cfg['default']

    for k in ('host','port','user','auth_type','dirs'):
        if k not in cfg:
            print(f"[ERROR] Missing config: {k}")
            sys.exit(1)
    cfg['port'] = int(cfg.get('port',22))
    cfg['interval'] = int(cfg.get('interval',60))
    if isinstance(cfg['dirs'], str):
        cfg['dirs'] = [d.strip() for d in cfg['dirs'].split(',')]

    print("[INFO] Connecting to SFTP...")
    print(f"[INFO] host         = {cfg['host']}")
    print(f"[INFO] port         = {cfg['port']}")
    print(f"[INFO] user         = {cfg['user']}")
    print(f"[INFO] password     = {'*'*8 if cfg.get('password') else '(none)'}")
    print(f"[INFO] auth_type    = {cfg['auth_type']}")
    print(f"[INFO] proxy_host   = {cfg.get('proxy_host','(none)')}")
    print(f"[INFO] proxy_port   = {cfg.get('proxy_port','(none)')}")
    print(f"[INFO] dirs         = {cfg['dirs']}")
    print(f"[INFO] interval     = {cfg['interval']}")
    if cfg['auth_type']=='pkey':
        l = len(cfg.get('pkey_base64',''))
        print(f"[INFO] pkey_base64 = {'*'*10}... ({l} chars)")

    sftp = connect_sftp(cfg)
    print(f"[INFO] Monitoring recursively: {cfg['dirs']} every {cfg['interval']}s")

    prev = {}
    prev_dirs = {}  # Store directory paths separately
    first = True

    try:
        while True:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            current = {}
            for d in cfg['dirs']:
                current.update(list_files_recursive(sftp, d))

            # Extract directory paths from current entries
            current_dirs = get_dir_paths(current)

            if first:
                print(f"[INFO] Initial scan complete. {len(current)} entries:")
                print_tree(current)
                print("-"*40)
            else:
                # Process regular file changes
                file_entries = {p: v for p, v in current.items() if not v['is_dir']}
                prev_file_entries = {p: v for p, v in prev.items() if not v['is_dir']}
                
                added_files = set(file_entries) - set(prev_file_entries)
                removed_files = set(prev_file_entries) - set(file_entries)
                
                # Find files with same name and size but different modification time
                common_files = set(file_entries) & set(prev_file_entries)
                updated = set()
                date_only_changed = set()
                
                for p in common_files:
                    if file_entries[p]['size'] != prev_file_entries[p]['size']:
                        updated.add(p)
                    elif file_entries[p]['mtime'] != prev_file_entries[p]['mtime']:
                        date_only_changed.add(p)
                
                # Process directory changes - only report when a directory is actually added or removed
                added_dirs = set(current_dirs) - set(prev_dirs)
                removed_dirs = set(prev_dirs) - set(current_dirs)
                
                if added_files or removed_files or updated or date_only_changed or added_dirs or removed_dirs:
                    print("\n------------------")
                    print(f"-----  {now}  変更検知  -----")

                    changes = []
                    display_messages = []
                    
                    # Report added directories (only genuinely new ones)
                    for p in sorted(added_dirs):
                        msg = f"{BLUE}[ADD] {p}/ (directory){RESET}"
                        print(msg)
                        display_messages.append(msg.replace(BLUE, '').replace(RESET, ''))
                        changes.append([now, 'ADD_DIR', p + '/'])
                    
                    # Report removed directories (only genuinely removed ones)
                    for p in sorted(removed_dirs):
                        msg = f"{BLUE}[DEL] {p}/ (directory){RESET}"
                        print(msg)
                        display_messages.append(msg.replace(BLUE, '').replace(RESET, ''))
                        changes.append([now, 'DEL_DIR', p + '/'])
                    
                    # Report added files
                    for p in sorted(added_files):
                        m = current[p]
                        msg = f"{CYAN}[ADD] {p} size={m['size']:,}{RESET}"
                        print(msg)
                        display_messages.append(msg.replace(CYAN, '').replace(RESET, ''))
                        changes.append([now, 'ADD', p, m['size']])
                    
                    # Report removed files
                    for p in sorted(removed_files):
                        m = prev[p]
                        msg = f"{CYAN}[DEL] {p} was size={m['size']:,}{RESET}"
                        print(msg)
                        display_messages.append(msg.replace(CYAN, '').replace(RESET, ''))
                        changes.append([now, 'DEL', p, m['size']])
                    
                    # Report modified files
                    for p in sorted(updated):
                        msg = f"{ORANGE}[MOD] {p} {prev[p]['size']:,} -> {current[p]['size']:,}{RESET}"
                        print(msg)
                        display_messages.append(msg.replace(ORANGE, '').replace(RESET, ''))
                        changes.append([now, 'MOD', p, prev[p]['size'], current[p]['size']])
                        
                    # Report files with only date changes
                    for p in sorted(date_only_changed):
                        old_time = datetime.fromtimestamp(prev[p]['mtime']).strftime('%Y-%m-%d %H:%M:%S')
                        new_time = datetime.fromtimestamp(current[p]['mtime']).strftime('%Y-%m-%d %H:%M:%S')
                        file_size = current[p]['size']
                        msg = f"{ORANGE}[DATEonly] {p} size={file_size:,} {old_time} -> {new_time}{RESET}"
                        print(msg)
                        display_messages.append(msg.replace(ORANGE, '').replace(RESET, ''))
                        changes.append([now, 'DATE', p, file_size, old_time, new_time])

                    # Write both CSV and display logs
                    write_logs_csv(changes)
                    write_display_log(now, display_messages)

                    write_logs_csv(changes)

            write_logs_json(current)
            prev = current
            prev_dirs = current_dirs
            first = False
            time.sleep(cfg['interval'])

    finally:
        sftp.close()

if __name__ == '__main__':
    main()
